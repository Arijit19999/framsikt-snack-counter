"""Monthly management report across both offices and currencies."""
from decimal import Decimal

import pytest

from snack_counter.config import BENGALURU, OSLO
from snack_counter.report import FxRates, MissingFxRateError, build_monthly_report, render_text
from tests.helpers import build_world, complete_refill, inr, nok

FX = FxRates("NOK", {("2026-11", "INR"): Decimal("0.125")})


def test_combined_total_converts_each_office_once_into_nok():
    w = build_world()
    complete_refill(w, OSLO, nok("850"), items=["Bananas", "Kvikk Lunsj"])
    complete_refill(w, OSLO, nok("1000.50"), items=["bananas"])
    complete_refill(w, BENGALURU, inr("2400"), items=["Parle-G"])
    complete_refill(w, BENGALURU, inr("1800"), items=["Murukku"])

    report = build_monthly_report(w.service, "2026-11", FX)
    oslo, blr = report.offices

    assert oslo.spent == nok("1850.50")
    assert oslo.spent_converted == nok("1850.50")
    assert blr.spent == inr("4200")
    assert blr.spent_converted == nok("525")
    assert report.total_spent == nok("2375.50")
    assert report.total_budget == nok("7875")  # 6,000 NOK + 15,000 INR x 0.125
    assert oslo.refills == 2 and blr.refills == 2
    assert oslo.top_items[0] == ("bananas", 2)


def test_report_refuses_to_guess_a_missing_exchange_rate():
    w = build_world()
    complete_refill(w, BENGALURU, inr("1000"))

    with pytest.raises(MissingFxRateError):
        build_monthly_report(w.service, "2026-11", FxRates("NOK", {}))


def test_claims_rejected_by_hr_are_not_reported_as_spend():
    w = build_world()
    kept = complete_refill(w, OSLO, nok("700"))
    rejected = complete_refill(w, OSLO, nok("900"))
    w.hr.reject(rejected.claim_id)
    w.service.tick()

    report = build_monthly_report(w.service, "2026-11", FX)
    assert report.offices[0].spent == nok("700")
    assert report.offices[0].refills == 1
    assert kept.claim_id in w.hr.claims


def test_other_months_are_not_included():
    w = build_world()
    complete_refill(w, OSLO, nok("700"))
    w.clock.advance(days=20)  # into December
    complete_refill(w, OSLO, nok("400"))

    report = build_monthly_report(w.service, "2026-11", FX)
    assert report.offices[0].spent == nok("700")


def test_report_text_shows_each_office_and_the_total():
    w = build_world()
    complete_refill(w, OSLO, nok("850"))
    complete_refill(w, BENGALURU, inr("2400"))

    text = render_text(build_monthly_report(w.service, "2026-11", FX))
    assert "Oslo: 850.00 NOK of 6,000.00 NOK" in text
    assert "Bengaluru: 2,400.00 INR of 15,000.00 INR" in text
    assert "= 300.00 NOK at 0.125 NOK/INR" in text
    assert "TOTAL: 1,150.00 NOK of 7,875.00 NOK" in text
