"""Shared test setup: a small simulated world with both offices."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from snack_counter.chat import FakeChat
from snack_counter.clock import FakeClock
from snack_counter.config import BENGALURU, OSLO, default_offices
from snack_counter.hr import FakeHR
from snack_counter.models import Refill, Volunteer
from snack_counter.money import Money
from snack_counter.presence import FakePresence
from snack_counter.refill_service import RefillService, Settings

# Monday 16 Nov 2026, 09:00 in Oslo / 13:30 in Bengaluru.
START = datetime(2026, 11, 16, 8, 0, tzinfo=timezone.utc)
RECEIPT = b"\xff\xd8 fake receipt photo"

OSLO_VOLUNTEERS = ["kari", "lars", "nora", "ola"]
BLR_VOLUNTEERS = ["arjun", "meera", "priya"]

VOLUNTEERS = [
    Volunteer("kari", "Kari", "E-101", OSLO, "NOK"),
    Volunteer("lars", "Lars", "E-102", OSLO, "NOK"),
    Volunteer("nora", "Nora", "E-103", OSLO, "NOK"),
    Volunteer("ola", "Ola", "E-104", OSLO, "NOK"),
    Volunteer("arjun", "Arjun", "E-201", BENGALURU, "INR"),
    Volunteer("meera", "Meera", "E-202", BENGALURU, "INR"),
    Volunteer("priya", "Priya", "E-203", BENGALURU, "INR"),
]


def nok(amount: str) -> Money:
    return Money.of(amount, "NOK")


def inr(amount: str) -> Money:
    return Money.of(amount, "INR")


@dataclass
class World:
    clock: FakeClock
    chat: FakeChat
    hr: FakeHR
    presence: FakePresence
    service: RefillService


def build_world(start: datetime = START, settings: Settings | None = None) -> World:
    clock = FakeClock(start)
    chat = FakeChat()
    hr = FakeHR()
    presence = FakePresence(VOLUNTEERS)
    service = RefillService(
        offices=default_offices(),
        volunteers=VOLUNTEERS,
        chat=chat,
        hr=hr,
        presence=presence,
        clock=clock,
        settings=settings,
    )
    return World(clock, chat, hr, presence, service)


def complete_refill(world: World, office_id: str, amount: Money, items=()) -> Refill:
    """Report empty -> offered volunteer accepts -> receipt submitted."""
    s = world.service
    refill = s.report_empty(office_id, reported_by="someone")
    buyer = refill.current_offer.volunteer_id
    s.accept(refill.id, buyer)
    s.submit_receipt(refill.id, buyer, amount, RECEIPT, items)
    return refill


def month_of(world: World, office_id: str) -> str:
    return world.service.offices[office_id].local_month(world.clock.now())
