"""Refill lifecycle: empty -> offered -> committed -> purchased -> reimbursed."""
import threading
import time

import pytest

from snack_counter.config import OSLO
from snack_counter.models import OfferOutcome, RefillState
from snack_counter.refill_service import (
    AlreadyTakenError,
    InvalidStateError,
    NotAllowedError,
)
from tests.helpers import RECEIPT, build_world, complete_refill, nok


# ---- reporting ----------------------------------------------------------
def test_empty_report_offers_the_refill_to_exactly_one_volunteer():
    w = build_world()
    refill = w.service.report_empty(OSLO, reported_by="anyone")

    assert refill.state is RefillState.OFFERED
    assert refill.current_offer.volunteer_id == "kari"
    assert len(w.chat.to("kari")) == 1
    assert "empty" in w.chat.to("kari")[0]


def test_second_empty_report_does_not_start_a_second_refill():
    w = build_world()
    first = w.service.report_empty(OSLO, reported_by="anyone")
    second = w.service.report_empty(OSLO, reported_by="ola")

    assert second.id == first.id
    assert len(w.service.refills) == 1
    assert "already being handled" in w.chat.to("ola")[0]


def test_new_refill_can_start_once_the_box_has_been_refilled():
    w = build_world()
    first = complete_refill(w, OSLO, nok("800"))
    second = w.service.report_empty(OSLO, reported_by="anyone")

    assert second.id != first.id
    assert second.state is RefillState.OFFERED


# ---- only one buyer -----------------------------------------------------------
def test_two_people_cannot_both_commit_to_the_same_refill():
    w = build_world()
    refill = w.service.report_empty(OSLO, reported_by="anyone")

    w.service.accept(refill.id, "kari")
    with pytest.raises(AlreadyTakenError):
        w.service.accept(refill.id, "ola")

    assert refill.assignee_id == "kari"
    month = w.service.offices[OSLO].local_month(w.clock.now())
    assert w.service.ledger.reserved(OSLO, month) == nok("1200")  # reserved once


class SlowPresence:
    """Makes the eligibility check slow, so without proper locking two
    accepts would both pass the "is it still free?" check before either one
    writes. This forces the race to actually happen in the test."""

    def __init__(self, inner):
        self.inner = inner

    def presence_of(self, volunteer_id):
        time.sleep(0.02)
        return self.inner.presence_of(volunteer_id)


@pytest.mark.parametrize("attempt", range(10))
def test_simultaneous_accepts_only_one_wins(attempt):
    w = build_world()
    for v in ("kari", "lars", "nora", "ola"):
        w.presence.set_remote(v)
    refill = w.service.report_empty(OSLO, reported_by="anyone")
    assert refill.state is RefillState.OPEN_TO_ALL  # anyone may grab it
    w.service.presence = SlowPresence(w.presence)
    barrier = threading.Barrier(2)
    results: dict[str, str] = {}

    def try_accept(volunteer_id: str) -> None:
        barrier.wait()
        try:
            w.service.accept(refill.id, volunteer_id)
            results[volunteer_id] = "won"
        except AlreadyTakenError:
            results[volunteer_id] = "lost"

    threads = [threading.Thread(target=try_accept, args=(v,)) for v in ("kari", "ola")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results.values()) == ["lost", "won"]
    winner = next(v for v, r in results.items() if r == "won")
    assert refill.assignee_id == winner


def test_someone_else_in_the_office_can_take_an_offered_refill():
    w = build_world()
    refill = w.service.report_empty(OSLO, reported_by="anyone")  # offered to kari

    w.service.accept(refill.id, "ola")

    assert refill.assignee_id == "ola"
    assert refill.offers[0].outcome is OfferOutcome.TAKEN_BY_OTHER
    assert any("No need to buy" in m for m in w.chat.to("kari"))


def test_volunteer_of_the_other_office_cannot_take_the_refill():
    w = build_world()
    refill = w.service.report_empty(OSLO, reported_by="anyone")

    with pytest.raises(NotAllowedError):
        w.service.accept(refill.id, "priya")  # Bengaluru-based, sitting in Bengaluru


def test_commit_is_announced_in_the_channel_so_nobody_double_buys():
    w = build_world()
    refill = w.service.report_empty(OSLO, reported_by="anyone")
    w.service.accept(refill.id, "kari")

    assert any("Kari is refilling" in m for m in w.chat.channel("snacks-oslo"))


# ---- nobody left hanging ------------------------------------------------------
def test_decline_passes_the_refill_to_the_next_volunteer():
    w = build_world()
    refill = w.service.report_empty(OSLO, reported_by="anyone")
    w.service.decline(refill.id, "kari")

    assert refill.current_offer.volunteer_id == "lars"
    assert "kari" in refill.excluded


def test_unanswered_offer_expires_and_moves_on():
    w = build_world()
    refill = w.service.report_empty(OSLO, reported_by="anyone")

    w.clock.advance(hours=2, minutes=59)
    w.service.tick()
    assert refill.current_offer.volunteer_id == "kari"  # not yet

    w.clock.advance(minutes=1)
    w.service.tick()
    assert refill.current_offer.volunteer_id == "lars"
    assert refill.offers[0].outcome is OfferOutcome.EXPIRED


def test_after_three_failed_offers_it_is_opened_to_everyone_and_coordinator_told():
    w = build_world()
    refill = w.service.report_empty(OSLO, reported_by="anyone")
    for volunteer in ("kari", "lars", "nora"):
        w.service.decline(refill.id, volunteer)

    assert refill.state is RefillState.OPEN_TO_ALL
    assert any("needs a volunteer" in m for m in w.chat.channel("snacks-oslo"))
    assert any("No volunteer found" in m for m in w.chat.to("coord-oslo"))

    w.service.accept(refill.id, "ola")  # first taker wins
    assert refill.state is RefillState.COMMITTED


def test_nobody_in_the_office_goes_straight_to_the_channel():
    w = build_world()
    for v in ("kari", "lars", "nora", "ola"):
        w.presence.set_remote(v)

    refill = w.service.report_empty(OSLO, reported_by="anyone")
    assert refill.state is RefillState.OPEN_TO_ALL
    assert refill.offers == []


def test_refill_nobody_completes_is_handed_to_someone_else():
    w = build_world()
    refill = w.service.report_empty(OSLO, reported_by="anyone")
    w.service.accept(refill.id, "kari")
    month = w.service.offices[OSLO].local_month(w.clock.now())

    w.clock.advance(hours=24)
    w.service.tick()

    assert refill.state is RefillState.OFFERED
    assert refill.current_offer.volunteer_id == "lars"
    assert refill.assignee_id is None
    assert "kari" in refill.excluded
    assert w.service.ledger.reserved(OSLO, month) == nok("0")  # reservation released
    assert any("deadline" in m for m in w.chat.to("kari"))


def test_dropped_buyer_cannot_submit_a_late_receipt():
    w = build_world()
    refill = w.service.report_empty(OSLO, reported_by="anyone")
    w.service.accept(refill.id, "kari")
    w.clock.advance(hours=25)
    w.service.tick()

    with pytest.raises(InvalidStateError):
        w.service.submit_receipt(refill.id, "kari", nok("500"), RECEIPT)


def test_buyer_gets_one_reminder_before_the_deadline():
    w = build_world()
    refill = w.service.report_empty(OSLO, reported_by="anyone")
    w.service.accept(refill.id, "kari")

    w.clock.advance(hours=20)
    w.service.tick()
    w.clock.advance(hours=1)
    w.service.tick()

    reminders = [m for m in w.chat.to("kari") if m.startswith("Reminder")]
    assert len(reminders) == 1


def test_only_the_assignee_can_submit_the_receipt():
    w = build_world()
    refill = w.service.report_empty(OSLO, reported_by="anyone")
    w.service.accept(refill.id, "kari")

    with pytest.raises(NotAllowedError):
        w.service.submit_receipt(refill.id, "ola", nok("500"), RECEIPT)


def test_receipt_photo_is_required():
    w = build_world()
    refill = w.service.report_empty(OSLO, reported_by="anyone")
    w.service.accept(refill.id, "kari")

    with pytest.raises(ValueError):
        w.service.submit_receipt(refill.id, "kari", nok("500"), b"")


# ---- reimbursement --------------------------------------------------------------
def test_full_path_from_empty_to_reimbursed():
    w = build_world()
    refill = complete_refill(w, OSLO, nok("850"), items=["Bananas", "Kvikk Lunsj"])

    assert refill.state is RefillState.CLAIM_SUBMITTED
    assert any("sent to HR" in m for m in w.chat.to("kari"))

    w.hr.approve(refill.claim_id)
    w.clock.advance(days=3)
    w.service.tick()
    assert refill.state is RefillState.APPROVED

    w.hr.pay(refill.claim_id)
    w.clock.advance(days=2)
    w.service.tick()
    assert refill.state is RefillState.REIMBURSED
    assert any("paid out" in m for m in w.chat.to("kari"))


def test_hr_outage_is_retried_without_creating_duplicate_claims():
    w = build_world()
    w.hr.unavailable = True
    refill = complete_refill(w, OSLO, nok("850"))
    assert refill.state is RefillState.PURCHASED

    w.clock.advance(minutes=5)
    w.service.tick()
    assert refill.state is RefillState.PURCHASED

    w.hr.unavailable = False
    w.service.tick()
    w.service.tick()
    assert refill.state is RefillState.CLAIM_SUBMITTED
    assert len(w.hr.claims) == 1


def test_coordinator_is_nudged_once_when_hr_is_slow():
    w = build_world()
    refill = complete_refill(w, OSLO, nok("850"))

    w.clock.advance(days=4)
    w.service.tick()
    assert not any("pending" in m for m in w.chat.to("coord-oslo"))

    w.clock.advance(days=1)
    w.service.tick()
    w.clock.advance(days=1)
    w.service.tick()
    nudges = [m for m in w.chat.to("coord-oslo") if "pending" in m]
    assert len(nudges) == 1
    assert refill.claim_id in nudges[0]
