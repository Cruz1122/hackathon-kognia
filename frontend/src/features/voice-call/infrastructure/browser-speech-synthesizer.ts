import { BackendError, backendErrorFromResponse } from './backend-error';

export class BrowserSpeechSynthesizer {
  private voices: SpeechSynthesisVoice[] = [];
  private speaking = false;
  private generation = 0;
  private fallbackRequest?: AbortController;
  private fallbackAudio?: HTMLAudioElement;
  private fallbackObjectUrl?: string;
  private fallbackContext?: AudioContext;
  private fallbackSources: AudioBufferSourceNode[] = [];

  constructor(private readonly lang = 'es-CO', private readonly fallbackUrl?: string) {
    this.refreshVoices();
    window.speechSynthesis?.addEventListener('voiceschanged', () => this.refreshVoices());
  }

  get available(): boolean {
    return 'speechSynthesis' in window && 'SpeechSynthesisUtterance' in window;
  }

  get voiceOptions(): SpeechSynthesisVoice[] {
    return this.voices;
  }

  speak(text: string, onStart: () => void, onEnd: () => void, voiceName?: string, onError?: (message: string) => void): void {
    if (this.fallbackUrl) {
      this.cancel();
      const generation = ++this.generation;
      void this.speakFallbackStream(text, onStart, onEnd, onError, generation);
      return;
    }
    if (!this.available) {
      onError?.('Este navegador no tiene síntesis de voz disponible.');
      onEnd();
      return;
    }
    this.cancel();
    window.speechSynthesis.resume();
    this.refreshVoices();
    const generation = ++this.generation;
    this.speakAttempt(text, voiceName, onStart, onEnd, onError, generation);
  }

  cancel(): void {
    this.generation += 1;
    if (this.available) window.speechSynthesis.cancel();
    this.fallbackRequest?.abort();
    this.fallbackRequest = undefined;
    this.fallbackAudio?.pause();
    this.fallbackAudio = undefined;
    if (this.fallbackObjectUrl) URL.revokeObjectURL(this.fallbackObjectUrl);
    this.fallbackObjectUrl = undefined;
    this.fallbackSources.forEach((source) => {
      try { source.stop(); } catch { /* The source may have already ended. */ }
    });
    this.fallbackSources = [];
    void this.fallbackContext?.close();
    this.fallbackContext = undefined;
    this.speaking = false;
  }

  isSpeaking(): boolean { return this.speaking || Boolean(window.speechSynthesis?.speaking); }

  private refreshVoices(): void {
    this.voices = window.speechSynthesis?.getVoices() ?? [];
  }

  private speakAttempt(
    text: string,
    voiceName: string | undefined,
    onStart: () => void,
    onEnd: () => void,
    onError: ((message: string) => void) | undefined,
    generation: number,
  ): void {
    const utterance = new SpeechSynthesisUtterance(text);
    const selectedVoice = this.voices.find((voice) => voice.name === voiceName)
      ?? this.voices.find((voice) => voice.lang.toLowerCase().startsWith(this.lang.slice(0, 2)));
    utterance.lang = selectedVoice?.lang ?? this.lang;
    if (selectedVoice) utterance.voice = selectedVoice;
    utterance.rate = 0.92;
    utterance.pitch = 1.12;
    utterance.onstart = () => { if (generation === this.generation) { this.speaking = true; onStart(); } };
    utterance.onend = () => { if (generation === this.generation) { this.speaking = false; onEnd(); } };
    utterance.onerror = (event) => {
      if (generation !== this.generation) return;
      const cancellableError = event.error === 'canceled' || event.error === 'interrupted';
      if (!cancellableError && this.fallbackUrl) {
        void this.speakFallback(text, onStart, onEnd, onError, generation);
        return;
      }
      this.speaking = false;
      const detail = this.voices.length === 0 ? 'Brave no expone voces instaladas en el sistema' : (event.error || 'error desconocido');
      onError?.(`TTS no pudo reproducir el fragmento (${detail}).`);
      onEnd();
    };
    window.speechSynthesis.speak(utterance);
  }

  private async speakFallbackStream(
    text: string,
    onStart: () => void,
    onEnd: () => void,
    onError: ((message: string) => void) | undefined,
    generation: number,
  ): Promise<void> {
    const request = new AbortController();
    this.fallbackRequest = request;
    let context: AudioContext | undefined;
    let nextTime = 0;
    let started = false;
    try {
      const response = await fetch(this.fallbackUrl!, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
        signal: request.signal,
      });
      if (!response.ok) throw await backendErrorFromResponse(response, 'No se pudo reproducir el audio.');
      if (!response.body) throw new Error('La API no devolvió un stream de audio.');
      const sampleRate = Number(response.headers.get('X-Audio-Sample-Rate') ?? 24000);
      context = new AudioContext({ sampleRate });
      this.fallbackContext = context;
      const reader = response.body.getReader();
      let remainder = new Uint8Array(0);
      while (generation === this.generation) {
        const { value, done } = await reader.read();
        if (done) break;
        if (!value) continue;
        const bytes = new Uint8Array(remainder.length + value.length);
        bytes.set(remainder);
        bytes.set(value, remainder.length);
        const usableLength = bytes.length - (bytes.length % 2);
        remainder = bytes.slice(usableLength);
        if (usableLength === 0) continue;
        const samples = new Int16Array(bytes.buffer, bytes.byteOffset, usableLength / 2);
        const buffer = context.createBuffer(1, samples.length, sampleRate);
        const channel = buffer.getChannelData(0);
        for (let index = 0; index < samples.length; index += 1) channel[index] = samples[index] / 32768;
        const source = context.createBufferSource();
        source.buffer = buffer;
        source.connect(context.destination);
        const startAt = Math.max(context.currentTime + 0.03, nextTime);
        source.start(startAt);
        nextTime = startAt + buffer.duration;
        this.fallbackSources.push(source);
        if (!started) { started = true; this.speaking = true; onStart(); }
      }
      if (generation !== this.generation) return;
      const wait = Math.max(0, (nextTime - context.currentTime) * 1000);
      window.setTimeout(() => {
        if (generation === this.generation) { this.speaking = false; onEnd(); }
      }, wait);
    } catch (error) {
      if (generation !== this.generation || (error instanceof DOMException && error.name === 'AbortError')) return;
      this.speaking = false;
      onError?.(error instanceof BackendError ? error.message : 'No se pudo reproducir el audio.');
      onEnd();
    } finally {
      if (this.fallbackRequest === request) this.fallbackRequest = undefined;
    }
  }

  private async speakFallback(
    text: string,
    onStart: () => void,
    onEnd: () => void,
    onError: ((message: string) => void) | undefined,
    generation: number,
  ): Promise<void> {
    const request = new AbortController();
    this.fallbackRequest = request;
    let objectUrl: string | undefined;
    let finished = false;
    const finish = (callback: () => void) => {
      if (finished) return;
      finished = true;
      callback();
    };
    try {
      const response = await fetch(this.fallbackUrl!, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
        signal: request.signal,
      });
      if (!response.ok) throw await backendErrorFromResponse(response, 'No se pudo reproducir el audio.');
      const audioBlob = await response.blob();
      if (generation !== this.generation) return;
      objectUrl = URL.createObjectURL(audioBlob);
      if (generation !== this.generation) {
        URL.revokeObjectURL(objectUrl);
        return;
      }
      this.fallbackObjectUrl = objectUrl;
      const audio = new Audio(objectUrl);
      this.fallbackAudio = audio;
      audio.onplay = () => { if (generation === this.generation) { this.speaking = true; onStart(); } };
      audio.onended = () => {
        this.releaseFallbackAudio(objectUrl!);
        if (generation === this.generation) finish(() => { this.speaking = false; onEnd(); });
      };
      audio.onerror = () => {
        this.releaseFallbackAudio(objectUrl!);
        if (generation === this.generation) finish(() => { this.speaking = false; onError?.('El audio local no pudo reproducirse.'); onEnd(); });
      };
      await audio.play();
    } catch (error) {
      if (objectUrl) this.releaseFallbackAudio(objectUrl);
      if (generation !== this.generation || (error instanceof DOMException && error.name === 'AbortError')) return;
      finish(() => { onError?.(error instanceof BackendError ? error.message : 'No se pudo reproducir el audio.'); onEnd(); });
    } finally {
      if (this.fallbackRequest === request) this.fallbackRequest = undefined;
    }
  }

  private releaseFallbackAudio(url: string): void {
    if (this.fallbackObjectUrl === url) this.fallbackObjectUrl = undefined;
    if (this.fallbackAudio?.src === url) this.fallbackAudio = undefined;
    URL.revokeObjectURL(url);
  }
}
