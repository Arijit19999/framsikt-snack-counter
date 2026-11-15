"""Choosing who buys: presence, fairness, and people still owed money."""
from datetime import timedelta

from snack_counter.config import BENGALURU, OSLO
from snack_counter.presence import FakePresence
from snack_counter.selection import VolunteerStats, choose_buyer
from tests.helpers import (
    OSLO_VOLUNTEERS,
    START,
    VOLUNTEERS,
    build_world,
    complete_refill,
    nok,
)


def pick(presence, stats=None, excluded=None, office=OSLO):
    chosen = choose_buyer(office, VOLUNTEERS, presence, stats or {}, excluded or set())
    return chosen.id if chosen else None


def test_only_people_in_that_office_today_are_asked():
    presence = FakePresence(VOLUNTEERS)
    presence.set_remote("kari")
    presence.set_on_leave("lars")

    assert pick(presence) == "nora"


def test_fewest_recent_refills_goes_first():
    presence = FakePresence(VOLUNTEERS)
    stats = {
        "kari": VolunteerStats(refills_in_window=3),
        "lars": VolunteerStats(refills_in_window=1),
        "nora": VolunteerStats(refills_in_window=2),
        "ola": VolunteerStats(refills_in_window=2),
    }
    assert pick(presence, stats) == "lars"


def test_someone_still_waiting_for_money_is_not_asked_to_pay_again():
    presence = FakePresence(VOLUNTEERS)
    stats = {
        "kari": VolunteerStats(refills_in_window=0, has_unpaid_claim=True),
        "lars": VolunteerStats(refills_in_window=2),
        "nora": VolunteerStats(refills_in_window=2),
        "ola": VolunteerStats(refills_in_window=2),
    }
    assert pick(presence, stats) != "kari"


def test_tie_goes_to_whoever_refilled_longest_ago():
    presence = FakePresence(VOLUNTEERS)
    stats = {
        "kari": VolunteerStats(1, START - timedelta(days=2)),
        "lars": VolunteerStats(1, START - timedelta(days=20)),
        "nora": VolunteerStats(1, START - timedelta(days=5)),
        "ola": VolunteerStats(1, START - timedelta(days=9)),
    }
    assert pick(presence, stats) == "lars"


def test_visitor_counts_for_the_office_they_are_visiting():
    presence = FakePresence(VOLUNTEERS)
    for v in OSLO_VOLUNTEERS:
        presence.set_remote(v)
    presence.set_in_office("priya", OSLO)  # Bengaluru employee visiting Oslo

    assert pick(presence, office=OSLO) == "priya"
    assert pick(presence, office=BENGALURU) == "arjun"  # priya is not in Bengaluru


def test_excluded_people_are_skipped_and_none_when_nobody_left():
    presence = FakePresence(VOLUNTEERS)
    assert pick(presence, excluded={"kari", "lars"}) == "nora"
    assert pick(presence, excluded=set(OSLO_VOLUNTEERS)) is None


def test_refills_rotate_across_volunteers_over_a_month():
    w = build_world()
    buyers = []
    for _ in range(4):
        refill = complete_refill(w, OSLO, nok("700"))
        buyers.append(refill.assignee_id)
        w.hr.pay(refill.claim_id)
        w.clock.advance(days=4)
        w.service.tick()

    assert sorted(buyers) == sorted(OSLO_VOLUNTEERS)  # everyone did one, nobody two
