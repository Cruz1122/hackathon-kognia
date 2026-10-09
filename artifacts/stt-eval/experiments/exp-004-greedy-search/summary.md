# exp-004-greedy-search

| Audio | Escenario | WER | CER | Semántica | Partial latency | Final latency | Resultado |
|---|---|---:|---:|---:|---:|---:|---|
| audio-001 | office_noise | 0.944 | 0.741 | 0.826 | 2800.0 / 159.68 | 64.6 | critical |
| audio-002 | whisper | 0.000 | 0.000 | 0.970 | 2800.0 / 141.07 | 73.1 | pass |
| audio-003 | chopped | 0.143 | 0.118 | 0.990 | 2800.0 / 135.02 | 72.8 | pass |
| audio-004 | nightclub | 0.474 | 0.171 | 0.960 | 2800.0 / 128.93 | 72.6 | weak |
| audio-005 | distortion | 0.000 | 0.000 | 0.994 | 2800.0 / 149.52 | 66.7 | pass |
| audio-006 | distortion | 0.667 | 0.436 | 0.900 | 2800.0 / 147.29 | 80.9 | critical |

```json
{
  "n": 6,
  "mean_wer": 0.3713,
  "mean_cer": 0.2442,
  "mean_semantic": 0.94,
  "critical_count": 2,
  "mean_tail_ms": 71.79,
  "mean_audio_ms_to_first_partial": 2800.0
}
```
