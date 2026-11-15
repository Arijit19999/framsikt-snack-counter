"""Choosing who buys. A pure function, so it is easy to reason about and test.

Rules, in order:
1. Only volunteers who are physically in that office today. This covers
   hybrid days, holidays and visitors, who count for the office they are in.
2. Skip anyone already excluded from this refill (declined, let the offer
   time out, or missed the buying deadline).
3. Prefer people who are not still waiting for money from an earlier refill.
4. Then the fewest refills in the last 30 days (spreads the load).
5. Then whoever refilled longest ago, then volunteer id (deterministic).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .models import Volunteer
from .presence import PresenceDirectory

_NEVER = datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class VolunteerStats:
    refills_in_window: int = 0
    last_refill_at: datetime | None = None
    has_unpaid_claim: bool = False


def choose_buyer(
    office_id: str,
    volunteers: Iterable[Volunteer],
    presence: PresenceDirectory,
    stats: dict[str, VolunteerStats],
    excluded: set[str],
) -> Volunteer | None:
    candidates = [
        v
        for v in volunteers
        if v.id not in excluded and presence.presence_of(v.id).is_in(office_id)
    ]
    if not candidates:
        return None

    def rank(v: Volunteer) -> tuple:
        s = stats.get(v.id, VolunteerStats())
        return (
            s.has_unpaid_claim,
            s.refills_in_window,
            s.last_refill_at or _NEVER,
            v.id,
        )

    return min(candidates, key=rank)
