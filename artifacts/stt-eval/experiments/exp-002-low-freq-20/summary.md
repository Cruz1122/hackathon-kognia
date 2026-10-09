# exp-002-low-freq-20

| Audio | Escenario | WER | CER | Semántica | Partial latency | Final latency | Resultado |
|---|---|---:|---:|---:|---:|---:|---|
| audio-001 | office_noise | 1.000 | 0.642 | 0.857 | 2800.0 / 173.55 | 77.2 | critical |
| audio-002 | whisper | 0.231 | 0.088 | 0.947 | 2800.0 / 160.59 | 77.4 | pass |
| audio-003 | chopped | 0.143 | 0.118 | 0.990 | 2800.0 / 156.46 | 77.7 | pass |
| audio-004 | nightclub | 0.474 | 0.207 | 0.959 | 2800.0 / 138.12 | 72.0 | weak |
| audio-005 | distortion | 0.000 | 0.000 | 0.994 | 2800.0 / 170.68 | 80.0 | pass |
| audio-006 | distortion | 0.417 | 0.236 | 0.930 | 2800.0 / 156.67 | 67.5 | weak |

```json
{
  "n": 6,
  "mean_wer": 0.3774,
  "mean_cer": 0.2152,
  "mean_semantic": 0.9461,
  "critical_count": 1,
  "mean_tail_ms": 75.29,
  "mean_audio_ms_to_first_partial": 2800.0
}
```
