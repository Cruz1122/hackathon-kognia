"""Guided reservation and cross-channel regression conversations."""
from scripts.agent_stt_cases import speech


def whatsapp(text):
    return {**speech(text), 'channel': 'whatsapp'}


SCENARIOS = [
    ('guided_user_report', 'reported_callback', [
        speech('Hola, hola, hola, hola.'), speech('Necesito hacer una reserva.'),
        speech('Mañana a las ocho.'), speech('Mensaje ininteligible.', 'I sáma sa', 'phonetic_substitution', 'unclear'),
        speech('Mañana a las ocho y somos ocho.'), speech('Soy Camilo, a las ocho de la mañana.'),
        speech('Es que es muy enredado.', 'Es que es muy enregado', 'phonetic_substitution', 'context_recoverable'),
        whatsapp('Cortó, llámame, por favor')]),
    ('guided_step_by_step', 'booking', [speech(text) for text in [
        'Hola.', 'Necesito reservar.', 'Mañana.', 'A las siete.', 'De la noche.',
        'Cuatro personas.', 'Camilo.', 'Sí, confirmo.']]),
    ('guided_all_details', 'booking', [speech('Soy Camilo, mesa para cuatro mañana a las siete de la noche.'), speech('Confirmo.')]),
    ('guided_noise_after_date', 'booking', [speech(text) for text in [
        'Soy Camilo, reserva mañana a las siete de la noche.', 'I sáma sa', 'Cuatro personas.', 'Confirmo.']]),
    ('guided_morning', 'booking_morning', [speech(text) for text in [
        'Soy Camilo, mesa para ocho mañana a las ocho.', 'De la mañana.', 'Confirmo.']]),
    ('guided_condition_change', 'changed', [speech(text) for text in [
        'Soy Camilo, cuatro personas mañana a las siete de la noche.',
        'Sí, pero mejor a las ocho de la noche.', 'Confirmo.']]),
    ('guided_cancel', 'cancel', [speech(text) for text in [
        'Soy Camilo, cuatro personas mañana a las siete de la noche.', 'No, mejor no la hagas.']]),
    ('guided_whatsapp_callback', 'callback', [whatsapp('Se cortó la llamada, llámame por favor.')]),
    ('guided_large_group', 'unavailable', [speech(text) for text in [
        'Soy Camilo, mesa para doce mañana a las siete de la noche.', '¿Qué opción tienes para doce?']]),
    ('guided_human', 'human', [speech('Quiero hablar con un asesor humano.'), speech('Sí confirma la reserva para cuatro.')]),
]
