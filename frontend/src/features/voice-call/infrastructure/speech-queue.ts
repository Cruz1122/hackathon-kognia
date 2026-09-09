import { BrowserSpeechSynthesizer } from './browser-speech-synthesizer';

export class SpeechQueue {
  private pending: string[] = [];
  private running = false;
  private voiceName?: string;

  constructor(private readonly synthesizer: BrowserSpeechSynthesizer) {}

  setVoice(name?: string): void { this.voiceName = name; }

  enqueue(text: string, onStart: () => void, onEmpty: () => void, onError: (message: string) => void): void {
    if (!text.trim()) return;
    this.pending.push(text.trim());
    this.drain(onStart, onEmpty, onError);
  }

  cancel(): void {
    this.pending = [];
    this.running = false;
    this.synthesizer.cancel();
  }

  get size(): number { return this.pending.length + (this.running ? 1 : 0); }

  private drain(onStart: () => void, onEmpty: () => void, onError: (message: string) => void): void {
    if (this.running || this.pending.length === 0) {
      if (!this.running && this.pending.length === 0) onEmpty();
      return;
    }
    this.running = true;
    const next = this.pending.shift();
    if (!next) return;
    try {
      this.synthesizer.speak(next, onStart, () => {
        this.running = false;
        this.drain(onStart, onEmpty, onError);
      }, this.voiceName, onError);
    } catch {
      this.running = false;
      onError('El navegador rechazó la síntesis de voz.');
      this.drain(onStart, onEmpty, onError);
    }
  }
}
