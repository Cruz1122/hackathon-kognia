# STT Optimization Report

Dataset: `tests/fixtures/stt/` (n=6). Harness: `backend/scripts/evaluate_stt.py` (PCM 16 kHz, frames 100 ms, `feed_pcm` + `finish_stream`). Proveedor: Sherpa-ONNX Zipformer ES Kroko (`sherpa-onnx-streaming-zipformer-es-kroko-2025-08-06`) vía `SpeechToTextProvider`.

Partial latency = `audio_ms_to_first_partial` (espera percibida en streaming). Final latency = `tail_ms` (último chunk → `finish_stream`). Un clip de 8 s no se juzga contra el presupuesto de 1500 ms por su duración.

---

## 1. Baseline

Configuración de producción previa: `modified_beam_search`, `max_active_paths=4`, `blank_penalty=0.4`, `temperature_scale=1.2`, `low_freq=80`, `finish_padding_seconds=0.5`. Sin preprocess. VAD de energía/pitch solo para barge-in live, no entra al reconocedor.

| Audio | Escenario | WER | CER | Semántica | Partial (audio/wall ms) | tail_ms | Resultado |
|---|---|---:|---:|---:|---|---:|---|
| audio-001 | office_noise | 1.000 | 0.667 | 0.848 | 2800 / 173 | 65 | critical |
| audio-002 | whisper | 0.077 | 0.018 | 0.952 | 2800 / 151 | 70 | pass |
| audio-003 | chopped | 0.143 | 0.118 | 0.990 | 2800 / 166 | 79 | pass |
| audio-004 | nightclub | 0.474 | 0.159 | 0.963 | 2800 / 138 | 72 | weak |
| audio-005 | distortion | 0.000 | 0.000 | 0.994 | 2800 / 154 | 81 | pass |
| audio-006 | distortion | 0.667 | 0.455 | 0.898 | 2800 / 153 | 79 | critical |

Agregados: mean WER **0.393**, mean CER **0.236**, mean semantic **0.941**, críticos **2/6**, mean tail **75 ms**, primer partial siempre **2800 ms** de audio.

Diagnóstico:

- **001 oficina** — acústico + clipping (`peak=1.0`, RMS 0.165). Hipótesis ilegible. Semántica 0.85 no salva el WER.
- **002 susurro** — no era el fallo; WER 0.08. RMS 0.032 basta para este modelo.
- **003 entrecortado** — aceptable (omite “esta”).
- **004 discoteca** — acústico: “agente”→“la gente”, “discoteca”→“discotecnia”. Semántica alta.
- **005 vs 006** — mismo oracle; 005 peak 0.30 perfecto, 006 peak 0.97 crítico. Distorsión + saturación, no el texto.
- **Partial 2800 ms** — no es silencio inicial (voz desde 0–1200 ms). Delay del Zipformer streaming (chunk de encoder), no del beam. Wall-clock sí es faster-than-realtime (~150 ms CPU hasta el primer texto).
- **tail** — dentro de 1500 ms (ideal 1000). El +800 ms de `CALL_SILENCE_SECONDS` en `/ws/call` es producto, no del reconocedor.

---

## 2. Experimentos realizados

| Exp | Cambio | Decisión |
|---|---|---|
| baseline | producción | referencia |
| exp-001-gain-normalize | RMS 0.10 | REJECT (rompe chopped) |
| exp-002-low-freq-20 | `low_freq` 80→20 (default Sherpa) | REJECT (susurro WER ×3) |
| exp-003-blank-penalty-0 | `blank_penalty` 0.4→0.0 (default Sherpa) | REJECT (CER/semántica oficina) |
| exp-004-greedy-search | `greedy_search` (default `from_transducer`) | KEEP |
| exp-005-beam-paths-8 | `max_active_paths` 8 | REJECT (no gana a greedy) |
| exp-006-greedy-and-gain | greedy + gain | REJECT |
| exp-007-peak-limit | peak≤0.80 | REJECT |
| exp-008-greedy-and-peak-limit | greedy + peak | REJECT |

Detalle y tablas: `artifacts/stt-eval/experiments/<name>/`.

---

## 3. Cambios aplicados

En [`backend/app/features/transcription/service.py`](backend/app/features/transcription/service.py):

- `SHERPA_CONFIG` + `build_recognizer()` / `apply_config()` para eval sin plugins.
- **KEEP:** `decoding_method`: `modified_beam_search` → **`greedy_search`**.
- `decode_audio_file` público (ffmpeg 16 kHz s16le).
- `finish_stream` usa `finish_padding_seconds` del dict (sigue 0.5 s).

No se movió preprocess (gain/peak) a producción. No se tocó VAD live ni `CALL_SILENCE_SECONDS`.

---

## 4. Evidencia técnica

- [Sherpa-ONNX `OnlineRecognizer.from_transducer`](https://github.com/k2-fsa/sherpa-onnx/blob/master/sherpa-onnx/python/sherpa_onnx/online_recognizer.py): default `decoding_method="greedy_search"`, `low_freq=20`, `blank_penalty=0.0`. `temperature_scale` solo afecta confianza, no los logits.
- `blank_penalty` (comentario C++): valor más alto → menos deleciones / más inserciones.
- Latencia de streaming Zipformer la fija el chunk exportado (~160–320 ms teóricos en docs k2-fsa); el primer token útil aquí aparece a **2.8 s de audio** de forma estable. greedy no lo adelantó.
- Kroko/Banafo: modelo comunitario Zipformer sobre Sherpa; no documentan AGC. Whisper sigue descartado para este path realtime.

---

## 5. Métricas antes/después

Después = exp-004 (config KEEP), n=6.

| Métrica | Baseline (beam) | KEEP (greedy) |
|---|---:|---:|
| mean WER | 0.393 | **0.371** |
| mean CER | 0.236 | 0.244 |
| mean semantic | 0.941 | 0.940 |
| críticos | 2/6 | 2/6 |
| whisper WER | 0.077 | **0.000** |
| oficina WER | 1.000 | 0.944 |
| mean tail_ms | 75 | 72 |
| audio_ms primer partial | 2800 | 2800 |

Clips 003/004/005/006: sin cambio material de WER. CER discoteca 0.159→0.171 (aceptable).

---

## 6. Configuración final recomendada

```text
decoding_method=greedy_search
max_active_paths=4          # ignorado por greedy; se deja por si se vuelve a beam
blank_penalty=0.4
temperature_scale=1.2
low_freq=80
finish_padding_seconds=0.5
sample_rate=16000
provider=cpu
```

No AGC/limiter global: mejora 006 y empeora entrecortado.

---

## 7. Limitaciones actuales

- n=6 no generaliza (oficina, club, distorsión saturada).
- **001 y 006 siguen críticos.** Causa probable: clipping + ruido; el Zipformer comunitario no separa voz. Un modelo Kroko comercial o fine-tune de ruido ayudaría más que knobs.
- Primer partial a 2800 ms de audio **fuera del gate de 500 ms**. No se corrigió con greedy/beam/gain. Siguiente palanca: otro checkpoint con chunk más corto, no más `decoding_method`.
- El harness no incluye red ni el silencio de 0.8 s del WS; `conversational_final_est_ms` ≈ 870 ms (silencio + tail).
- Semántica E5 puede quedar alta con transcripciones inservibles (001). No usar sola como gate.

Criterio de parada: dataset evaluado; latencia de cola OK; semántica media alta; **errores críticos de oficina/distorsión saturada no resueltos**; el resto de knobs fue marginal o regresivo.

---

## 8. Próximos escenarios de prueba

- Más clips de oficina y distorsión **sin** saturar el int16; y los mismos con saturación etiquetada.
- Micrófono bajo + AGC del browser (`autoGainControl` hoy está en `false` en capture).
- Overlap de TTS / barge-in (no está en este golden set).
- Checkpoint Zipformer chunk-8 (o Kroko commercial) midiendo `audio_ms_to_first_partial`.
- Hotwords de dominio (“agente”, “discoteca”) solo con corpus más grande; aquí sería overfit.
