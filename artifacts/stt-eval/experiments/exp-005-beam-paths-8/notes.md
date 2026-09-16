Experiment: exp-005-beam-paths-8
Cambio: max_active_paths 4 → 8
Motivo: Docs: beam size en modified_beam_search. Hipótesis: más hipótesis en ruido.
Configuración anterior: 4
Nueva configuración: 8
Resultado: calidad ≈ greedy; tail medio 88 ms vs 72 ms greedy
Mejora: whisper también a WER 0
Regresión: más CPU en cola, sin ganar a greedy en WER medio
Decisión: REJECT (greedy gana en simplicidad/latencia)
