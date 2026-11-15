"""End-to-end walkthrough with the simulated chat tool and HR system.

Run:  python demo.py
Prints every chat message the system sends, step by step, then the
monthly management report.
"""
from datetime import datetime, timezone
from decimal import Decimal

from snack_counter.chat import FakeChat
from snack_counter.clock import FakeClock
from snack_counter.config import BENGALURU, OSLO, default_offices
from snack_counter.hr import FakeHR
from snack_counter.models import Volunteer
from snack_counter.money import Money
from snack_counter.presence import FakePresence
from snack_counter.refill_service import AlreadyTakenError, RefillService
from snack_counter.report import FxRates, build_monthly_report, render_text

VOLUNTEERS = [
    Volunteer("kari", "Kari", "E-101", OSLO, "NOK"),
    Volunteer("lars", "Lars", "E-102", OSLO, "NOK"),
    Volunteer("nora", "Nora", "E-103", OSLO, "NOK"),
    Volunteer("arjun", "Arjun", "E-201", BENGALURU, "INR"),
    Volunteer("meera", "Meera", "E-202", BENGALURU, "INR"),
    Volunteer("priya", "Priya", "E-203", BENGALURU, "INR"),
]
RECEIPT = b"\xff\xd8 receipt photo"

clock = FakeClock(datetime(2026, 11, 16, 8, 0, tzinfo=timezone.utc))
chat = FakeChat()
hr = FakeHR()
presence = FakePresence(VOLUNTEERS)
service = RefillService(default_offices(), VOLUNTEERS, chat, hr, presence, clock)
_seen = 0


def step(title: str) -> None:
    global _seen
    print(f"\n=== {clock.now():%a %d %b %H:%M} UTC | {title}")
    for m in chat.messages[_seen:]:
        print(f"   -> {m.to:<18} {m.text}")
    _seen = len(chat.messages)


# --- Oslo ---------------------------------------------------------------
presence.set_remote("nora")

r1 = service.report_empty(OSLO, reported_by="someone-at-the-coffee-machine")
step("Oslo box reported empty (Nora works from home today)")

service.report_empty(OSLO, reported_by="lars")
step("Lars reports the same empty box again: no second refill")

clock.advance(hours=3)
service.tick()
step("Kari did not answer within 3 hours: passed on")

service.accept(r1.id, r1.current_offer.volunteer_id)
step(f"{service.volunteers[r1.assignee_id].name} accepts")

try:
    service.accept(r1.id, "kari")
except AlreadyTakenError as e:
    print(f"   !! Kari tries to accept too: {e}")

clock.advance(hours=2)
service.submit_receipt(
    r1.id, r1.assignee_id, Money.of("845", "NOK"), RECEIPT, ["Bananas", "Kvikk Lunsj"]
)
step("Receipt uploaded: claim goes to HR automatically")

# --- Bengaluru --------------------------------------------------------------
r2 = service.report_empty(BENGALURU, reported_by="someone")
service.accept(r2.id, r2.current_offer.volunteer_id)
clock.advance(hours=4)
service.submit_receipt(
    r2.id, r2.assignee_id, Money.of("2350", "INR"), RECEIPT, ["Parle-G", "Bananas"]
)
step("Bengaluru refill, start to finish")

# --- HR -------------------------------------------------------------------
clock.advance(days=3)
hr.approve(r1.claim_id)
service.tick()
step("HR approves the Oslo claim")

clock.advance(days=2)
hr.pay(r1.claim_id)
service.tick()
step("Oslo claim paid out; Bengaluru claim pending 5 days, so the coordinator is nudged")

# --- report -------------------------------------------------------------------
fx = FxRates("NOK", {("2026-11", "INR"): Decimal("0.1240")})
print("\n" + render_text(build_monthly_report(service, "2026-11", fx)))
