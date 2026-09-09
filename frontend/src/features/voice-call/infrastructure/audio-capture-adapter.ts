type CaptureHandlers = {
  onResult: (result: { transcript: string; isFinal: boolean }) => void;
  onEnd: () => void;
  onError: (message: string) => void;
};

export class AudioCaptureAdapter {
  private recorder?: MediaRecorder;
  private stream?: MediaStream;
  private audioContext?: AudioContext;
  private silenceTimer?: number;
  private request?: AbortController;
  private heardSpeech = false;
  private lastSpeechAt = 0;
  private discarding = false;
  private handlers?: CaptureHandlers;
  private captureGeneration = 0;
  private monitorStream?: MediaStream;
  private monitorContext?: AudioContext;
  private monitorTimer?: number;
  private monitorGeneration = 0;
  private monitorHits = 0;

  constructor(private readonly transcribeUrl: string) {}

  async start(handlers: CaptureHandlers): Promise<boolean> {
    if (this.recorder) return false;
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      handlers.onError('Este navegador no permite capturar audio. Usa el input textual.');
      return false;
    }
    const generation = ++this.captureGeneration;
    try {
      this.handlers = handlers;
      this.discarding = false;
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (generation !== this.captureGeneration || this.discarding) {
        stream.getTracks().forEach((track) => track.stop());
        return false;
      }
      this.stream = stream;
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
      this.recorder = new MediaRecorder(this.stream, { mimeType });
      this.heardSpeech = false;
      this.lastSpeechAt = performance.now();
      const recorder = this.recorder;
      const chunks: Blob[] = [];
      recorder.ondataavailable = (event) => {
        if (generation === this.captureGeneration && recorder === this.recorder && event.data.size) chunks.push(event.data);
      };
      recorder.onerror = () => this.handleRecorderError(generation, recorder);
      this.recorder.onstop = () => {
        if (!this.discarding && generation === this.captureGeneration && recorder === this.recorder) void this.transcribe(handlers, mimeType, generation, chunks);
      };
      this.recorder.start(100);
      this.startSilenceDetection(handlers);
      return true;
    } catch (error) {
      if (this.discarding || generation !== this.captureGeneration) return false;
      this.discarding = true;
      const recorder = this.recorder;
      if (recorder && recorder.state !== 'inactive') {
        recorder.onstop = null;
        recorder.onerror = null;
        recorder.stop();
      }
      this.recorder = undefined;
      this.stopSilenceDetection();
      this.cleanupTracks();
      this.handlers = undefined;
      handlers.onError(error instanceof DOMException && error.name === 'NotAllowedError' ? 'Permiso de micrófono denegado. Usa el input textual.' : 'No se pudo abrir el micrófono.');
      return false;
    }
  }

  stop(): void {
    if (this.recorder && this.recorder.state !== 'inactive') this.recorder.stop();
    this.stopSilenceDetection();
  }

  abort(): void {
    this.captureGeneration += 1;
    this.discarding = true;
    this.request?.abort();
    this.request = undefined;
    if (this.recorder && this.recorder.state !== 'inactive') this.recorder.stop();
    this.recorder = undefined;
    this.stopSilenceDetection();
    this.cleanupTracks();
    this.handlers = undefined;
    this.stopBargeIn();
  }

  async startBargeIn(onSpeech: () => void, onError: (message: string) => void): Promise<boolean> {
    if (this.recorder || this.monitorStream || !navigator.mediaDevices?.getUserMedia) return false;
    const generation = ++this.monitorGeneration;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (generation !== this.monitorGeneration) {
        stream.getTracks().forEach((track) => track.stop());
        return false;
      }
      this.monitorStream = stream;
      this.monitorContext = new AudioContext();
      const analyser = this.monitorContext.createAnalyser();
      analyser.fftSize = 512;
      this.monitorContext.createMediaStreamSource(stream).connect(analyser);
      const data = new Uint8Array(analyser.fftSize);
      const check = () => {
        if (generation !== this.monitorGeneration || !this.monitorStream) return;
        analyser.getByteTimeDomainData(data);
        const rms = Math.sqrt(data.reduce((sum, value) => sum + ((value - 128) / 128) ** 2, 0) / data.length);
        this.monitorHits = rms > 0.07 ? this.monitorHits + 1 : 0;
        if (this.monitorHits >= 3) { this.stopBargeIn(); onSpeech(); return; }
        this.monitorTimer = window.setTimeout(check, 100);
      };
      this.monitorTimer = window.setTimeout(check, 100);
      return true;
    } catch {
      if (generation === this.monitorGeneration) {
        this.stopBargeIn();
        onError('No se pudo activar la detección de interrupción.');
      }
      return false;
    }
  }

  private startSilenceDetection(handlers: CaptureHandlers): void {
    if (!this.stream) return;
    try {
      this.audioContext = new AudioContext();
      const analyser = this.audioContext.createAnalyser();
      analyser.fftSize = 512;
      this.audioContext.createMediaStreamSource(this.stream).connect(analyser);
      const data = new Uint8Array(analyser.fftSize);
      const check = () => {
        analyser.getByteTimeDomainData(data);
        const rms = Math.sqrt(data.reduce((sum, value) => sum + ((value - 128) / 128) ** 2, 0) / data.length);
        if (rms > 0.035) { this.heardSpeech = true; this.lastSpeechAt = performance.now(); }
        if (this.heardSpeech && performance.now() - this.lastSpeechAt > 900) this.stop();
        else this.silenceTimer = window.setTimeout(check, 100);
      };
      this.silenceTimer = window.setTimeout(check, 100);
    } catch {
      // Recording still works when AudioContext/VAD is unavailable; stop manually on navigation.
      handlers.onError('Detección automática de silencio no disponible; usa el input textual.');
    }
  }

  private async transcribe(handlers: CaptureHandlers, mimeType: string, generation: number, chunks: Blob[]): Promise<void> {
    if (generation !== this.captureGeneration || this.discarding) return;
    const blob = new Blob(chunks, { type: mimeType });
    this.recorder = undefined;
    this.stopSilenceDetection();
    this.cleanupTracks();
    if (!blob.size) { handlers.onEnd(); return; }
    const request = new AbortController();
    this.request = request;
    try {
      const response = await fetch(this.transcribeUrl, { method: 'POST', headers: { 'Content-Type': mimeType }, body: blob, signal: request.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json() as { text?: unknown };
      const text = typeof payload.text === 'string' ? payload.text.trim() : '';
      if (generation === this.captureGeneration && text) handlers.onResult({ transcript: text, isFinal: true });
      else if (generation === this.captureGeneration && !this.discarding) handlers.onError('Whisper no detectó una frase.');
    } catch (error) {
      if (generation === this.captureGeneration && !this.discarding && !(error instanceof DOMException && error.name === 'AbortError')) handlers.onError('No se pudo transcribir el audio.');
    } finally {
      if (this.request === request) this.request = undefined;
      if (generation === this.captureGeneration) {
        this.handlers = undefined;
        handlers.onEnd();
      }
    }
  }

  private handleRecorderError(generation: number, recorder: MediaRecorder): void {
    if (generation !== this.captureGeneration || recorder !== this.recorder) return;
    const handlers = this.handlers;
    this.discarding = true;
    this.stopSilenceDetection();
    this.cleanupTracks();
    this.recorder = undefined;
    this.handlers = undefined;
    handlers?.onError('El navegador no pudo grabar el micrófono.');
    handlers?.onEnd();
  }

  private stopSilenceDetection(): void {
    if (this.silenceTimer !== undefined) window.clearTimeout(this.silenceTimer);
    this.silenceTimer = undefined;
    void this.audioContext?.close();
    this.audioContext = undefined;
  }

  stopBargeIn(): void {
    this.monitorGeneration += 1;
    if (this.monitorTimer !== undefined) window.clearTimeout(this.monitorTimer);
    this.monitorTimer = undefined;
    void this.monitorContext?.close();
    this.monitorContext = undefined;
    this.monitorStream?.getTracks().forEach((track) => track.stop());
    this.monitorStream = undefined;
    this.monitorHits = 0;
  }

  private cleanupTracks(): void {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = undefined;
  }
}
