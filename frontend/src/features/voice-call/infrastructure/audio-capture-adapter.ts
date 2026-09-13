type CaptureHandlers = {
  onResult: (result: { transcript: string; isFinal: boolean }) => void;
  onAudio?: (audio: Blob, mimeType: string) => void;
  onEnd: () => void;
  onError: (message: string) => void;
};

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

function goertzel(samples: ArrayLike<number>, sampleRate: number, freq: number): number {
  const n = samples.length;
  const omega = (2 * Math.PI * freq) / sampleRate;
  const coeff = 2 * Math.cos(omega);
  let s0 = 0;
  let s1 = 0;
  let s2 = 0;
  for (let i = 0; i < n; i += 1) {
    s0 = samples[i] + coeff * s1 - s2;
    s2 = s1;
    s1 = s0;
  }
  return Math.sqrt(Math.max(0, s1 * s1 + s2 * s2 - coeff * s1 * s2)) / n;
}

export function analyzeVoice(samples: ArrayLike<number>, sampleRate: number): { level: number; voiced: boolean; rms: number } {
  const n = samples.length;
  if (n < 32) return { level: 0.04, voiced: false, rms: 0 };
  let energy = 0;
  let crossings = 0;
  let previous = samples[0];
  for (let i = 0; i < n; i += 1) {
    const sample = samples[i];
    energy += sample * sample;
    if ((previous >= 0) !== (sample >= 0)) crossings += 1;
    previous = sample;
  }
  const rms = Math.sqrt(energy / n);
  if (rms < 0.003) return { level: clamp01(rms * 14), voiced: false, rms };
  let bands = 0;
  for (const hz of [120, 240, 500, 900, 1600, 2600]) bands += goertzel(samples, sampleRate, hz);
  const freq = Math.sqrt(bands / 6);
  const pitchHz = (crossings * sampleRate) / (2 * n);
  const pitch = clamp01((Math.min(500, Math.max(70, pitchHz)) - 70) / 430);
  const voiced = rms >= 0.016 && pitchHz >= 85 && pitchHz <= 340 && freq > rms * 0.35;
  return {
    level: clamp01(Math.max(rms * 9, freq * 4.4) * (0.45 + 0.55 * pitch)),
    voiced,
    rms,
  };
}

export function voiceLevelFromSamples(samples: ArrayLike<number>, sampleRate: number): number {
  return analyzeVoice(samples, sampleRate).level;
}

export class AudioCaptureAdapter {
  private recorder?: MediaRecorder;
  private stream?: MediaStream;
  private audioContext?: AudioContext;
  private silenceTimer?: number;
  private request?: AbortController;
  private heardSpeech = false;
  private lastSpeechAt = 0;
  private firstSpeechAt = 0;
  private speechHits = 0;
  private discarding = false;
  private handlers?: CaptureHandlers;
  private captureGeneration = 0;
  private monitorStream?: MediaStream;
  private monitorContext?: AudioContext;
  private monitorTimer?: number;
  private monitorGeneration = 0;
  private monitorHits = 0;
  private speechAnalyser?: AnalyserNode;
  private processor?: ScriptProcessorNode;
  private lastLevel = 0.04;
  private lastVoiced = false;
  private workletNode?: AudioWorkletNode;
  private captureSource?: MediaStreamAudioSourceNode;
  private pcmTail = new Int16Array(0);
  private pcmCallback?: (frame: Int16Array) => void;
  private workletReady?: Promise<void>;
  private captureMute?: GainNode;
  private levelListener?: (level: number) => void;
  private maxRecordTimer?: number;

  constructor(private readonly transcribeUrl: string) {}

  primeContext(): void {
    if (!this.audioContext || this.audioContext.state === 'closed') this.audioContext = new AudioContext();
    void this.audioContext.resume();
  }

  voiceLevel(): number {
    return this.lastLevel;
  }

  isVoiced(): boolean {
    return this.lastVoiced;
  }

  onLevel(listener: ((level: number) => void) | undefined): void {
    this.levelListener = listener;
  }

  frequencyAnalyser(): { analyser: AnalyserNode; sampleRate: number } | undefined {
    if (!this.audioContext || this.audioContext.state === 'closed' || !this.speechAnalyser) return undefined;
    if (this.audioContext.state === 'suspended') void this.audioContext.resume();
    return { analyser: this.speechAnalyser, sampleRate: this.audioContext.sampleRate };
  }

  private micConstraints(): MediaStreamConstraints {
    return {
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: false,
        channelCount: 1,
      },
    };
  }

  private ensureCaptureAnalyser(context: AudioContext): void {
    if (!this.captureSource) return;
    if (!this.speechAnalyser || this.speechAnalyser.context !== context) {
      this.speechAnalyser = context.createAnalyser();
      this.speechAnalyser.fftSize = 2048;
      this.speechAnalyser.smoothingTimeConstant = 0.18;
      this.speechAnalyser.minDecibels = -90;
      this.speechAnalyser.maxDecibels = -25;
    }
    try {
      this.captureSource.connect(this.speechAnalyser);
    } catch {
      /* already connected */
    }
    if (this.captureMute) {
      try {
        this.speechAnalyser.connect(this.captureMute);
      } catch {
        /* already connected */
      }
    }
  }

  private attachSpeechAnalyser(): void {
    if (!this.stream) return;
    this.primeContext();
    const context = this.audioContext;
    if (!context) return;
    if (this.processor && this.speechAnalyser && context.state !== 'closed') {
      void context.resume();
      return;
    }
    const source = context.createMediaStreamSource(this.stream);
    this.speechAnalyser = context.createAnalyser();
    this.speechAnalyser.fftSize = 2048;
    this.speechAnalyser.smoothingTimeConstant = 0.18;
    this.speechAnalyser.minDecibels = -90;
    this.speechAnalyser.maxDecibels = -25;
    source.connect(this.speechAnalyser);
    const mute = context.createGain();
    mute.gain.value = 0.0001;
    this.speechAnalyser.connect(mute);
    mute.connect(context.destination);
    this.processor = context.createScriptProcessor(1024, 1, 1);
    source.connect(this.processor);
    this.processor.connect(mute);
    this.processor.onaudioprocess = (event) => {
      const copy = new Float32Array(event.inputBuffer.getChannelData(0));
      const voice = analyzeVoice(copy, context.sampleRate);
      this.lastLevel = voice.level;
      this.lastVoiced = voice.voiced;
      this.levelListener?.(this.lastLevel);
    };
    void context.resume();
  }

  setTrackEnabled(enabled: boolean): void {
    this.stream?.getAudioTracks().forEach((track) => {
      track.enabled = enabled;
    });
    if (!enabled) this.stop();
  }

  async start(handlers: CaptureHandlers): Promise<boolean> {
    if (this.recorder) return false;
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      handlers.onError('Este navegador no permite capturar audio.');
      return false;
    }
    const generation = ++this.captureGeneration;
    try {
      this.handlers = handlers;
      this.discarding = false;
      if (!this.stream) {
        const stream = await navigator.mediaDevices.getUserMedia(this.micConstraints());
        if (generation !== this.captureGeneration || this.discarding) {
          stream.getTracks().forEach((track) => track.stop());
          return false;
        }
        this.stream = stream;
      }
      this.stream.getAudioTracks().forEach((track) => {
        track.enabled = true;
      });
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
      this.recorder = new MediaRecorder(this.stream, { mimeType });
      this.heardSpeech = false;
      this.speechHits = 0;
      this.lastSpeechAt = 0;
      this.firstSpeechAt = 0;
      const recorder = this.recorder;
      const chunks: Blob[] = [];
      recorder.ondataavailable = (event) => {
        if (generation !== this.captureGeneration || recorder !== this.recorder || !event.data.size) return;
        chunks.push(event.data);
        if (!this.heardSpeech && chunks.length > 8) chunks.splice(0, chunks.length - 8);
      };
      recorder.onerror = () => this.handleRecorderError(generation, recorder);
      this.recorder.onstop = () => {
        if (!this.discarding && generation === this.captureGeneration && recorder === this.recorder) {
          const blob = new Blob(chunks, { type: mimeType });
          this.recorder = undefined;
          this.pauseSilenceDetection();
          if (handlers.onAudio) {
            if (blob.size > 1200 && this.heardSpeech) handlers.onAudio(blob, mimeType);
            else handlers.onEnd();
          } else void this.transcribe(handlers, mimeType, generation, chunks);
        }
      };
      this.recorder.start(200);
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
      this.teardownAnalyser();
      this.cleanupTracks();
      this.handlers = undefined;
      handlers.onError(error instanceof DOMException && error.name === 'NotAllowedError' ? 'Permiso de micrófono denegado.' : 'No se pudo abrir el micrófono.');
      return false;
    }
  }

  stop(): void {
    if (this.maxRecordTimer !== undefined) {
      window.clearTimeout(this.maxRecordTimer);
      this.maxRecordTimer = undefined;
    }
    this.pauseSilenceDetection();
    if (this.recorder && this.recorder.state !== 'inactive') this.recorder.stop();
  }

  cancelRecording(): void {
    this.discarding = true;
    if (this.maxRecordTimer !== undefined) {
      window.clearTimeout(this.maxRecordTimer);
      this.maxRecordTimer = undefined;
    }
    this.pauseSilenceDetection();
    const recorder = this.recorder;
    if (recorder && recorder.state !== 'inactive') {
      recorder.onstop = null;
      recorder.onerror = null;
      recorder.stop();
    }
    this.recorder = undefined;
    this.handlers = undefined;
    this.discarding = false;
  }

  async startPcmStream(onFrame: (frame: Int16Array) => void): Promise<boolean> {
    this.primeContext();
    const context = this.audioContext;
    if (!context || !navigator.mediaDevices?.getUserMedia) return false;
    try {
      await context.resume();
      if (!this.stream) this.stream = await navigator.mediaDevices.getUserMedia(this.micConstraints());
      this.stream.getAudioTracks().forEach((track) => {
        track.enabled = true;
      });
      this.pcmCallback = onFrame;
      this.pcmTail = new Int16Array(0);
      if (this.workletNode || this.processor) {
        this.ensureCaptureAnalyser(context);
        return true;
      }
      if (!this.workletReady) {
        const source = [
          'class PcmCaptureProcessor extends AudioWorkletProcessor {',
          '  process(inputs) {',
          '    const channel = inputs[0] && inputs[0][0];',
          '    if (channel && channel.length) this.port.postMessage(channel.slice());',
          '    return true;',
          '  }',
          '}',
          "registerProcessor('pcm-capture', PcmCaptureProcessor);",
        ].join('\n');
        const blob = new Blob([source], { type: 'application/javascript' });
        this.workletReady = context.audioWorklet.addModule(URL.createObjectURL(blob));
      }
      try {
        await this.workletReady;
      } catch {
        this.workletReady = undefined;
      }
      this.captureSource = context.createMediaStreamSource(this.stream);
      const mute = context.createGain();
      mute.gain.value = 0.0001;
      mute.connect(context.destination);
      this.captureMute = mute;
      this.ensureCaptureAnalyser(context);
      if (this.workletReady) {
        this.workletNode = new AudioWorkletNode(context, 'pcm-capture');
        this.captureSource.connect(this.workletNode);
        this.workletNode.connect(mute);
        this.workletNode.port.onmessage = (event) => {
          if (!(event.data instanceof Float32Array)) return;
          this.pushPcmFrame(event.data, context.sampleRate);
        };
      } else {
        this.processor = context.createScriptProcessor(1024, 1, 1);
        this.captureSource.connect(this.processor);
        this.processor.connect(mute);
        this.processor.onaudioprocess = (event) => {
          this.pushPcmFrame(event.inputBuffer.getChannelData(0), context.sampleRate);
        };
      }
      return true;
    } catch {
      return false;
    }
  }

  stopPcmStream(): void {
    this.pcmCallback = undefined;
    this.pcmTail = new Int16Array(0);
    if (this.workletNode) {
      this.workletNode.port.onmessage = null;
      this.workletNode.disconnect();
    }
    if (this.processor) {
      this.processor.onaudioprocess = null;
      this.processor.disconnect();
    }
    this.captureSource?.disconnect();
    this.workletNode = undefined;
    this.processor = undefined;
    this.captureSource = undefined;
    this.captureMute = undefined;
  }

  private pushPcmFrame(input: Float32Array, inputRate: number): void {
    const ratio = inputRate / 16000;
    const length = Math.max(1, Math.floor(input.length / ratio));
    const converted = new Int16Array(length);
    const floats = new Float32Array(length);
    for (let i = 0; i < length; i += 1) {
      const sample = Math.max(-1, Math.min(1, input[Math.floor(i * ratio)] ?? 0));
      floats[i] = sample;
      converted[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
    const voice = analyzeVoice(floats, 16000);
    this.lastLevel = voice.level;
    this.lastVoiced = voice.voiced;
    const merged = new Int16Array(this.pcmTail.length + converted.length);
    merged.set(this.pcmTail);
    merged.set(converted, this.pcmTail.length);
    const frameSize = 1600;
    let offset = 0;
    while (merged.length - offset >= frameSize) {
      const frame = merged.slice(offset, offset + frameSize);
      this.pcmCallback?.(frame);
      offset += frameSize;
    }
    this.pcmTail = merged.slice(offset);
  }

  async ensureSession(): Promise<boolean> {
    this.primeContext();
    if (this.stream && this.processor && this.audioContext?.state !== 'closed') {
      await this.audioContext.resume();
      return true;
    }
    if (!navigator.mediaDevices?.getUserMedia) return false;
    try {
      if (!this.stream) this.stream = await navigator.mediaDevices.getUserMedia(this.micConstraints());
      this.attachSpeechAnalyser();
      if (this.audioContext?.state === 'suspended') await this.audioContext.resume();
      return Boolean(this.processor || this.speechAnalyser);
    } catch {
      return false;
    }
  }

  abort(): void {
    this.captureGeneration += 1;
    this.discarding = true;
    this.request?.abort();
    this.request = undefined;
    if (this.maxRecordTimer !== undefined) {
      window.clearTimeout(this.maxRecordTimer);
      this.maxRecordTimer = undefined;
    }
    if (this.recorder && this.recorder.state !== 'inactive') this.recorder.stop();
    this.recorder = undefined;
    this.teardownAnalyser();
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
        this.monitorHits = rms > 0.16 ? this.monitorHits + 1 : 0;
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
    this.pauseSilenceDetection();
    try {
      this.attachSpeechAnalyser();
      const analyser = this.speechAnalyser;
      const data = analyser ? new Uint8Array(analyser.fftSize) : undefined;
      const check = () => {
        let rms = 0;
        if (analyser && data) {
          analyser.getByteTimeDomainData(data);
          rms = Math.sqrt(data.reduce((sum, value) => sum + ((value - 128) / 128) ** 2, 0) / data.length);
        }
        const speaking = rms > 0.16 || this.lastLevel > 0.22;
        if (speaking) {
          this.speechHits += 1;
          this.lastSpeechAt = performance.now();
          if (!this.heardSpeech && this.speechHits >= 4) {
            this.heardSpeech = true;
            this.firstSpeechAt = this.lastSpeechAt;
          }
        } else {
          this.speechHits = 0;
        }
        const now = performance.now();
        if (this.heardSpeech && (now - this.lastSpeechAt > 900 || now - this.firstSpeechAt > 15000)) this.stop();
        else this.silenceTimer = window.setTimeout(check, 80);
      };
      this.silenceTimer = window.setTimeout(check, 80);
    } catch {
      handlers.onError('Detección automática de silencio no disponible.');
    }
  }

  private pauseSilenceDetection(): void {
    if (this.silenceTimer !== undefined) window.clearTimeout(this.silenceTimer);
    this.silenceTimer = undefined;
  }

  private async transcribe(handlers: CaptureHandlers, mimeType: string, generation: number, chunks: Blob[]): Promise<void> {
    if (generation !== this.captureGeneration || this.discarding) return;
    const blob = new Blob(chunks, { type: mimeType });
    this.recorder = undefined;
    this.teardownAnalyser();
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
      else if (generation === this.captureGeneration && !this.discarding) handlers.onError('No se detectó una frase.');
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
    this.teardownAnalyser();
    this.cleanupTracks();
    this.recorder = undefined;
    this.handlers = undefined;
    handlers?.onError('El navegador no pudo grabar el micrófono.');
    handlers?.onEnd();
  }

  private teardownAnalyser(): void {
    this.pauseSilenceDetection();
    if (this.processor) {
      this.processor.onaudioprocess = null;
      this.processor.disconnect();
    }
    this.processor = undefined;
    this.stopPcmStream();
    this.workletReady = undefined;
    this.lastLevel = 0.04;
    this.lastVoiced = false;
    void this.audioContext?.close();
    this.audioContext = undefined;
    this.speechAnalyser = undefined;
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
