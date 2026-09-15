# STT Optimization Report

Dataset: `tests/fixtures/stt/` (n=13; 001–006 originales + 007–013 del zip). Harness: `backend/scripts/evaluate_stt.py` (PCM 16 kHz, frames 100 ms, `feed_pcm` + `finish_stream`). Proveedor: Sherpa-ONNX vía `SpeechToTextProvider`.

**Producción (default):** NVIDIA Nemotron 3.5 streaming 0.6b 560 ms int8 (`backend/models/sherpa-nemotron-35-560`), `language=es`, sin hot-frame. KEEP de intención: exp-023. Juez **6/6**. Primer partial ~2.6 s (misma clase que Kroko). Tail ~92 ms.

**Criterio de calidad (desde exp-029):** LLM-as-a-judge (Gemini 3.5 flash-lite). El agente ¿entiende el pedido? Spacing/homófonos OK. Alucinación o “Hola.” vacío = fail. E5 no basta (Kroko 001 tenía E5 0.87 e ilegible).

**GOAT único (calidad + latencia en un modelo):** Nemotron 560. Gana a Kroko en 001/006 sin ir más lento. Canary es más rápido (400 ms) y alucina 001 — no es el GOAT.

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

- **001 oficina** — acústico + ruido (`peak=1.0`, RMS 0.165). El clipping real es irrelevante: 2 runs, máx. 4 samples (0.2 ms). Con Kroko la hipótesis es ilegible. Semántica 0.85 no salva el WER.
- **002 susurro** — no era el fallo; WER 0.08. RMS 0.032 basta para este modelo.
- **003 entrecortado** — aceptable (omite “esta”). 251 clip runs: los picos **son** el habla.
- **004 discoteca** — acústico: “agente”→“la gente”, “discoteca”→“discotecnia”. Semántica alta.
- **005 vs 006** — mismo oracle; 005 peak 0.30 perfecto, 006 peak 0.97 crítico. Distorsión a nivel medio (1 frame con peak>0.95), no el texto.
- **Partial 2800 ms** — no es silencio inicial (voz desde 0–1200 ms). Encoder Kroko: `T=141`, `decode_chunk_len=128` (~1.28 s/chunk). ~2 chunks hasta el primer token ≈ 2.8 s. Wall-clock sí es faster-than-realtime.
- **tail** — dentro de 1500 ms (ideal 1000). El +800 ms de `CALL_SILENCE_SECONDS` en `/ws/call` es producto, no del reconocedor.

---

## 2. Experimentos realizados

| Exp | Cambio | Decisión |
|---|---|---|
| baseline | producción beam | referencia |
| exp-001-gain-normalize | RMS 0.10 global | REJECT (rompe chopped) |
| exp-002-low-freq-20 | `low_freq` 80→20 | REJECT (susurro WER ×3) |
| exp-003-blank-penalty-0 | `blank_penalty` 0.4→0.0 | REJECT (CER/semántica oficina) |
| exp-004-greedy-search | `greedy_search` | KEEP |
| exp-005-beam-paths-8 | `max_active_paths` 8 | REJECT (no gana a greedy) |
| exp-006-greedy-and-gain | greedy + gain | REJECT |
| exp-007-peak-limit | peak≤0.80 global | REJECT |
| exp-008-greedy-and-peak-limit | greedy + peak | REJECT |
| exp-009-hot-peak-limit | frames 100 ms peak>0.95 → 0.90 | REJECT (001 peor, 003 weak, 006 igual) |
| exp-010-high-freq-6000 | `high_freq=6000` | PARTIAL (006 sale de crítico; rompe sibilantes 002) |
| exp-011-high-freq-7000 | `high_freq=7000` | REJECT (rompe chopped) |
| exp-012-dither | `dither=0.00003` | REJECT (rompe chopped) |
| exp-013-sparse-clip-gain | AGC si peak>0.95 y clip_frac<0.001 | PARTIAL (mejor 006; no streamable) |
| exp-014-hot-frame-rms | RMS 0.10 en frames peak>0.95 | KEEP (harness) |
| exp-015-keep-in-feed-pcm | mismo en feed_pcm float32 | REJECT (chopped “es”→“if”) |
| exp-016-hot-frame-int16 | feed_pcm + round-trip int16 | KEEP (producción Kroko) |
| exp-017-nemo-ctc-es-local | NeMo CTC ES int8 local | PARTIAL (006 pass; rompe 002/003) |
| exp-018-moonshine-base-es-local | Moonshine v2 ES local | REJECT (001/004 peores; tail ~4 s) |
| exp-019-canary-180m-es-local | Canary 180m flash local | PARTIAL (006 pass; partial 400 ms; 001 ilegible; 003 WER por spacing) |
| exp-020-parakeet-tdt-v3-local | Parakeet TDT 0.6b v3 local (offline) | PARTIAL (006 exacto; 004 pass; 001 = “Hola.”) |
| exp-021-afftdn-kroko | ffmpeg afftdn + Kroko | REJECT (001 igual; 006 vuelve a crítico) |
| exp-022-arnndn-kroko | RNNoise arnndn + Kroko | REJECT (empeora 001/003/004/006) |
| exp-023-nemotron-streaming-es | Nemotron 3.5 streaming 560 ms, `language=es` | KEEP (producción; judge 6/6) |
| exp-024-nemotron-lang-es-es | mismo, `language=es-ES` | REJECT (001 “Lord of Sin”) |
| exp-025-nemotron-hot-frame | Nemotron + hot-frame KEEP | REJECT (001 vuelve a crítico) |
| exp-026-nemotron-blank-0 | Nemotron `blank_penalty=0` | REJECT (001 crítico; partial 7400 ms) |
| exp-027-nemotron-lang-es-us | `language=es-US` | REJECT (idéntico a `es`; no gana) |
| exp-028-nemotron-1120ms | Nemotron chunk 1120 ms | REJECT (001/003 críticos; 006 weak) |
| exp-029-nemotron-160ms | Nemotron chunk 160 ms + LLM judge | REJECT default (judge 6/6; partial sigue ~2 s; RTF≈1) |
| exp-030-nemotron-flush-2s | Partial especulativo + 2 s de ceros | REJECT (silencio ≠ 2 s de habla; wall 4–8 s) |
| exp-031-new-clips-nemotron | fixtures 007–013, mismo Nemotron 560 | KEEP fixtures; judge 11/13; críticos 007 restaurante y 013 solapamiento |

Detalle y tablas: `artifacts/stt-eval/experiments/<name>/`. Todos los checkpoints son **locales** (Sherpa-ONNX on-device; sin cloud). No se añadió Whisper/faster-whisper (segundo vendor descartado).

---

## 3. Cambios aplicados

En [`backend/app/features/transcription/service.py`](backend/app/features/transcription/service.py):

- `SHERPA_CONFIG` + `build_recognizer()` / `apply_config()` para eval sin plugins.
- **KEEP:** `decoding_method`: `modified_beam_search` → **`greedy_search`**.
- **KEEP:** `_compress_hot_frame` en `feed_pcm` / `transcribe_pcm`: si el chunk tiene peak>0.95, escala su RMS a 0.10 y cuantiza a int16. No es AGC global: 002/004/005 (peak<0.95) no se tocan. **Solo con Kroko**; en Nemotron es REJECT (exp-025).
- `high_freq=-400` y `dither=0.0` quedan explícitos (defaults Sherpa; no son KEEP de calidad).
- `decode_audio_file` público (ffmpeg 16 kHz s16le).
- `finish_stream` usa `finish_padding_seconds` del dict (sigue 0.5 s).
- Loaders **experimentales** (default sigue Kroko): `offline_nemo_ctc`, `offline_nemo_transducer`, `offline_moonshine`, `offline_nemo_canary` + buffer `_OfflinePcmBuffer` (re-decode cada `offline_partial_seconds`).
- **KEEP producción:** Nemotron 560, `language=es`, `hot_frame_peak_gate=0`, `SHERPA_MODEL_DIR=backend/models/sherpa-nemotron-35-560`.
- **KEEP Kroko (ya no default):** `decoding_method=greedy_search` y `_compress_hot_frame` si alguien vuelve a Zipformer.
- Loaders **experimentales:** `offline_nemo_ctc`, `offline_nemo_transducer`, `offline_moonshine`, `offline_nemo_canary`.
- **Harness:** LLM-as-a-judge en `evaluate_stt.py`.

No se tocó VAD live ni `CALL_SILENCE_SECONDS`. `speculative_flush_seconds` queda en 0 (exp-030 REJECT).

---

## 4. Evidencia técnica

- [Sherpa-ONNX `OnlineRecognizer.from_transducer`](https://github.com/k2-fsa/sherpa-onnx/blob/master/sherpa-onnx/python/sherpa_onnx/online_recognizer.py): default `decoding_method="greedy_search"`, `low_freq=20`, `high_freq=-400`, `dither=0.0`. **No hay limiter ni AGC.**
- Icefall Zipformer: `decode_chunk_len` default 32 = 320 ms. El encoder Kroko ES tiene **`decode_chunk_len=128`, `T=141`**. Primer texto útil estable a **2800 ms** (~2 chunks). [sherpa-onnx #3715](https://github.com/k2-fsa/sherpa-onnx/discussions/3715).
- Clipping PCM: 001 frac≥0.95 = 0.02 %; 006 ≈ 0 frames saturados; 003 = 7 % de frames calientes (el habla entrecortada).
- Parakeet TDT 0.6b v3 en sherpa-onnx es **offline** (`OfflineRecognizer`, simulated streaming). El sucesor NVIDIA **streaming** con ES nativo es [nemotron-3.5-asr-streaming-0.6b](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b) exportado a [sherpa-onnx 560 ms int8](https://k2-fsa.github.io/sherpa/onnx/nemo/nemotron-streaming.html). `language` per-stream (`es`, `es-ES`, `es-US`, `auto`). sherpa-onnx **1.13.8** lo carga.
- Offline locales (int8/ORT, CPU): NeMo CTC ES, Moonshine base ES, Canary 180m flash, Parakeet TDT 0.6b v3. Canary/Parakeet **sí** transcriben 006; ninguno dejaba 001 fuera de crítico hasta Nemotron.
- Denoise local no es el fallo de 001 en Kroko: ffmpeg `afftdn` y RNNoise `arnndn` no producen texto usable.
- Hotwords Sherpa solo con `modified_beam_search` (**NEEDS MORE DATA**).
- Chunk Nemotron 1120 ms (docs: más accuracy) **empeora** este golden set (exp-028). Chunk **160 ms** (exp-029) tampoco adelanta el primer token al gate de 500 ms: 1900–3000 ms de audio y wall ≈ duración (RTF~1). Locale `es-ES` alucina inglés en 001. Hot-frame Kroko y `blank_penalty=0` también.
- El primer token Nemotron es un delay de **emisión**, no de tamaño de chunk. Canary re-decodifica el buffer cada 400 ms y por eso sí pasa el gate; 001 es alucinación.
- **Ceros sintéticos no vale** (exp-030): 2 s de silencio al instante en un fork no adelanta el texto a 400 ms. El encoder quiere ~2 s de **voz**. Meter ese silencio en el stream live partiría las palabras. `finish_padding_seconds=0.5` ya rellena al final y no mueve el primer partial (los clips duran >2 s).

---

## 5. Métricas antes/después

Producción = exp-016 (greedy + hot-frame RMS int16). n=6. Mejor eval 001+006 = exp-023 (Nemotron 560 ms, `language=es`, sin hot-frame).

| Métrica | Baseline (beam) | greedy (exp-004) | KEEP Kroko (exp-016) | Nemotron 560 (exp-023) |
|---|---:|---:|---:|---:|
| mean WER | 0.393 | 0.371 | 0.325 | **0.243** |
| mean CER | 0.236 | 0.244 | 0.226 | **0.110** |
| mean semantic | 0.941 | 0.940 | 0.949 | **0.968** |
| críticos | 2/6 | 2/6 | 1/6 | **0/6** |
| whisper WER | 0.077 | 0.000 | **0.000** | **0.000** |
| chopped WER | 0.143 | 0.143 | **0.143 pass** | 0.429 weak |
| oficina WER | 1.000 | 0.944 | 0.833 crit | **0.444 weak** |
| distorsión-006 WER | 0.667 | 0.667 | 0.500 weak | **0.083 pass** |
| distorsión-005 WER | 0.000 | 0.000 | **0.000** | 0.083 pass |
| discoteca WER | 0.474 | 0.474 | 0.474 | 0.421 |
| mean tail_ms | 75 | 72 | 99 | **92** |
| audio_ms primer partial | 2800 | 2800 | 2800 | 2600 |

| Audio | KEEP Kroko | Kroko resultado | Nemotron 023 | Nemotron resultado | Nemotron hipótesis (resumen) |
|---|---:|---|---:|---|---|
| 001 oficina | 0.833 | critical | **0.444** | **weak** | “Hola, se utilizando mi agente de audio… Loci” |
| 002 susurro | 0.000 | pass | 0.000 | pass | intacto |
| 003 chopped | 0.143 | pass | 0.429 | weak | “entre cortado” (CER 0.118; omite “esta” igual que Kroko) |
| 004 discoteca | 0.474 | weak | 0.421 | weak | “la gente” / cola “puede ser desde el” |
| 005 distorsión | 0.000 | pass | 0.083 | pass | “esta”→“eso” |
| 006 distorsión | 0.500 | weak | **0.083** | **pass** | “esta”→“eso” |

Modelos locales vs KEEP Kroko (solo Nemotron 023 saca 001 de crítico **y** 006 a pass **y** 002 a pass):

| | KEEP Kroko | NeMo CTC | Moonshine | Canary 180m | Parakeet v3 | **Nemotron 560** |
|---|---:|---:|---:|---:|---:|---:|
| mean WER | 0.325 | 0.404 | 0.395 | 0.294 | 0.301 | **0.243** |
| 001 oficina | 0.833 crit | 0.944 crit | 0.889 crit | 0.667 crit | 0.944 crit | **0.444 weak** |
| 006 distorsión | 0.500 weak | **0.083 pass** | 0.333 weak | **0.083 pass** | **0.000 pass** | **0.083 pass** |
| 002 susurro | **0.000** | 0.462 | 0.462 | 0.077 | 0.077 | **0.000** |
| 003 chopped | **0.143 pass** | 0.429 | **0.000** | 0.571 (CER 0) | 0.571 | 0.429 weak |
| 004 discoteca | 0.474 | 0.421 | 0.684 crit | 0.368 | **0.210 pass** | 0.421 |
| críticos | 1/6 | 1/6 | — | 2/6 | 2/6 | **0/6** |
| partial audio ms | 2800 | 400–800 | ~2000 | **400** | ~1200 | 2600 |
| mean tail_ms | **99** | 288 | 3882 | 1143 | 924 | **92** |

LLM-as-a-judge (pedido usable, no WER perfecto). Gemini 3.5 flash-lite.

| Sistema | Judge usable | 001 | Partial audio | tail | CPU primer partial |
|---|---:|---|---:|---:|---|
| Kroko KEEP | 5/6 | FAIL ilegible | 2800 | 99 | wall ~150 ms |
| **Nemotron 560** | **6/6** | **PASS** | 2600 | **92** | wall ~550–750 ms (RTF ~0.25) |
| Nemotron 160 | **6/6** | PASS (+“oficina”) | 1900–3000 | 507 | wall ~1.7–3.0 s (RTF ~1) |
| Canary 180m | 5/6 | FAIL “video de aluminio” | **400** | 1143 | OK |
| Parakeet v3 | 5/6 | FAIL “Hola.” | ~1200 | 924 | OK |

003 “entre cortado” es **PASS** de juez en Nemotron y Canary. Promover Nemotron ya no está bloqueado por WER de 003.

No existe un checkpoint local que sea 6/6 en 001 **y** primer partial &lt; 500 ms.

---

## 6. Configuración final recomendada

Producción / demo (exp-023 KEEP):

```text
SHERPA_MODEL_DIR=backend/models/sherpa-nemotron-35-560
backend=online_transducer
language=es
hot_frame_peak_gate=0
blank_penalty=0.4
decoding_method=greedy_search
finish_padding_seconds=0.5
speculative_flush_seconds=0
```

Kroko queda en `backend/models/sherpa-es` por si hace falta rollback (más ligero, peor 001).

No AGC/limiter global. No chunk 160/1120 ms. No `language=es-ES`. No hot-frame sobre Nemotron. No Whisper. No flush de ceros.

---

## 7. Limitaciones actuales

- n=13 (exp-031): Nemotron 560 judge **11/13**. Críticos nuevos: **007 restaurante** (“la ombra… Roque”) y **013 solapamiento** (el juez pide un pedido al agente; el STT sí saca Londres/cinco años con errores). 010 dígitos: WER alto porque el modelo deletrea (“dos mil catorce” vs “2014”) — juez PASS. 012 ruido blanco: silencio correcto, sin partial.
- **Objetivo 001+006 fuera de crítico: cumplido con Nemotron 560 ms (exp-023), no con Kroko.** Hipótesis 001:

| Sistema | 001 hipótesis | Resultado |
|---|---|---|
| Kroko KEEP | ilegible (“santo día lucín… ocasional”) | critical 0.833 |
| NeMo CTC | ilegible (WER 0.944) | critical |
| Moonshine | “Hoy en mi cola… 10, 12…” | critical |
| Canary | “utilizó un video de aluminio…” | critical |
| Parakeet TDT v3 (offline) | “Hola.” | critical |
| Kroko+afftdn | “el vídeo de mis anomía… ocasional” | critical |
| Kroko+RNNoise | WER 0.944 | critical |
| Canary/Parakeet + denoise | “Hola.” / alucinaciones | critical |
| **Nemotron 560 `language=es`** | **“Hola, se utilizando mi agente de audio… Loci”** | **weak 0.444** |
| Nemotron `es-ES` | “Lo que digo, Lord of Sin” | critical 0.833 |
| Nemotron + hot-frame | “Sylvie de adulte… sombrero ofici” | critical 0.667 |
| Nemotron `blank_penalty=0` | “Lo que digo en sombre oficina” | critical 0.722 |
| Nemotron 1120 ms | “se utiliza no mía de agente… oficina” | critical 0.611 |

  001 **no es** un techo del set comunitario: un checkpoint streaming NVIDIA lo deja débil pero usable. Kroko/Zipformer y los offline ya medidos **sí** son techo para 001. Falta “estoy”, “TTS” y “ambiente de oficina” (cierra en “Loci”). WER 0.444 no llega a pass (≤0.25).
- **006** pass con Nemotron/Canary/Parakeet/CTC. Kroko se queda en 0.50 weak. Distorsión saturada es límite de **Kroko**, no del audio.
- **003** con Nemotron: semántica alta, CER ~Kroko; WER sube porque tokeniza “entrecortado”→“entre cortado”. Kroko omite “esta” y sigue pass (WER 0.143). No hay knob KEEP que una el compuesto sin más datos.
- Primer partial: Kroko 2800 ms, Nemotron 560 **2600 ms**, Nemotron 160 **1900–3000 ms** (wall RTF≈1). **Canary 400 ms** es el único bajo 500, con 001 alucinado. El delay Nemotron es de emisión (~2 s de contexto), no se arregla bajando el chunk.
- Discoteca 004: homófonos; juez PASS en Nemotron 560. Hotwords = **NEEDS MORE DATA**.
- E5 ≥ 0.85 no implica intención (Kroko 001). Usar el juez.

Criterio de parada: dataset + juez evaluados. **GOAT de intención = Nemotron 560 (6/6).** No hay GOAT único de intención+latencia conversacional en el set comunitario local. Siguiente palanca real: encoder que emita tokens antes de 500 ms **sin** alucinar 001 (fine-tune / otro checkpoint), o cascada Canary-parcial + Nemotron-final (dos modelos, fuera de un experimento de un knob).

---

## 8. Próximos escenarios de prueba

- Kroko sigue en disco para rollback. Canary no entra: 400 ms de partial no compensan alucinar 001.
- Encoder streaming ES que emita &lt; 500 ms **sin** alucinar oficina (no más chunks 80/160/1120 de este Nemotron).
- Cascada opcional: Canary para captions rápidos + Nemotron al `finish_stream` (~850 MB, dos modelos).
- Restaurante con SNR etiquetado (007 es el fallo acústico nuevo).
- Dos hablantes / solapamiento con diarización o prompt de juez distinto (013 no es un pedido al agente).
- Más clips de oficina con SNR etiquetado.
- No repetir: gain, peak, dither, high_freq 6–7 kHz, afftdn, arnndn, Moonshine default, Nemotron `es-ES` / hot-frame / blank 0 / 160 / 1120 / flush de ceros, Whisper segundo vendor.
