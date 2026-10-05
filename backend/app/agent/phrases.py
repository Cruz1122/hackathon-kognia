"""Bounded conversational prompts; never claim an external action succeeded."""
import random

HOLDING = (
    'Dame un momento, por favor.',
    'Permíteme revisar lo que tenemos pendiente.',
    'Un instante, estoy revisando la información.',
)
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


def pick(pool: tuple[str, ...], previous: str | None = None) -> str:
    return random.choice(tuple(text for text in pool if text != previous) or pool)
