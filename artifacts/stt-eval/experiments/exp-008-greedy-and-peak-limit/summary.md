# exp-008-greedy-and-peak-limit

| Audio | Escenario | WER | CER | Semántica | Partial latency | Final latency | Resultado |
|---|---|---:|---:|---:|---:|---:|---|
| audio-001 | office_noise | 0.944 | 0.605 | 0.831 | 2800.0 / 158.26 | 75.8 | critical |
| audio-002 | whisper | 0.000 | 0.000 | 0.970 | 2800.0 / 98.36 | 48.7 | pass |
| audio-003 | chopped | 0.429 | 0.059 | 0.945 | 2800.0 / 98.43 | 53.3 | weak |
| audio-004 | nightclub | 0.474 | 0.171 | 0.960 | 2800.0 / 98.05 | 52.7 | weak |
| audio-005 | distortion | 0.000 | 0.000 | 0.994 | 2800.0 / 103.71 | 50.2 | pass |
| audio-006 | distortion | 0.667 | 0.455 | 0.869 | 2800.0 / 111.11 | 52.4 | critical |

```json
{
  "n": 6,
  "mean_wer": 0.4189,
  "mean_cer": 0.2148,
  "mean_semantic": 0.928,
  "critical_count": 2,
  "mean_tail_ms": 55.52,
  "mean_audio_ms_to_first_partial": 2800.0
}
```
