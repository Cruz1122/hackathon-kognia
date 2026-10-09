"""Turn memory for IPS search: filters, follow-ups and a change of place.

No model call. Intent comes from the JEV signal. Places come from a gazetteer.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from ...agent.state import IPSMemory

_DEPARTMENTS: tuple[tuple[str, str], ...] = (
    ('norte de santander', 'Norte de Santander'),
    ('valle del cauca', 'Valle del Cauca'),
    ('la guajira', 'La Guajira'),
    ('bogota', 'Bogotá'),
    ('amazonas', 'Amazonas'),
    ('antioquia', 'Antioquia'),
    ('arauca', 'Arauca'),
    ('atlantico', 'Atlántico'),
    ('bolivar', 'Bolívar'),
    ('boyaca', 'Boyacá'),
    ('caldas', 'Caldas'),
    ('caqueta', 'Caquetá'),
    ('casanare', 'Casanare'),
    ('cauca', 'Cauca'),
    ('cesar', 'Cesar'),
    ('choco', 'Chocó'),
    ('cordoba', 'Córdoba'),
    ('cundinamarca', 'Cundinamarca'),
    ('guainia', 'Guainía'),
    ('guaviare', 'Guaviare'),
    ('huila', 'Huila'),
    ('magdalena', 'Magdalena'),
    ('meta', 'Meta'),
    ('narino', 'Nariño'),
    ('putumayo', 'Putumayo'),
    ('quindio', 'Quindío'),
    ('risaralda', 'Risaralda'),
    ('santander', 'Santander'),
    ('sucre', 'Sucre'),
    ('tolima', 'Tolima'),
    ('vaupes', 'Vaupés'),
    ('vichada', 'Vichada'),
)
# Longest first so "norte de santander" wins over "santander".
_DEPARTMENTS = tuple(sorted(_DEPARTMENTS, key=lambda item: len(item[0]), reverse=True))

# A named city fills the municipality slot and its department, so the gate does not ask again.
_CITIES: tuple[tuple[str, str, str], ...] = (
    ('barranquilla', 'Barranquilla', 'Atlántico'),
    ('bucaramanga', 'Bucaramanga', 'Santander'),
    ('cartagena', 'Cartagena', 'Bolívar'),
    ('manizales', 'Manizales', 'Caldas'),
    ('medellin', 'Medellín', 'Antioquia'),
    ('pereira', 'Pereira', 'Risaralda'),
    ('santa marta', 'Santa Marta', 'Magdalena'),
    ('villavicencio', 'Villavicencio', 'Meta'),
    ('armenia', 'Armenia', 'Quindío'),
    ('ibague', 'Ibagué', 'Tolima'),
    ('neiva', 'Neiva', 'Huila'),
    ('pasto', 'Pasto', 'Nariño'),
    ('popayan', 'Popayán', 'Cauca'),
    ('tunja', 'Tunja', 'Boyacá'),
    ('cali', 'Cali', 'Valle del Cauca'),
)
_CITIES = tuple(sorted(_CITIES, key=lambda item: len(item[0]), reverse=True))

_SEARCH = {'buscar_ips', 'informacion_ips', 'capacidad_ips', 'comparar_ips'}
_GREETING = {'hola', 'hey', 'buenas', 'buenos dias', 'buenas tardes', 'buenas noches', 'buen dia'}
_THANKS = {'gracias', 'muchas gracias', 'ok gracias', 'eso es todo', 'listo', 'no gracias'}


def fold(text: str) -> str:
    return ''.join(
        char for char in unicodedata.normalize('NFD', text.casefold())
        if unicodedata.category(char) != 'Mn'
    )


def update_memory(memory: IPSMemory, text: str, intent: str | None) -> None:
    """Apply this turn's intent and any newly heard filters. A new place drops the previous results."""
    if intent:
        memory.intent = intent
    folded = fold(text)
    department = _department(folded)
    municipality = _city(folded)
    if municipality and not department:
        department = municipality[1]
        municipality_name = municipality[0]
    else:
        municipality_name = municipality[0] if municipality else None
    place_changed = False
    if department and department != memory.department:
        place_changed = True
        memory.department = department
        if not municipality_name:
            memory.municipality = None
    if municipality_name and municipality_name != memory.municipality:
        place_changed = True
        memory.municipality = municipality_name
    if place_changed:
        memory.last_site_codes = []
        memory.site_code = None
        memory.site_name = None
    heard = bool(department or municipality_name)
    if re.search(r'\bpublic', folded):
        memory.nature = 'publica'
        heard = True
    elif re.search(r'\bprivad', folded):
        memory.nature = 'privada'
        heard = True
    if re.search(r'\bhospital', folded):
        memory.kind = 'hospital'
        heard = True
    elif re.search(r'\bclinic', folded):
        memory.kind = 'clinica'
        heard = True
    if re.search(r'\buci\b', folded):
        memory.capacity = 'UCI'
        heard = True
    elif re.search(r'\bcamas?\b', folded):
        memory.capacity = 'camas'
        heard = True
    if memory.intent in {None, 'unknown'} and heard:
        if memory.last_site_codes and memory.capacity and not (department or municipality_name):
            memory.intent = 'comparar_ips'
        else:
            memory.intent = 'buscar_ips'
    memory.awaiting = _missing(memory) or ''


def choose_stage(memory: IPSMemory, text: str, *, first_turn: bool) -> str:
    """One resting stage for this turn. Emergency and out of scope win before a search."""
    intent = memory.intent
    if intent == 'emergencia':
        return 'emergencia'
    if intent == 'fuera_alcance':
        return 'fuera_alcance'
    if intent in {None, 'unknown'} and fold(text).strip(' .,!¡¿?') in _THANKS:
        return 'cierre'
    if first_turn and intent in {None, 'unknown'} and fold(text).strip(' .,!¡¿?') in _GREETING:
        return 'inicio'
    if intent == 'orientacion_salud':
        return 'respondiendo'
    if memory.awaiting:
        return 'aclarando'
    if intent in _SEARCH:
        return 'buscando'
    return 'respondiendo'


def remember_tool(memory: IPSMemory, arguments: dict[str, Any], result: dict[str, Any]) -> None:
    """A successful tool fills filters the extractor did not hear, and the sites it returned."""
    if result.get('status') not in {None, 'ok'}:
        return
    for key in ('department', 'municipality', 'capacity'):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            setattr(memory, key, value.strip())
    nature = arguments.get('nature')
    if nature in {'publica', 'privada'}:
        memory.nature = nature
    kind = arguments.get('kind')
    if kind in {'hospital', 'clinica'}:
        memory.kind = kind
    codes: list[str] = []
    rows = result.get('results') if isinstance(result.get('results'), list) else []
    sites = result.get('sites') if isinstance(result.get('sites'), list) else []
    for row in [*rows, *sites]:
        if isinstance(row, dict) and row.get('site_code'):
            codes.append(str(row['site_code']))
    for key in ('site', 'site_capacity'):
        site = result.get(key) if isinstance(result.get(key), dict) else None
        if site and site.get('site_code'):
            codes.append(str(site['site_code']))
    if codes:
        memory.last_site_codes = list(dict.fromkeys(codes))[:10]
        memory.site_code = memory.last_site_codes[0]
    memory.awaiting = ''


def _missing(memory: IPSMemory) -> str | None:
    has_place = bool(memory.department or memory.municipality or memory.site_name)
    if memory.intent == 'buscar_ips':
        return None if has_place else 'location'
    if memory.intent in {'capacidad_ips', 'comparar_ips'}:
        return None if has_place or memory.last_site_codes else 'location'
    if memory.intent == 'informacion_ips':
        return None if memory.site_name or memory.site_code or memory.last_site_codes else 'name'
    return None


def _department(folded: str) -> str | None:
    for needle, name in _DEPARTMENTS:
        if re.search(rf'\b{re.escape(needle)}\b', folded):
            return name
    return None


def _city(folded: str) -> tuple[str, str] | None:
    for needle, city, department in _CITIES:
        if re.search(rf'\b{re.escape(needle)}\b', folded):
            return city, department
    return None
