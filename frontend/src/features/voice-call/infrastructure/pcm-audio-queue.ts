import { voiceLevelFromSamples } from './audio-capture-adapter';

const AGENT_GAIN = 2.5;

export class PcmAudioQueue {
  private context?: AudioContext;
  private analyser?: AnalyserNode;
  private nextTime = 0;
  private pending = 0;
  private finished = false;
  private generation = 0;
  private lastLevel = 0.04;
  private inputRate = 24000;
  private tail = new Float32Array(0);
  private sources = new Set<AudioBufferSourceNode>();
  private paused = false;
  private output?: GainNode;

  frequencyAnalyser(): { analyser: AnalyserNode; sampleRate: number } | undefined {
    if (!this.context || !this.analyser) return undefined;
    if (!this.paused && this.context.state === 'suspended') void this.context.resume();
    return { analyser: this.analyser, sampleRate: this.context.sampleRate };
  }

  voiceLevel(): number {
    return this.lastLevel;
  }

  prime(): void {
    if (!this.context || this.context.state === 'closed') this.context = new AudioContext();
    if (!this.paused) void this.context.resume();
  }

  start(sampleRate: number): void {
    this.stopSources();
    this.generation += 1;
    this.inputRate = sampleRate || 24000;
    this.pending = 0;
    this.finished = false;
    this.tail = new Float32Array(0);
    this.lastLevel = 0.04;
    this.nextTime = 0;
    this.prime();
    const context = this.context;
    if (!context) return;
    if (!this.output || this.output.context !== context) {
      this.output?.disconnect();
      this.output = context.createGain();
      this.output.gain.value = AGENT_GAIN;
      this.output.connect(context.destination);
    }
    if (!this.analyser || this.analyser.context !== context) {
      this.analyser?.disconnect();
      this.analyser = context.createAnalyser();
      this.analyser.fftSize = 2048;
      this.analyser.smoothingTimeConstant = 0.18;
      this.analyser.minDecibels = -90;
      this.analyser.maxDecibels = -12;
      this.analyser.connect(this.output);
    }
    if (!this.paused) void context.resume();
  }

  pause(): void {
    this.paused = true;
    if (this.context && this.context.state === 'running') void this.context.suspend();
  }

  resume(): void {
    this.paused = false;
    if (this.context && this.context.state === 'suspended') void this.context.resume();
  }

  enqueue(chunk: Uint8Array, onStart: () => void, onEnd: () => void): void {
    if (!this.context || !this.analyser || chunk.byteLength < 2) return;
    if (!this.paused) void this.context.resume();
    const copy = new Uint8Array(chunk.byteLength);
    copy.set(chunk);
    const usableLength = copy.byteLength - (copy.byteLength % 2);
    const samples = new Int16Array(copy.buffer, 0, usableLength / 2);
    const incoming = new Float32Array(samples.length);
    for (let index = 0; index < samples.length; index += 1) incoming[index] = samples[index] / 32768;
    const merged = new Float32Array(this.tail.length + incoming.length);
    merged.set(this.tail);
    merged.set(incoming, this.tail.length);
    const minSamples = Math.floor(this.inputRate * 0.02);
    if (!this.finished && merged.length < minSamples) {
      this.tail = merged;
      return;
    }
    this.tail = new Float32Array(0);
    this.play(merged, this.generation, onStart, onEnd);
  }

  private play(samples: Float32Array, generation: number, onStart: () => void, onEnd: () => void): void {
    if (!this.context || !this.analyser || samples.length === 0) return;
    this.lastLevel = voiceLevelFromSamples(samples, this.inputRate);
    const buffer = this.context.createBuffer(1, samples.length, this.inputRate);
    buffer.copyToChannel(samples, 0);
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.analyser);
    const now = this.context.currentTime;
    if (this.nextTime < now + 0.02) this.nextTime = now + 0.03;
    source.start(this.nextTime);
    this.nextTime += buffer.duration;
    this.pending += 1;
    this.sources.add(source);
    if (this.pending === 1) onStart();
    source.onended = () => {
      this.sources.delete(source);
      this.pending = Math.max(0, this.pending - 1);
      if (this.pending === 0) this.lastLevel = 0.04;
      if (generation === this.generation && this.finished && this.pending === 0 && this.tail.length === 0) onEnd();
    };
  }

  finish(onEnd: () => void): void {
    this.finished = true;
    if (this.tail.length) {
      const leftover = this.tail;
      this.tail = new Float32Array(0);
      this.play(leftover, this.generation, () => undefined, onEnd);
      return;
    }
    if (this.pending === 0) onEnd();
  }

  cancel(): void {
    this.generation += 1;
    this.stopSources();
    this.lastLevel = 0.04;
    this.pending = 0;
    this.finished = false;
    this.tail = new Float32Array(0);
    this.nextTime = 0;
    this.paused = false;
  }

  shutdown(): void {
    this.cancel();
    this.analyser?.disconnect();
    this.analyser = undefined;
    this.output?.disconnect();
    this.output = undefined;
    void this.context?.close();
    this.context = undefined;
  }

  private stopSources(): void {
    for (const source of this.sources) {
      source.onended = null;
      try {
        source.stop();
      } catch {
        /* already stopped */
      }
      source.disconnect();
    }
    this.sources.clear();
  }

  get size(): number { return this.pending; }
}
