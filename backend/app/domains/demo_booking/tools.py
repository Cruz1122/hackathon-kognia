from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from ...agent.tools.contracts import ToolContext, ToolDefinition
from ...agent.tools.registry import ToolRegistry
from .repository import repository

CONTEXT_INSTRUCTIONS = (
    'Restaurant booking demonstration. Availability is simulated, not a live restaurant integration. '
    'Collect date, time, party size and customer name. Check availability before proposing create_booking. '
    'Never treat missing year or ambiguous time as confirmed. '
    'When schedule_flexibility is flexible and the requested slot is unavailable, offer a different time instead of repeating the same search.'
)
JEV_QUESTIONS = {
    'schedule_flexibility': ('Has the customer indicated willingness to change their requested booking time?', ['flexible', 'fixed', 'unknown']),
}


class BookingArgs(BaseModel):
    date: date
    time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    party_size: int = Field(ge=1, le=20)


class CreateBookingArgs(BookingArgs):
    customer_name: str = Field(min_length=1, max_length=120)


def check_availability(args: BookingArgs, _context: ToolContext) -> dict:
    return repository.availability(args.date, args.time, args.party_size)


def create_booking(args: CreateBookingArgs, _context: ToolContext) -> dict:
    if not _context.operation_id:
        raise ValueError('A durable authorized operation is required')
    if not repository.availability(args.date, args.time, args.party_size)['available']:
        raise ValueError('Availability changed')
    # The operation ledger commits this result atomically with AgentState.
    return {'booking_id': 'BKG-' + _context.operation_id[:12].upper(), 'status': 'confirmed'}


def register_tools(registry: ToolRegistry) -> None:
    registry.register(ToolDefinition("check_availability", "Check whether a table is available.", BookingArgs, check_availability, "read"))
    registry.register(ToolDefinition("create_booking", "Create a confirmed booking for a customer.", CreateBookingArgs, create_booking, "write", replay_safe=True))
