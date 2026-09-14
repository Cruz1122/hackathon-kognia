# baseline

| Audio | Escenario | WER | CER | Semántica | Partial latency | Final latency | Resultado |
|---|---|---:|---:|---:|---:|---:|---|
| audio-001 | office_noise | 1.000 | 0.667 | 0.848 | 2800.0 / 173.06 | 65.2 | critical |
| audio-002 | whisper | 0.077 | 0.018 | 0.952 | 2800.0 / 151.08 | 69.8 | pass |
| audio-003 | chopped | 0.143 | 0.118 | 0.990 | 2800.0 / 165.8 | 79.3 | pass |
| audio-004 | nightclub | 0.474 | 0.159 | 0.963 | 2800.0 / 137.95 | 72.4 | weak |
| audio-005 | distortion | 0.000 | 0.000 | 0.994 | 2800.0 / 153.88 | 81.4 | pass |
| audio-006 | distortion | 0.667 | 0.455 | 0.898 | 2800.0 / 153.21 | 78.9 | critical |

```json
{
  "n": 6,
  "mean_wer": 0.3934,
  "mean_cer": 0.2358,
  "mean_semantic": 0.9409,
  "critical_count": 2,
  "mean_tail_ms": 74.52,
  "mean_audio_ms_to_first_partial": 2800.0
}
```
