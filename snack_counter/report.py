"""One monthly report across both offices.

Each office is reported in its own currency (that is what its budget is in)
and converted once into a single reporting currency (NOK by assumption) using
one stored rate per month. The rate is printed in the report so the total can
be reproduced later. A missing rate fails loudly; there is no silent default.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from .money import Money
from .refill_service import RefillService


class MissingFxRateError(LookupError):
    pass


@dataclass(frozen=True)
class FxRates:
    """rates[(month, currency)] = units of reporting currency per 1 unit."""

    reporting_currency: str = "NOK"
    rates: dict[tuple[str, str], Decimal] = field(default_factory=dict)

    def rate(self, month: str, currency: str) -> Decimal:
        if currency == self.reporting_currency:
            return Decimal(1)
        try:
            return self.rates[(month, currency)]
        except KeyError:
            raise MissingFxRateError(
                f"No {currency}->{self.reporting_currency} rate stored for {month}"
            ) from None


@dataclass(frozen=True)
class OfficeMonth:
    office_name: str
    currency: str
    budget: Money
    spent: Money
    remaining: Money
    fx_rate: Decimal
    spent_converted: Money
    budget_converted: Money
    refills: int
    avg_hours_empty: Decimal | None
    avg_days_box_lasted: Decimal | None
    refills_by_volunteer: dict[str, int]
    top_items: list[tuple[str, int]]


@dataclass(frozen=True)
class MonthlyReport:
    month: str
    reporting_currency: str
    offices: list[OfficeMonth]
    total_spent: Money
    total_budget: Money


def _avg(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return (sum(values) / len(values)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def build_monthly_report(service: RefillService, month: str, fx: FxRates) -> MonthlyReport:
    reporting = fx.reporting_currency
    total_spent = Money.zero(reporting)
    total_budget = Money.zero(reporting)
    rows: list[OfficeMonth] = []

    for office in service.offices.values():
        rate = fx.rate(month, office.currency)
        spent = service.ledger.spent(office.id, month)
        budget = service.ledger.budget(office.id, month)
        spent_converted = spent.convert(rate, reporting)
        budget_converted = budget.convert(rate, reporting)
        total_spent = total_spent + spent_converted
        total_budget = total_budget + budget_converted

        # Refills whose purchase counts in this month. Claims HR rejected are
        # not here because the ledger already reversed them.
        refill_ids = set(service.ledger.spend_entries(office.id, month))
        refills = [service.refills[rid] for rid in sorted(refill_ids)]

        hours_empty = [
            Decimal((r.receipt.purchased_at - r.reported_at).total_seconds()) / 3600
            for r in refills
        ]

        # How long a full box lasts: from a refill until the next "empty" report.
        office_refills = sorted(
            (r for r in service.refills.values() if r.office_id == office.id),
            key=lambda r: r.reported_at,
        )
        days_lasted = []
        for this, nxt in zip(office_refills, office_refills[1:]):
            if this.id in refill_ids:
                seconds = (nxt.reported_at - this.receipt.purchased_at).total_seconds()
                days_lasted.append(Decimal(seconds) / 86400)

        by_volunteer = Counter(service.volunteers[r.assignee_id].name for r in refills)
        items = Counter(item.strip().lower() for r in refills for item in r.receipt.items)

        rows.append(
            OfficeMonth(
                office_name=office.name,
                currency=office.currency,
                budget=budget,
                spent=spent,
                remaining=service.ledger.remaining(office.id, month),
                fx_rate=rate,
                spent_converted=spent_converted,
                budget_converted=budget_converted,
                refills=len(refills),
                avg_hours_empty=_avg(hours_empty),
                avg_days_box_lasted=_avg(days_lasted),
                refills_by_volunteer=dict(by_volunteer.most_common()),
                top_items=items.most_common(5),
            )
        )

    return MonthlyReport(month, reporting, rows, total_spent, total_budget)


def render_text(report: MonthlyReport) -> str:
    cur = report.reporting_currency
    lines = [f"Snack spending report {report.month} (reporting currency {cur})"]
    for o in report.offices:
        pct = (o.spent.amount / o.budget.amount * 100).quantize(Decimal("0.1"))
        converted = (
            f" = {o.spent_converted} at {o.fx_rate} {cur}/{o.currency}"
            if o.currency != cur
            else ""
        )
        lines.append(
            f"- {o.office_name}: {o.spent} of {o.budget} ({pct}%), "
            f"{o.refills} refill{'' if o.refills == 1 else 's'}{converted}"
        )
        if o.avg_hours_empty is not None:
            lines.append(f"    box empty on average {o.avg_hours_empty} h before refill")
        if o.avg_days_box_lasted is not None:
            lines.append(f"    a full box lasts on average {o.avg_days_box_lasted} days")
        if o.refills_by_volunteer:
            who = ", ".join(f"{n} x{c}" for n, c in o.refills_by_volunteer.items())
            lines.append(f"    refills by: {who}")
        if o.top_items:
            lines.append("    most bought: " + ", ".join(f"{i} x{c}" for i, c in o.top_items))
    lines.append(f"TOTAL: {report.total_spent} of {report.total_budget} combined budget")
    return "\n".join(lines)
