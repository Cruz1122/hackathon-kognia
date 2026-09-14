Experiment: exp-001-gain-normalize
Cambio: RMS → 0.10 en el harness
Motivo: 001/006 saturan (peak ~1); 002/005 son bajos. Docs ASR: gain control antes del reconocedor.
Configuración anterior: sin preprocess
Nueva configuración: gain_normalize target_rms=0.10
Resultado: mean WER 0.348 (mejor), critical 1/6 (006 deja de ser crítico). Chopped 0.143 → 0.429.
Mejora: oficina y distorsión-006
Regresión: entrecortado (grave)
Decisión: REJECT (no aplicar globalmente)
