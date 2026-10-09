from app.features.agent.tools import (
    execute_tool,
    generate_lorem_ipsum,
    present_tool_inputs,
    present_tool_name,
    present_tool_outputs,
    sum_numbers,
)


def test_lorem_ipsum_matches_requested_length() -> None:
    text = generate_lorem_ipsum(200)
    assert len(text) == 200
    assert text.lower().startswith("lorem ipsum")


def test_sum_numbers_adds_user_values() -> None:
    assert sum_numbers([4, 7, 12]) == "23"
    assert execute_tool("sum_numbers", {"numbers": [1.5, 2.5]}) == "4"


def test_tool_display_uses_client_labels() -> None:
    assert present_tool_name("create_booking") == "Creación de reserva"
    assert present_tool_inputs("create_booking", {
        "customer_name": "Ana",
        "date": "2026-09-16",
        "time": "19:30",
        "party_size": 4,
    }) == [
        {"label": "Nombre", "value": "Ana"},
        {"label": "Fecha", "value": "16/09/2026"},
        {"label": "Hora", "value": "19:30"},
        {"label": "Comensales", "value": "4"},
    ]
    assert present_tool_outputs("check_availability", '{"date":"2026-09-16","time":"19:30","party_size":4,"available":true}') == [
        {"label": "Fecha", "value": "16/09/2026"},
        {"label": "Hora", "value": "19:30"},
        {"label": "Comensales", "value": "4"},
        {"label": "Disponible", "value": "Sí"},
    ]
    assert present_tool_outputs("create_booking", '{"booking_id":"BKG-ABC","status":"confirmed"}') == [
        {"label": "Código de reserva", "value": "BKG-ABC"},
        {"label": "Estado", "value": "Confirmada"},
    ]
    assert present_tool_name("custom_lookup") == "Custom lookup"
