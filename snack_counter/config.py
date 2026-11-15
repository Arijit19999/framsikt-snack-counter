"""Office configuration taken from the brief."""
from __future__ import annotations

from .models import Office
from .money import Money

OSLO = "oslo"
BENGALURU = "bengaluru"


def default_offices() -> list[Office]:
    return [
        Office(
            id=OSLO,
            name="Oslo",
            currency="NOK",
            timezone="Europe/Oslo",
            monthly_budget=Money.of("6000", "NOK"),
            typical_min=Money.of("300", "NOK"),
            typical_max=Money.of("1200", "NOK"),
            coordinator_id="coord-oslo",
            channel="snacks-oslo",
        ),
        Office(
            id=BENGALURU,
            name="Bengaluru",
            currency="INR",
            timezone="Asia/Kolkata",
            monthly_budget=Money.of("15000", "INR"),
            typical_min=Money.of("500", "INR"),
            typical_max=Money.of("3000", "INR"),
            coordinator_id="coord-blr",
            channel="snacks-bengaluru",
        ),
    ]
