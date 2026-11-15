"""Budget check, claims and currencies."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from snack_counter.config import BENGALURU, OSLO
from snack_counter.models import RefillState
from snack_counter.money import CurrencyMismatchError, Money
from snack_counter.refill_service import NotAllowedError
from tests.helpers import (
    OSLO_VOLUNTEERS,
    RECEIPT,
    build_world,
    complete_refill,
    inr,
    month_of,
    nok,
)


# ---- money basics ---------------------------------------------------------------
def test_money_refuses_floats_and_mixed_currencies():
    with pytest.raises(TypeError):
        Money.of(10.5, "NOK")
    with pytest.raises(CurrencyMismatchError):
        nok("100") + inr("100")
    with pytest.raises(ValueError):
        Money.of("1.005", "NOK")


def test_conversion_rounds_half_up_to_the_minor_unit():
    assert inr("999.99").convert(Decimal("0.1234"), "NOK") == nok("123.40")
    assert inr("4200").convert(Decimal("0.125"), "NOK") == nok("525")


# ---- reserving and spending -----------------------------------------------------
def test_accepting_reserves_the_spend_cap():
    w = build_world()
    refill = w.service.report_empty(OSLO, "anyone")
    w.service.accept(refill.id, "kari")

    month = month_of(w, OSLO)
    assert refill.spend_cap == nok("1200")
    assert w.service.ledger.remaining(OSLO, month) == nok("4800")


def test_receipt_within_cap_is_spent_and_claimed_with_the_right_details():
    w = build_world()
    refill = complete_refill(w, OSLO, nok("850.50"), items=["Bananas"])
    month = month_of(w, OSLO)

    ledger = w.service.ledger
    assert ledger.spent(OSLO, month) == nok("850.50")
    assert ledger.reserved(OSLO, month) == nok("0")
    assert ledger.remaining(OSLO, month) == nok("5149.50")

    claim = w.hr.claims[refill.claim_id]
    assert claim.employee_id == "E-101"
    assert claim.amount == Decimal("850.50")
    assert claim.currency == "NOK"
    assert claim.receipt_image == RECEIPT
    assert claim.idempotency_key == refill.id
    assert "Oslo" in claim.description and "Bananas" in claim.description


def test_receipt_above_cap_waits_for_coordinator_before_going_to_hr():
    w = build_world()
    refill = complete_refill(w, OSLO, nok("1500"))
    month = month_of(w, OSLO)

    assert refill.state is RefillState.NEEDS_REVIEW
    assert w.hr.claims == {}
    assert w.service.ledger.remaining(OSLO, month) == nok("4500")  # held, not lost
    assert any("above the agreed cap" in m for m in w.chat.to("coord-oslo"))

    with pytest.raises(NotAllowedError):
        w.service.approve_review(refill.id, "kari")  # not the coordinator

    w.service.approve_review(refill.id, "coord-oslo")
    assert refill.state is RefillState.CLAIM_SUBMITTED
    assert w.service.ledger.spent(OSLO, month) == nok("1500")
    assert w.service.ledger.reserved(OSLO, month) == nok("0")


def test_rejected_over_cap_receipt_is_not_counted():
    w = build_world()
    refill = complete_refill(w, OSLO, nok("1500"))
    w.service.reject_review(refill.id, "coord-oslo", "Please keep to the cap")

    month = month_of(w, OSLO)
    assert refill.state is RefillState.REJECTED
    assert w.service.ledger.remaining(OSLO, month) == nok("6000")
    assert w.hr.claims == {}


def test_cap_shrinks_to_what_is_left_in_the_budget():
    w = build_world()
    for amount in ("1200", "1200", "1200", "1200", "600"):  # 5,400 spent
        complete_refill(w, OSLO, nok(amount))

    refill = w.service.report_empty(OSLO, "anyone")
    w.service.accept(refill.id, refill.current_offer.volunteer_id)
    assert refill.spend_cap == nok("600")


def test_exhausted_budget_blocks_the_refill_until_coordinator_approves():
    w = build_world()
    for amount in ("1200", "1200", "1200", "1200", "1000"):  # 5,800 spent, 200 left
        complete_refill(w, OSLO, nok(amount))
    month = month_of(w, OSLO)

    refill = w.service.report_empty(OSLO, "anyone")
    assert refill.state is RefillState.BLOCKED_BUDGET
    assert refill.offers == []
    assert any("budget is left" in m for m in w.chat.to("coord-oslo"))
    assert any("don't buy" in m for m in w.chat.channel("snacks-oslo"))

    with pytest.raises(NotAllowedError):
        w.service.approve_budget_override(refill.id, "coord-blr")  # wrong office

    w.service.approve_budget_override(refill.id, "coord-oslo")
    buyer = refill.current_offer.volunteer_id
    w.service.accept(refill.id, buyer)
    assert refill.spend_cap == nok("1200")
    w.service.submit_receipt(refill.id, buyer, nok("900"), RECEIPT)

    assert refill.state is RefillState.CLAIM_SUBMITTED
    assert w.service.ledger.remaining(OSLO, month) == nok("-700")  # visible overspend


def test_hr_rejection_gives_the_money_back_to_the_budget():
    w = build_world()
    refill = complete_refill(w, OSLO, nok("850"))
    w.hr.reject(refill.claim_id)
    w.clock.advance(days=2)
    w.service.tick()

    assert refill.state is RefillState.REJECTED
    assert w.service.ledger.spent(OSLO, month_of(w, OSLO)) == nok("0")
    assert any("rejected" in m for m in w.chat.to("coord-oslo"))


# ---- currencies and offices --------------------------------------------------------
def test_each_office_is_budgeted_in_its_own_currency():
    w = build_world()
    complete_refill(w, OSLO, nok("850"))
    complete_refill(w, BENGALURU, inr("2400"))

    ledger = w.service.ledger
    assert ledger.spent(OSLO, month_of(w, OSLO)) == nok("850")
    assert ledger.spent(BENGALURU, month_of(w, BENGALURU)) == inr("2400")
    assert ledger.remaining(OSLO, month_of(w, OSLO)) == nok("5150")
    assert ledger.remaining(BENGALURU, month_of(w, BENGALURU)) == inr("12600")


def test_visitor_buys_in_local_currency_and_it_hits_the_visited_office_budget():
    w = build_world()
    for v in OSLO_VOLUNTEERS:
        w.presence.set_remote(v)
    w.presence.set_in_office("priya", OSLO)  # Bengaluru employee visiting Oslo

    refill = w.service.report_empty(OSLO, "anyone")
    assert refill.current_offer.volunteer_id == "priya"
    w.service.accept(refill.id, "priya")
    w.service.submit_receipt(refill.id, "priya", nok("640"), RECEIPT)

    claim = w.hr.claims[refill.claim_id]
    assert (claim.employee_id, claim.amount, claim.currency) == ("E-203", Decimal("640.00"), "NOK")
    assert w.service.ledger.spent(OSLO, month_of(w, OSLO)) == nok("640")
    assert w.service.ledger.spent(BENGALURU, month_of(w, BENGALURU)) == inr("0")
    assert any("pay you in INR" in m for m in w.chat.to("priya"))


def test_receipt_in_the_wrong_currency_is_refused():
    w = build_world()
    refill = w.service.report_empty(OSLO, "anyone")
    w.service.accept(refill.id, "kari")

    with pytest.raises(CurrencyMismatchError):
        w.service.submit_receipt(refill.id, "kari", inr("5000"), RECEIPT)
    assert refill.state is RefillState.COMMITTED


def test_budget_month_follows_each_office_local_time():
    # 30 Nov 22:30 UTC = 23:30 on 30 Nov in Oslo, but 04:00 on 1 Dec in Bengaluru.
    w = build_world(start=datetime(2026, 11, 30, 22, 30, tzinfo=timezone.utc))
    oslo = complete_refill(w, OSLO, nok("500"))
    blr = complete_refill(w, BENGALURU, inr("1000"))

    assert oslo.spend_month == "2026-11"
    assert blr.spend_month == "2026-12"
    assert w.service.ledger.spent(BENGALURU, "2026-11") == inr("0")


def test_commit_in_one_month_and_purchase_in_the_next_is_counted_at_purchase():
    # Commit 30 Nov 23:00 Oslo time, buy 1 Dec 10:00 Oslo time.
    w = build_world(start=datetime(2026, 11, 30, 22, 0, tzinfo=timezone.utc))
    refill = w.service.report_empty(OSLO, "anyone")
    w.service.accept(refill.id, "kari")
    assert w.service.ledger.reserved(OSLO, "2026-11") == nok("1200")

    w.clock.advance(hours=11)
    w.service.submit_receipt(refill.id, "kari", nok("700"), RECEIPT)

    ledger = w.service.ledger
    assert ledger.reserved(OSLO, "2026-11") == nok("0")
    assert ledger.spent(OSLO, "2026-11") == nok("0")
    assert ledger.spent(OSLO, "2026-12") == nok("700")
