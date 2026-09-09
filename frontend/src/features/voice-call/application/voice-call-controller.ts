import type { ChatMessage, VoiceMetrics, VoiceSnapshot } from '../domain/types';
import { emptyMetrics } from '../domain/types';
import { AgentStreamAdapter } from '../infrastructure/agent-stream-adapter';
import { BrowserSpeechSynthesizer } from '../infrastructure/browser-speech-synthesizer';
import { CancellationController } from '../infrastructure/cancellation-controller';
import { AudioCaptureAdapter } from '../infrastructure/audio-capture-adapter';
import { SpeechQueue } from '../infrastructure/speech-queue';
import { SemanticChunker } from '../services/semantic-chunker';

type Listener = (snapshot: VoiceSnapshot) => void;

export class VoiceCallController {
  private snapshot: VoiceSnapshot = { state: 'idle', partialTranscript: '', lastUserMessage: '', assistantText: '', warning: '', error: '', muted: false, metrics: emptyMetrics(), history: [] };
  private readonly recognition: AudioCaptureAdapter;
  private readonly synthesizer: BrowserSpeechSynthesizer;
  private readonly queue: SpeechQueue;
  private readonly cancellation = new CancellationController();
  private readonly listeners = new Set<Listener>();
  private readonly chunker = new SemanticChunker();
  private bargeInTurn?: number;
  private retryTimer?: number;

  constructor(private readonly agent: AgentStreamAdapter, apiUrl: string, private readonly lang = 'es-CO') {
    this.synthesizer = new BrowserSpeechSynthesizer(this.lang, `${apiUrl}/synthesize`);
    this.queue = new SpeechQueue(this.synthesizer);
    this.recognition = new AudioCaptureAdapter(`${apiUrl}/transcribe`);
  }

  subscribe(listener: Listener): () => void { this.listeners.add(listener); listener(this.snapshot); return () => this.listeners.delete(listener); }
  get current(): VoiceSnapshot { return this.snapshot; }
  get voices(): SpeechSynthesisVoice[] { return this.synthesizer.voiceOptions; }
  setVoice(name: string): void { this.queue.setVoice(name || undefined); }

  start(): void {
    if (!['idle', 'ended', 'error'].includes(this.snapshot.state)) return;
    this.patch({ state: 'connecting', callStartedAt: performance.now(), history: [], assistantText: '', lastUserMessage: '', error: '', warning: '' });
    void this.listen();
  }

  end(): void {
    if (this.retryTimer !== undefined) window.clearTimeout(this.retryTimer);
    this.cancellation.cancel('call-ended');
    this.recognition.abort();
    this.queue.cancel();
    this.patch({ state: 'ended' });
  }

  toggleMute(): void {
    const muted = !this.snapshot.muted;
    if (muted) {
      this.recognition.abort();
      if (this.snapshot.state === 'processing' || this.snapshot.state === 'speaking') {
        this.cancellation.cancel('microphone-muted');
        this.queue.cancel();
        this.chunker.reset();
      }
    }
    this.patch({ muted });
    if (!muted && !['idle', 'ended'].includes(this.snapshot.state)) void this.listen();
  }

  interrupt(): void {
    if (this.snapshot.state !== 'speaking' && this.snapshot.state !== 'processing') return;
    this.cancellation.cancel('user-interrupted');
    this.queue.cancel();
    this.chunker.reset();
    this.patch({ state: 'interrupted', metrics: { ...this.snapshot.metrics, interrupted: true, cancellationReason: 'user-interrupted' } });
    void this.listen();
  }

  submitText(text: string): void {
    const prompt = text.trim();
    if (!prompt || this.snapshot.state === 'idle' || this.snapshot.state === 'ended') return;
    this.recognition.abort();
    void this.processTurn(prompt);
  }

  private async listen(): Promise<void> {
    if (this.snapshot.muted || this.snapshot.state === 'ended') return;
    this.patch({ state: 'listening', partialTranscript: '' });
    const started = await this.recognition.start({
      onResult: (result) => {
        if (result.isFinal) {
          this.patch({ lastUserMessage: result.transcript.trim(), partialTranscript: '', metrics: { ...this.snapshot.metrics, transcriptFinalAt: performance.now() } });
          void this.processTurn(result.transcript);
        } else this.patch({ partialTranscript: result.transcript });
      },
      onEnd: () => undefined,
      onError: (message) => {
        this.patch({ warning: message });
        if (message.includes('no detectó')) {
          if (this.retryTimer !== undefined) window.clearTimeout(this.retryTimer);
          this.retryTimer = window.setTimeout(() => {
            this.retryTimer = undefined;
            if (this.snapshot.state === 'listening' && !this.snapshot.muted) void this.listen();
          }, 700);
        }
      },
    });
    if (!started) this.patch({ warning: 'Micrófono no disponible. Usa el input textual para continuar.' });
  }

  private async processTurn(prompt: string): Promise<void> {
    if (!prompt.trim()) return;
    const metrics: VoiceMetrics = { ...emptyMetrics(), speechEndedAt: performance.now(), transcriptFinalAt: this.snapshot.metrics.transcriptFinalAt ?? performance.now() };
    const history = [...this.snapshot.history];
    this.patch({ state: 'processing', lastUserMessage: prompt, assistantText: '', metrics });
    this.queue.cancel();
    const turn = this.cancellation.begin();
    const sentAt = performance.now();
    this.patch({ metrics: { ...metrics, agentSentAt: sentAt } });
    this.chunker.reset();
    let answer = '';
    try {
      for await (const event of this.agent.stream(prompt, history, turn.signal)) {
        if (!this.cancellation.isCurrent(turn.id)) return;
        if (event.type === 'token' && event.text) {
          answer += event.text;
          const current = this.snapshot.metrics;
          const nextMetrics = current.firstTokenAt ? current : { ...current, firstTokenAt: performance.now() };
          this.patch({ assistantText: answer, metrics: nextMetrics });
          for (const chunk of this.chunker.push(event.text)) this.enqueueChunk(chunk, turn.id);
        } else if (event.type === 'error') {
          throw new Error(event.message ?? 'El agente se interrumpió');
        }
      }
      for (const chunk of this.chunker.flush()) this.enqueueChunk(chunk, turn.id);
      if (!answer.trim()) throw new Error('El agente devolvió una respuesta vacía');
      const finalHistory: ChatMessage[] = [...history, { role: 'user', content: prompt }, { role: 'assistant', content: answer }];
      this.patch({ history: finalHistory, metrics: { ...this.snapshot.metrics, completedAt: performance.now() } });
      if (this.queue.size === 0) this.listen();
    } catch (error) {
      if (!this.cancellation.isCurrent(turn.id)) return;
      if (turn.signal.aborted) return;
      this.patch({ state: 'error', error: error instanceof Error ? error.message : 'Error inesperado del agente' });
    }
  }

  private enqueueChunk(chunk: string, id: number): void {
    if (!this.cancellation.isCurrent(id)) return;
    const metrics = this.snapshot.metrics;
    this.patch({ metrics: { ...metrics, firstChunkAt: metrics.firstChunkAt ?? performance.now(), chunks: metrics.chunks + 1 }, state: 'speaking' });
    if (this.bargeInTurn !== id) {
      this.bargeInTurn = id;
      void this.recognition.startBargeIn(() => this.interrupt(), (message) => {
        if (this.cancellation.isCurrent(id)) this.patch({ warning: message });
      });
    }
    this.queue.enqueue(chunk, () => {
      if (this.cancellation.isCurrent(id)) this.patch({ state: 'speaking', metrics: { ...this.snapshot.metrics, speechStartedAt: this.snapshot.metrics.speechStartedAt ?? performance.now() } });
    }, () => {
      if (this.cancellation.isCurrent(id)) {
        this.recognition.stopBargeIn();
        void this.listen();
      }
    }, (message) => {
      if (this.cancellation.isCurrent(id)) this.patch({ warning: message });
    });
  }

  private patch(partial: Partial<VoiceSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...partial };
    this.listeners.forEach((listener) => listener(this.snapshot));
  }
}
