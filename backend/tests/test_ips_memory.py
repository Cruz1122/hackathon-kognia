"""Conversational IPS memory: filters, a missing place, follow-ups and a change of topic."""

from app.agent.state import IPSMemory
from app.domains.ips.memory import choose_stage, remember_tool, update_memory


def test_antioquia_public_hospitals_fill_filters() -> None:
    memory = IPSMemory()
    update_memory(memory, 'Quiero hospitales públicos en Antioquia', 'buscar_ips')
    assert memory.department == 'Antioquia'
    assert memory.nature == 'publica'
    assert memory.kind == 'hospital'
    assert memory.awaiting == ''
    assert choose_stage(memory, 'Quiero hospitales públicos en Antioquia', first_turn=False) == 'buscando'


def test_hospitals_without_a_place_ask_and_do_not_search() -> None:
    memory = IPSMemory()
    update_memory(memory, 'Muéstrame hospitales', 'buscar_ips')
    assert memory.kind == 'hospital'
    assert memory.awaiting == 'location'
    assert choose_stage(memory, 'Muéstrame hospitales', first_turn=False) == 'aclarando'


def test_follow_up_reuses_the_previous_search() -> None:
    memory = IPSMemory(department='Antioquia', kind='hospital', last_site_codes=['9100100019', '0500100001'])
    update_memory(memory, '¿Cuál tiene más camas?', 'comparar_ips')
    assert memory.department == 'Antioquia'
    assert memory.last_site_codes == ['9100100019', '0500100001']
    assert memory.capacity == 'camas'
    assert choose_stage(memory, '¿Cuál tiene más camas?', first_turn=False) == 'buscando'
    inherited = IPSMemory(department='Antioquia', last_site_codes=['9100100019'])
    update_memory(inherited, '¿Cuál tiene más camas?', 'unknown')
    assert inherited.intent == 'comparar_ips'
    assert inherited.last_site_codes == ['9100100019']


def test_a_new_place_clears_the_previous_results() -> None:
    memory = IPSMemory(department='Antioquia', nature='publica', last_site_codes=['9100100019'])
    update_memory(memory, 'Ahora en Bogotá', 'buscar_ips')
    assert memory.department == 'Bogotá'
    assert memory.last_site_codes == []
    assert choose_stage(memory, 'Ahora en Bogotá', first_turn=False) == 'buscando'


def test_an_appointment_request_does_not_search() -> None:
    memory = IPSMemory()
    update_memory(memory, 'Agéndame una cita', 'fuera_alcance')
    assert memory.awaiting == ''
    assert choose_stage(memory, 'Agéndame una cita', first_turn=False) == 'fuera_alcance'


def test_emergency_keeps_the_search() -> None:
    memory = IPSMemory(department='Antioquia', last_site_codes=['9100100019'])
    update_memory(memory, 'No puedo respirar', 'emergencia')
    assert memory.department == 'Antioquia'
    assert memory.last_site_codes == ['9100100019']
    assert choose_stage(memory, 'No puedo respirar', first_turn=False) == 'emergencia'


def test_a_bare_greeting_opens_the_conversation() -> None:
    memory = IPSMemory()
    update_memory(memory, 'Hola', 'unknown')
    assert choose_stage(memory, 'Hola', first_turn=True) == 'inicio'


def test_thanks_closes_without_clearing_the_search() -> None:
    memory = IPSMemory(department='Antioquia', last_site_codes=['9100100019'], intent='buscar_ips')
    update_memory(memory, 'Gracias', 'unknown')
    assert memory.last_site_codes == ['9100100019']
    assert choose_stage(memory, 'Gracias', first_turn=False) == 'cierre'


def test_a_successful_tool_remembers_the_sites() -> None:
    memory = IPSMemory(intent='buscar_ips', department='Antioquia')
    remember_tool(memory, {'department': 'Antioquia'}, {
        'status': 'ok',
        'results': [{'site_code': '1'}, {'site_code': '2'}],
    })
    assert memory.last_site_codes == ['1', '2']
