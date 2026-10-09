"""Bounded conversational prompts; never claim an external action succeeded."""
import random

BACKCHANNEL = (
    'Dame un segundo.',
    'Claro, ya reviso.',
    'Un momento, por favor.',
    'Déjame comprobarlo.',
    'Voy a revisar esa información.',
    'Ya te cuento.',
)
# Compatibility name for the existing silence/holding callers.
HOLDING = BACKCHANNEL
RECOVERY = (
    'Para seguir, ¿qué necesitas gestionar?',
    '¿Qué te gustaría que revisemos primero?',
    'Cuéntame un poco más de lo que necesitas.',
)
SILENCE = (
    '¿Sigues ahí?',
    '¿Me escuchas bien? Estoy aquí para ayudarte.',
    '¿Continuamos? Dime si necesitas un momento.',
)
EMERGENCY_GUIDANCE = 'Si es una emergencia, llama ahora al 123 o ve al servicio de urgencias más cercano.'
IPS_GREETING = (
    'Hola, soy un asistente de información de IPS y hospitales de Colombia. '
    'Puedo ayudarte a encontrar instituciones, consultar su información registrada '
    'y conocer las capacidades registradas.'
)

def pick(pool: tuple[str, ...], previous: str | None = None) -> str:
    return random.choice(tuple(text for text in pool if text != previous) or pool)
