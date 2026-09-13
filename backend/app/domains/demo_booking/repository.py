from __future__ import annotations

import hashlib
from datetime import date


class BookingRepository:
    def __init__(self) -> None:
        self._bookings: dict[str, dict] = {}

    def availability(self, booking_date: date, time: str, party_size: int) -> dict:
        return {"date": booking_date.isoformat(), "time": time, "party_size": party_size, "available": party_size <= 10}

    def create(self, booking_date: date, time: str, party_size: int, customer_name: str) -> dict:
        raw = f"{booking_date.isoformat()}|{time}|{party_size}|{customer_name}|{len(self._bookings)}".encode()
        booking_id = "BKG-" + hashlib.sha256(raw).hexdigest()[:10].upper()
        result = {"booking_id": booking_id, "status": "confirmed"}
        self._bookings[booking_id] = result
        return result


repository = BookingRepository()
