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
    level: clamp01(rms * 4.2 * (0.5 + 0.5 * pitch)),
    voiced,
    rms,
  };
}

export function voiceLevelFromSamples(samples: ArrayLike<number>, sampleRate: number): number {
  return analyzeVoice(samples, sampleRate).level;
}

export class LinearResampler {
  private leftover = new Float32Array(0);
  private frac = 0;

  constructor(
    private readonly inRate: number,
    private readonly outRate = 16000,
  ) {}

  push(chunk: Float32Array): Float32Array {
    if (chunk.length === 0) return new Float32Array(0);
    if (this.inRate === this.outRate) return chunk;
    const merged = new Float32Array(this.leftover.length + chunk.length);
    merged.set(this.leftover);
    merged.set(chunk, this.leftover.length);
    const step = this.inRate / this.outRate;
    if (merged.length < 2) {
      this.leftover = merged;
      return new Float32Array(0);
    }
    const out: number[] = [];
    let pos = this.frac;
    while (pos + 1 < merged.length) {
      const index = Math.floor(pos);
      const t = pos - index;
      out.push(merged[index] * (1 - t) + merged[index + 1] * t);
      pos += step;
    }
    const consumed = Math.min(Math.floor(pos), merged.length - 1);
    this.frac = pos - consumed;
    this.leftover = merged.slice(consumed);
    return Float32Array.from(out);
  }
}

export class AudioCaptureAdapter {
  private stream?: MediaStream;
  private audioContext?: AudioContext;
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
  private captureSink?: MediaStreamAudioDestinationNode;
  private resampler?: LinearResampler;

  primeContext(): void {
    if (!this.audioContext || this.audioContext.state === 'closed') {
      try {
        this.audioContext = new AudioContext({ sampleRate: 16000 });
      } catch {
        this.audioContext = new AudioContext();
      }
    }
    void this.audioContext.resume();
  }

  voiceLevel(): number {
    return this.lastLevel;
  }

  isVoiced(): boolean {
    return this.lastVoiced;
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
        // Let the browser's voice processor attenuate nearby conversations
        // before the mono stream reaches Sherpa. The server still applies its
        // own high-pass/gate because this constraint is only a best effort.
        noiseSuppression: { ideal: true },
        autoGainControl: true,
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
      this.speechAnalyser.maxDecibels = -12;
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

  private ensureSilentTap(context: AudioContext): GainNode {
    if (!this.captureSink || this.captureSink.context !== context) {
      this.captureSink = context.createMediaStreamDestination();
    }
    if (!this.captureMute || this.captureMute.context !== context) {
      this.captureMute = context.createGain();
      this.captureMute.gain.value = 0.0001;
      this.captureMute.connect(this.captureSink);
    }
    return this.captureMute;
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
      this.resampler = new LinearResampler(context.sampleRate, 16000);
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
      const mute = this.ensureSilentTap(context);
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
    this.resampler = undefined;
    if (this.workletNode) {
      this.workletNode.port.onmessage = null;
      this.workletNode.disconnect();
    }
    if (this.processor) {
      this.processor.onaudioprocess = null;
      this.processor.disconnect();
    }
    this.captureSource?.disconnect();
    this.captureMute?.disconnect();
    this.workletNode = undefined;
    this.processor = undefined;
    this.captureSource = undefined;
    this.captureMute = undefined;
    this.captureSink = undefined;
  }

  cancelRecording(): void {
    this.stopPcmStream();
  }

  abort(): void {
    this.teardownAnalyser();
    this.cleanupTracks();
  }

  private pushPcmFrame(input: Float32Array, inputRate: number): void {
    if (!this.resampler) this.resampler = new LinearResampler(inputRate, 16000);
    const floats = this.resampler.push(input);
    if (!floats.length) return;
    const converted = new Int16Array(floats.length);
    for (let i = 0; i < floats.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, floats[i] ?? 0));
      converted[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
    const voice = analyzeVoice(floats, 16000);
    this.lastLevel = voice.level;
    this.lastVoiced = voice.voiced;
    const merged = new Int16Array(this.pcmTail.length + converted.length);
    merged.set(this.pcmTail);
    merged.set(converted, this.pcmTail.length);
    const frameSize = 640;
    let offset = 0;
    while (merged.length - offset >= frameSize) {
      const frame = merged.slice(offset, offset + frameSize);
      this.pcmCallback?.(frame);
      offset += frameSize;
    }
    this.pcmTail = merged.slice(offset);
  }

  private teardownAnalyser(): void {
    this.stopPcmStream();
    this.workletReady = undefined;
    this.lastLevel = 0.04;
    this.lastVoiced = false;
    void this.audioContext?.close();
    this.audioContext = undefined;
    this.speechAnalyser = undefined;
  }

  private cleanupTracks(): void {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = undefined;
  }
}
