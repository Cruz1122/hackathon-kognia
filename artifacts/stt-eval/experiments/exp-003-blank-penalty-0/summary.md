# exp-003-blank-penalty-0

| Audio | Escenario | WER | CER | Semántica | Partial latency | Final latency | Resultado |
|---|---|---:|---:|---:|---:|---:|---|
| audio-001 | office_noise | 0.944 | 0.827 | 0.799 | 2800.0 / 159.92 | 71.3 | critical |
| audio-002 | whisper | 0.077 | 0.018 | 0.952 | 2800.0 / 134.12 | 80.1 | pass |
| audio-003 | chopped | 0.143 | 0.118 | 0.990 | 2800.0 / 152.76 | 79.8 | pass |
| audio-004 | nightclub | 0.474 | 0.159 | 0.963 | 2800.0 / 143.24 | 72.0 | weak |
| audio-005 | distortion | 0.000 | 0.000 | 0.994 | 2800.0 / 149.41 | 77.3 | pass |
| audio-006 | distortion | 0.667 | 0.455 | 0.898 | 2800.0 / 140.67 | 70.0 | critical |

```json
{
  "n": 6,
  "mean_wer": 0.3841,
  "mean_cer": 0.2626,
  "mean_semantic": 0.9327,
  "critical_count": 2,
  "mean_tail_ms": 75.08,
  "mean_audio_ms_to_first_partial": 2800.0
}
```
