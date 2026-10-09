from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from ...agent.tools.contracts import ToolContext, ToolDefinition
from ...agent.tools.registry import ToolRegistry
from .repository import repository

CONTEXT_INSTRUCTIONS = (
    'Restaurant booking demonstration. Availability is simulated, not a live restaurant integration. '
    'Collect date, time, party size and customer name. Check availability before proposing create_booking. '
    'Resolve relative dates such as mañana/tomorrow using current_date from operational memory, including the year. '
    'Do not ask the customer to repeat a date or year already derivable from that context. '
    'Ask only when a date is genuinely ambiguous or the hour lacks AM/PM context. '
    'When name, date, unambiguous time and party size are known, check_availability and then call create_booking '
    'to register the proposal immediately; do not ask the customer to confirm in words before registering it. '
    'If availability is false, do not create or propose a booking for that slot. '
    'This demo cannot accommodate groups larger than ten at any time; changing the hour does not resolve that limit. '
    'Explain that concrete limit and offer assistance, never an invented available slot. '
    'Do not promise split-group reservations, neighboring tables or a multi-table arrangement; those options are '
    'not supported by this demo. Offer human assistance to assess larger groups instead.'
)


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
