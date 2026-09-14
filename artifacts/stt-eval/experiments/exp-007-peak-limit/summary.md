# exp-007-peak-limit

| Audio | Escenario | WER | CER | Semántica | Partial latency | Final latency | Resultado |
|---|---|---:|---:|---:|---:|---:|---|
| audio-001 | office_noise | 0.944 | 0.679 | 0.836 | 2800.0 / 159.01 | 75.0 | critical |
| audio-002 | whisper | 0.077 | 0.018 | 0.952 | 2800.0 / 109.18 | 52.8 | pass |
| audio-003 | chopped | 0.143 | 0.118 | 0.990 | 2800.0 / 135.67 | 56.5 | pass |
| audio-004 | nightclub | 0.474 | 0.159 | 0.963 | 2800.0 / 110.2 | 54.6 | weak |
| audio-005 | distortion | 0.000 | 0.000 | 0.994 | 2800.0 / 108.32 | 54.0 | pass |
| audio-006 | distortion | 0.667 | 0.564 | 0.870 | 2800.0 / 106.21 | 54.5 | critical |

```json
{
  "n": 6,
  "mean_wer": 0.3841,
  "mean_cer": 0.256,
  "mean_semantic": 0.9343,
  "critical_count": 2,
  "mean_tail_ms": 57.91,
  "mean_audio_ms_to_first_partial": 2800.0
}
```
