import asyncio

from pydantic import BaseModel

from app.agent.tools.contracts import ToolContext, ToolDefinition
from app.agent.tools.loader import load_tool_registry
from app.agent.tools.registry import ToolRegistry


def test_demo_tools_are_loaded_without_runtime_domain_imports() -> None:
    registry = load_tool_registry("app.domains.demo_booking.tools")
    assert {item.name for item in registry.list_definitions()} == {"check_availability", "create_booking"}
    assert "organization_id" not in registry.schemas()[0]["parameters"].get("properties", {})


def test_registry_normalizes_unknown_and_invalid_arguments() -> None:
    registry = load_tool_registry()
    context = ToolContext("req-1", organization_id="server-org")
    unknown = asyncio.run(registry.execute("refund_booking", {}, context))
    invalid = asyncio.run(registry.execute("create_booking", {"party_size": -4}, context))
    assert unknown.error_code == "TOOL_NOT_FOUND"
    assert invalid.error_code == "TOOL_ARGUMENT_VALIDATION_ERROR"


def test_duplicate_names_are_rejected() -> None:
    class Args(BaseModel):
        value: int

    registry = ToolRegistry()
    definition = ToolDefinition("same", "same", Args, lambda _args, _ctx: {}, "read")
    registry.register(definition)
    try:
        registry.register(definition)
    except ValueError as exc:
        assert "TOOL_DUPLICATE_NAME" in str(exc)
    else:
        raise AssertionError("duplicate tool name was accepted")
