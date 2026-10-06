"""Hand-authored ASR error hypotheses, not a measured acoustic error corpus.

The clean utterance is oracle-only. Never send it to the agent. Word substitutions,
deletions and utterance boundaries matter here, not spelling mistakes.
"""


def speech(clean, transcript=None, error='none', recoverability='clean'):
    return {'clean_utterance': clean, 'transcript': clean if transcript is None else transcript,
            'error_type': error, 'recoverability': recoverability}


BOOKING = 'Soy Camilo, quiero una reserva para cuatro personas mañana a las siete de la noche.'
CONFIRM = speech('Sí, confirmo la reserva.')

SCENARIOS = [
    ('stt_known_name', 'booking', [
        speech('Hola, me llamo Camilo.'),
        speech('Sí, me llamo Camilo.', 'Si me llevo camino', 'phonetic_word_substitution', 'context_recoverable'),
        speech('Necesito una reserva para cuatro personas mañana a las siete de la noche.'), CONFIRM]),
    ('stt_number_substitution', 'booking', [
        speech('Soy Camilo, quiero una reserva para cuatro personas mañana a las siete de la noche.',
               'Soy Camilo quiero una reserva para catorce personas mañana a las siete de la noche',
               'number_substitution', 'requires_clarification'),
        speech('Cuatro personas, no catorce.'), CONFIRM]),
    ('stt_omitted_party', 'booking', [
        speech('Soy Camilo y necesito una reserva.'),
        speech('Mañana a las siete de la noche, para cuatro personas.',
               'Mañana a las siete de la noche', 'deleted_required_detail', 'requires_clarification'),
        speech('Cuatro personas.'), CONFIRM]),
    ('stt_split_utterance', 'booking', [
        speech('Soy Camilo y necesito una reserva.'),
        speech('Mañana a las siete de la noche, para cuatro personas.',
               'Mañana a las siete de', 'early_endpoint', 'requires_next_segment'),
        speech('La noche, para cuatro personas.', 'la noche para cuatro personas',
               'continuation_segment', 'context_recoverable'), CONFIRM]),
    ('stt_truncated_change', 'changed', [
        speech(BOOKING),
        speech('Sí, pero mejor a las ocho de la noche.', 'Sí pero mejor a las',
               'truncated_condition', 'requires_clarification'),
        speech('A las ocho de la noche.'), CONFIRM]),
    ('stt_cancel_substitution', 'cancel', [
        speech(BOOKING),
        speech('No, olvídalo, mejor no.', 'No he olvidado mejor no',
               'word_boundary_and_substitution', 'context_recoverable'),
        speech('Hola.')]),
    ('stt_callback_omission', 'callback', [
        speech('¿Me puedes devolver la llamada?', 'Me puedes devolver la',
               'deleted_request_target', 'requires_clarification'),
        speech('La llamada, por teléfono.'),
        speech('Sí, llámame.', 'Sí dame la llamada', 'phonetic_word_substitution', 'context_recoverable')]),
    ('stt_group_number', 'unavailable', [
        speech('Soy Camilo, quiero una mesa para doce personas mañana a las siete de la noche.',
               'Soy Camilo quiero una mesa para dos personas mañana a las siete de la noche',
               'number_substitution', 'requires_clarification'),
        speech('No, doce personas, somos doce.'),
        speech('¿Qué opción tienes para nosotros?')]),
    ('stt_human_substitution', 'human', [
        speech('Quiero hablar con una persona.', 'Quiero hablar con una persiana',
               'phonetic_word_substitution', 'requires_clarification'),
        speech('Con un asesor humano, una persona real.'),
        speech('Sí, confirma la reserva para cuatro.')]),
    ('stt_lost_negation', 'no_write', [
        speech(BOOKING),
        speech('No confirmes todavía la reserva.', 'Confirma la reserva',
               'lost_negation_and_verb_substitution', 'irrecoverable_from_text'),
        speech('No, dije que no confirmaras todavía.')]),
]
