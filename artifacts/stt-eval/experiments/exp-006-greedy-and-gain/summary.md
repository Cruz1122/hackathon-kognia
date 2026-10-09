# exp-006-greedy-and-gain

| Audio | Escenario | WER | CER | Semántica | Partial latency | Final latency | Resultado |
|---|---|---:|---:|---:|---:|---:|---|
| audio-001 | office_noise | 0.944 | 0.679 | 0.811 | 2800.0 / 109.79 | 49.4 | critical |
| audio-002 | whisper | 0.077 | 0.018 | 0.945 | 2800.0 / 111.66 | 56.6 | pass |
| audio-003 | chopped | 0.429 | 0.118 | 0.974 | 2800.0 / 109.84 | 54.3 | weak |
| audio-004 | nightclub | 0.474 | 0.171 | 0.960 | 2800.0 / 124.11 | 66.5 | weak |
| audio-005 | distortion | 0.000 | 0.000 | 1.000 | 2800.0 / 192.28 | 64.4 | pass |
| audio-006 | distortion | 0.417 | 0.255 | 0.931 | 2800.0 / 126.36 | 59.0 | weak |

```json
{
  "n": 6,
  "mean_wer": 0.3901,
  "mean_cer": 0.2066,
  "mean_semantic": 0.9369,
  "critical_count": 1,
  "mean_tail_ms": 58.35,
  "mean_audio_ms_to_first_partial": 2800.0
}
```
