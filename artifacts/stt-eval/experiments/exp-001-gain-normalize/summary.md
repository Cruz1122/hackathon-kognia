# exp-001-gain-normalize

| Audio | Escenario | WER | CER | Semántica | Partial latency | Final latency | Resultado |
|---|---|---:|---:|---:|---:|---:|---|
| audio-001 | office_noise | 0.778 | 0.691 | 0.831 | 2800.0 / 159.04 | 63.8 | critical |
| audio-002 | whisper | 0.077 | 0.018 | 0.945 | 2800.0 / 186.75 | 80.8 | pass |
| audio-003 | chopped | 0.429 | 0.118 | 0.974 | 2800.0 / 141.43 | 76.8 | weak |
| audio-004 | nightclub | 0.474 | 0.159 | 0.963 | 2800.0 / 160.29 | 82.1 | weak |
| audio-005 | distortion | 0.000 | 0.000 | 1.000 | 2800.0 / 149.15 | 72.4 | pass |
| audio-006 | distortion | 0.333 | 0.218 | 0.922 | 2800.0 / 143.64 | 70.0 | weak |

```json
{
  "n": 6,
  "mean_wer": 0.3484,
  "mean_cer": 0.2005,
  "mean_semantic": 0.9393,
  "critical_count": 1,
  "mean_tail_ms": 74.3,
  "mean_audio_ms_to_first_partial": 2800.0
}
```
