from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from ...agent.tools.contracts import ToolContext, ToolDefinition
from ...agent.tools.registry import ToolRegistry
from .repository import repository


class BookingArgs(BaseModel):
    date: date
    time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    party_size: int = Field(ge=1, le=20)


class CreateBookingArgs(BookingArgs):
    customer_name: str = Field(min_length=1, max_length=120)


def check_availability(args: BookingArgs, _context: ToolContext) -> dict:
    return repository.availability(args.date, args.time, args.party_size)


def create_booking(args: CreateBookingArgs, _context: ToolContext) -> dict:
    return repository.create(args.date, args.time, args.party_size, args.customer_name)


def register_tools(registry: ToolRegistry) -> None:
    registry.register(ToolDefinition("check_availability", "Check whether a table is available.", BookingArgs, check_availability, "read"))
    registry.register(ToolDefinition("create_booking", "Create a confirmed booking for a customer.", CreateBookingArgs, create_booking, "write"))
