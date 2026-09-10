export class PcmAudioQueue {
  private context?: AudioContext;
  private nextTime = 0;
  private pending = 0;
  private finished = false;
  private generation = 0;

  start(sampleRate: number): void {
    this.cancel();
    this.generation += 1;
    this.context = new AudioContext({ sampleRate });
    this.nextTime = this.context.currentTime;
  }

  enqueue(chunk: Uint8Array, onStart: () => void, onEnd: () => void): void {
    if (!this.context || chunk.byteLength < 2) return;
    const generation = this.generation;
    const usableLength = chunk.byteLength - (chunk.byteLength % 2);
    const samples = new Int16Array(chunk.buffer, chunk.byteOffset, usableLength / 2);
    const buffer = this.context.createBuffer(1, samples.length, this.context.sampleRate);
    const channel = buffer.getChannelData(0);
    for (let index = 0; index < samples.length; index += 1) channel[index] = samples[index] / 32768;
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.context.destination);
    const startAt = Math.max(this.context.currentTime + 0.03, this.nextTime);
    source.start(startAt);
    this.nextTime = startAt + buffer.duration;
    this.pending += 1;
    if (this.pending === 1) onStart();
    source.onended = () => {
      this.pending -= 1;
      if (generation === this.generation && this.finished && this.pending === 0) onEnd();
    };
  }

  finish(onEnd: () => void): void {
    this.finished = true;
    if (this.pending === 0) onEnd();
  }

  cancel(): void {
    this.generation += 1;
    void this.context?.close();
    this.context = undefined;
    this.pending = 0;
    this.finished = false;
  }

  get size(): number { return this.pending; }
}
