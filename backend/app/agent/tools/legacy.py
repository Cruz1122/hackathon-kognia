from pydantic import BaseModel, Field

from .contracts import ToolContext, ToolDefinition
from .registry import ToolRegistry


class LoremArgs(BaseModel):
    characters: int = Field(ge=1, le=5000)


class SumArgs(BaseModel):
    numbers: list[float] = Field(min_length=1)


def _lorem(args: LoremArgs, _context: ToolContext) -> str:
    text = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
    return (text * (args.characters // len(text) + 1))[:args.characters]


def _sum(args: SumArgs, _context: ToolContext) -> str:
    value = sum(args.numbers)
    return str(int(value)) if value.is_integer() else str(value)


def register_tools(registry: ToolRegistry) -> None:
    registry.register(ToolDefinition("generate_lorem_ipsum", "Legacy text helper.", LoremArgs, _lorem, "read"))
    registry.register(ToolDefinition("sum_numbers", "Legacy arithmetic helper.", SumArgs, _sum, "read"))
