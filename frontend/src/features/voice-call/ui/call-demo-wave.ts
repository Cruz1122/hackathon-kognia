import { paintCallWave, resizeWave } from '../application/wave-mark';

type WaveAnalyser = {
  analyser: AnalyserNode;
  sampleRate: number;
};

type CallDemoWaveApi = {
  setLevels: (customer: number, agent: number) => void;
  connectAnalyser: (analyser: AnalyserNode, sampleRate?: number) => void;
  connectPlaybackAnalyser: (analyser: AnalyserNode, sampleRate?: number) => void;
  disconnectPlaybackAnalyser: () => void;
  disconnectAnalyser: () => void;
  setPlaying: (next: boolean) => void;
  resetLiveWave: () => void;
};

function clamp(value: number): number {
  return Math.max(0, Math.min(1, value));
}

function analyserLevel(source: WaveAnalyser | null, bins: Uint8Array | null): number {
  if (!source || !bins) return 0;
  try {
    source.analyser.getByteFrequencyData(bins);
  } catch {
    return 0;
  }
  let total = 0;
  for (const value of bins) total += value;
  return clamp((total / Math.max(1, bins.length)) / 150);
}

export function mountCallDemoWave(): () => void {
  const canvas = document.getElementById('waveCanvas');
  if (!(canvas instanceof HTMLCanvasElement)) return () => undefined;
  const context = canvas.getContext('2d');
  if (!context) return () => undefined;
  const waveShell = document.getElementById('waveShell');

  const customerPeaks: number[] = [.04];
  const agentPeaks: number[] = [0];
  let mic: WaveAnalyser | null = null;
  let playback: WaveAnalyser | null = null;
  let micBins: Uint8Array | null = null;
  let playbackBins: Uint8Array | null = null;
  let latestCustomer = .04;
  let latestAgent = 0;
  let playing = true;
  let frame = 0;
  let disposed = false;
  let elapsed = 0;
  let lastFrame = performance.now();
  let lastSample = lastFrame;

  function resize(): void {
    resizeWave(canvas, context);
  }

  function setLevels(customer: number, agent: number): void {
    if (disposed) return;
    latestCustomer = clamp(customer);
    latestAgent = clamp(agent);
    waveShell?.style.setProperty('--progress', '100%');
    waveShell?.classList.add('at-live-edge');
  }

  function appendSample(customer: number, agent: number): void {
    customerPeaks.push(clamp(customer));
    agentPeaks.push(clamp(agent));
    if (customerPeaks.length > 180) customerPeaks.shift();
    if (agentPeaks.length > 180) agentPeaks.shift();
  }

  function appendLiveSample(): void {
    const measuredCustomer = analyserLevel(mic, micBins);
    const measuredAgent = analyserLevel(playback, playbackBins);
    const hasAnalyser = Boolean(mic || playback);
    const simulated = .04 + Math.max(0, Math.sin(elapsed * 7.8) * .045 + Math.sin(elapsed * 3.7 + .8) * .028);
    appendSample(
      hasAnalyser ? Math.max(latestCustomer, measuredCustomer) : simulated,
      hasAnalyser ? Math.max(latestAgent, measuredAgent) : 0,
    );
  }

  function draw(now: number): void {
    const delta = Math.min(.05, (now - lastFrame) / 1000);
    lastFrame = now;
    elapsed += delta;
    if (playing && now - lastSample >= 33) {
      appendLiveSample();
      lastSample = now;
    }
    paintCallWave(context, canvas, customerPeaks, agentPeaks, 1, []);
    frame = window.requestAnimationFrame(draw);
  }

  const api: CallDemoWaveApi = {
    setLevels,
    connectAnalyser(analyser, sampleRate = 16000) {
      mic = { analyser, sampleRate };
      micBins = new Uint8Array(analyser.frequencyBinCount);
    },
    connectPlaybackAnalyser(analyser, sampleRate = 24000) {
      playback = { analyser, sampleRate };
      playbackBins = new Uint8Array(analyser.frequencyBinCount);
    },
    disconnectPlaybackAnalyser() {
      playback = null;
      playbackBins = null;
    },
    disconnectAnalyser() {
      mic = null;
      playback = null;
      micBins = null;
      playbackBins = null;
    },
    setPlaying(next) {
      playing = next;
    },
    resetLiveWave() {
      customerPeaks.length = 0;
      customerPeaks.push(.04);
      agentPeaks.length = 0;
      agentPeaks.push(0);
      latestCustomer = .04;
      latestAgent = 0;
      waveShell?.style.setProperty('--progress', '100%');
      waveShell?.classList.add('at-live-edge');
    },
  };

  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  resize();
  (window as Window & { callMonitorAudio?: CallDemoWaveApi }).callMonitorAudio = api;
  frame = window.requestAnimationFrame(draw);

  return () => {
    if (disposed) return;
    disposed = true;
    window.cancelAnimationFrame(frame);
    observer.disconnect();
    api.disconnectAnalyser();
    const current = (window as Window & { callMonitorAudio?: CallDemoWaveApi }).callMonitorAudio;
    if (current === api) delete (window as Window & { callMonitorAudio?: CallDemoWaveApi }).callMonitorAudio;
  };
}
