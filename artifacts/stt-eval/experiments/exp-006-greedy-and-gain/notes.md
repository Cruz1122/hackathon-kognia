Experiment: exp-006-greedy-and-gain
Cambio: greedy + gain_normalize
Motivo: ver si el KEEP de greedy anula la regresión de chopped del gain
Configuración anterior: greedy solo
Nueva configuración: greedy + RMS 0.10
Resultado: chopped otra vez 0.429; whisper empeora 0 → 0.077 vs greedy solo
Mejora: 006 WER 0.667 → 0.417
Regresión: entrecortado y susurro vs greedy
Decisión: REJECT
