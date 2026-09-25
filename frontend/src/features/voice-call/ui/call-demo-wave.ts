type WaveAnalyser = {
  analyser: AnalyserNode;
  sampleRate: number;
};

type CallDemoWaveApi = {
  pushAmplitude: (value: number) => void;
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

  const samples: number[] = [.04];
  let mic: WaveAnalyser | null = null;
  let playback: WaveAnalyser | null = null;
  let micBins: Uint8Array | null = null;
  let playbackBins: Uint8Array | null = null;
  let playing = true;
  let frame = 0;
  let disposed = false;
  let width = 1;
  let height = 1;
  let dpr = 1;
  let elapsed = 0;
  let lastFrame = performance.now();
  let lastSample = lastFrame;

  function resize(): void {
    const rect = canvas.getBoundingClientRect();
    width = Math.max(1, rect.width);
    height = Math.max(1, rect.height);
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function pushAmplitude(value: number): void {
    if (disposed) return;
    samples.push(clamp(value));
    if (samples.length > 180) samples.shift();
    waveShell?.style.setProperty('--progress', '100%');
    waveShell?.classList.add('at-live-edge');
  }

  function drawWave(from: number, to: number, fillStyle: string): void {
    if (to <= from) return;
    context.beginPath();
    const baseline = height - 1;
    const span = Math.max(1, samples.length - 1);
    const start = Math.floor(from * span);
    const end = Math.min(span, Math.ceil(to * span));
    context.moveTo(start / span * width, baseline);
    for (let index = start; index <= end; index += 1) {
      const x = index / span * width;
      const amplitude = 5 + Math.pow(samples[index] ?? .04, 1.25) * height * .5;
      context.lineTo(x, baseline - amplitude);
    }
    context.lineTo(end / span * width, baseline);
    context.closePath();
    context.fillStyle = fillStyle;
    context.fill();
  }

  function draw(now: number): void {
    const delta = Math.min(.05, (now - lastFrame) / 1000);
    lastFrame = now;
    elapsed += delta;
    const measured = Math.max(analyserLevel(mic, micBins), analyserLevel(playback, playbackBins));
    if (playing && now - lastSample >= 33) {
      const simulated = .04 + Math.max(0, Math.sin(elapsed * 7.8) * .045 + Math.sin(elapsed * 3.7 + .8) * .028);
      pushAmplitude(mic || playback ? measured : simulated);
      lastSample = now;
    }
    context.clearRect(0, 0, width, height);
    drawWave(0, 1, 'rgba(65,65,65,.14)');
    drawWave(0, 1, '#f7c974');
    frame = window.requestAnimationFrame(draw);
  }

  const api: CallDemoWaveApi = {
    pushAmplitude,
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
      samples.length = 0;
      samples.push(.04);
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
