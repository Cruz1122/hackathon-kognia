from __future__ import annotations

from datetime import date


class BookingRepository:
    def availability(self, booking_date: date, time: str, party_size: int) -> dict:
        return {"date": booking_date.isoformat(), "time": time, "party_size": party_size, "available": party_size <= 10}


repository = BookingRepository()
