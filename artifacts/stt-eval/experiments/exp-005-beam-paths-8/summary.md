# exp-005-beam-paths-8

| Audio | Escenario | WER | CER | Semántica | Partial latency | Final latency | Resultado |
|---|---|---:|---:|---:|---:|---:|---|
| audio-001 | office_noise | 0.944 | 0.679 | 0.851 | 2800.0 / 183.66 | 81.8 | critical |
| audio-002 | whisper | 0.000 | 0.000 | 0.970 | 2800.0 / 152.09 | 77.3 | pass |
| audio-003 | chopped | 0.143 | 0.118 | 0.990 | 2800.0 / 174.54 | 72.1 | pass |
| audio-004 | nightclub | 0.474 | 0.171 | 0.961 | 2800.0 / 164.06 | 93.0 | weak |
| audio-005 | distortion | 0.000 | 0.000 | 0.994 | 2800.0 / 150.58 | 123.9 | pass |
| audio-006 | distortion | 0.667 | 0.455 | 0.898 | 2800.0 / 166.28 | 78.3 | critical |

```json
{
  "n": 6,
  "mean_wer": 0.3713,
  "mean_cer": 0.237,
  "mean_semantic": 0.9438,
  "critical_count": 2,
  "mean_tail_ms": 87.75,
  "mean_audio_ms_to_first_partial": 2800.0
}
```
