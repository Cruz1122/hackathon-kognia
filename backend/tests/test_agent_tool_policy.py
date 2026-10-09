import pytest

from app.agent.policy import claims_callback, mentions_emergency


@pytest.mark.parametrize('text', [
    'Mi papá tiene dolor en el pecho',
    'No puedo respirar',
    'Se desmayó y está inconsciente',
    'Tiene convulsiones',
    'Hay un sangrado abundante',
    'Creo que es un infarto',
    'Síntomas de derrame',
    'Tomó una sobredosis',
    'Habla de suicidio',
    'Quiero morir',
    'Fue un accidente grave',
])
def test_mentions_emergency_matches_danger_signs(text):
    assert mentions_emergency(text)


@pytest.mark.parametrize('text', [
    '¿Qué hospitales tienen urgencias en Manizales?',
    'Tengo un dolor fuerte',
    'Quiero pedir una cita',
])
def test_mentions_emergency_ignores_registry_and_nonurgent_pain(text):
    assert not mentions_emergency(text)


def test_claims_callback_only_matches_real_call_claims():
    assert claims_callback('En este momento te estoy llamando al número registrado.')
    assert claims_callback('Ya tienes una llamada en curso con nosotros.')
    assert not claims_callback('¿Confirmas que te llame al teléfono de esta conversación?')
