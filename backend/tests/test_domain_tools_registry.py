import asyncio

import pytest
from pydantic import BaseModel

from app.agent.tools.contracts import ToolContext, ToolDefinition
from app.agent.tools.loader import load_tool_registry
from app.agent.tools.registry import ToolRegistry
from app.analytics.schemas import DashboardResponse
from app.domains.business_analytics.tools import BusinessAnalyticsArgs, get_business_analytics


def test_demo_tools_are_loaded_without_runtime_domain_imports() -> None:
    registry = load_tool_registry("app.domains.demo_booking.tools")
    assert {item.name for item in registry.list_definitions()} == {"check_availability", "create_booking"}
    assert "organization_id" not in registry.schemas()[0]["parameters"].get("properties", {})


def test_registry_normalizes_unknown_and_invalid_arguments() -> None:
    registry = load_tool_registry()
    context = ToolContext("req-1", organization_id="server-org")
    unknown = asyncio.run(registry.execute("refund_booking", {}, context))
    invalid = asyncio.run(registry.execute("search_ips", {}, context))
    assert unknown.error_code == "TOOL_NOT_FOUND"
    assert invalid.error_code == "TOOL_ARGUMENT_VALIDATION_ERROR"


def test_default_runtime_loads_ips_tools_without_restaurant_tools() -> None:
    registry = load_tool_registry()
    names = {item.name for item in registry.list_definitions()}
    assert {
        "search_ips", "get_ips_details", "get_ips_capacity", "semantic_search_ips", "compare_ips_capacity",
    } <= names
    assert "check_availability" not in names
    assert "create_booking" not in names
    assert "generate_lorem_ipsum" not in names
    assert "get_business_analytics" not in names


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


def test_business_analytics_tool_requires_tenant_context() -> None:
    context = ToolContext("req-analytics")
    with pytest.raises(ValueError):
        asyncio.run(get_business_analytics(BusinessAnalyticsArgs(), context))


def test_business_analytics_tool_is_registered_once() -> None:
    registry = load_tool_registry("app.domains.business_analytics.tools")
    assert [item.name for item in registry.list_definitions()] == ["get_business_analytics"]


def test_business_analytics_rejects_invalid_tenant_context_as_tool_error() -> None:
    registry = load_tool_registry("app.domains.business_analytics.tools")
    result = asyncio.run(
        registry.execute(
            "get_business_analytics",
            {},
            ToolContext("req-invalid-analytics", organization_id="not-a-uuid"),
        )
    )
    assert result.ok is False
    assert result.error_code == "TOOL_EXECUTION_ERROR"
