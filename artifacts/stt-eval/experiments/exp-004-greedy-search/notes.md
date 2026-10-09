Experiment: exp-004-greedy-search
Cambio: decoding_method modified_beam_search → greedy_search
Motivo: Default oficial de OnlineRecognizer.from_transducer. temperature_scale no afecta logits; beam no adelantó el primer partial (siempre 2800 ms).
Configuración anterior: modified_beam_search, max_active_paths=4
Nueva configuración: greedy_search
Resultado: mean WER 0.371; whisper WER 0; 001 0.944; chopped/005 iguales; tail no empeora
Mejora: susurro perfecto; media WER; 001 leve
Regresión: discoteca CER 0.159 → 0.171 (no crítica)
Decisión: KEEP
