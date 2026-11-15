"""Monthly budget ledger, one per office, each in its own currency.

remaining = monthly budget - spent - reserved

* reserved: the spend cap held for a refill someone has committed to but not
  bought yet, so the number people see already accounts for it.
* spent: actual receipt amounts, counted in the month of purchase in the
  office's local time. Reversed if HR rejects the claim.

Every entry is keyed by refill id, which makes all operations idempotent:
recording the same purchase twice cannot double-count it.
"""
from __future__ import annotations

from .models import Office
from .money import CurrencyMismatchError, Money


class BudgetLedger:
    def __init__(self, offices: dict[str, Office]) -> None:
        self._offices = offices
        self._reserved: dict[tuple[str, str], dict[str, Money]] = {}
        self._spent: dict[tuple[str, str], dict[str, Money]] = {}

    def _office(self, office_id: str) -> Office:
        return self._offices[office_id]

    def _sum(self, entries: dict[str, Money], currency: str) -> Money:
        total = Money.zero(currency)
        for amount in entries.values():
            total = total + amount
        return total

    # ---- queries ----------------------------------------------------
    def budget(self, office_id: str, month: str) -> Money:
        return self._office(office_id).monthly_budget

    def spent(self, office_id: str, month: str) -> Money:
        office = self._office(office_id)
        return self._sum(self._spent.get((office_id, month), {}), office.currency)

    def reserved(self, office_id: str, month: str) -> Money:
        office = self._office(office_id)
        return self._sum(self._reserved.get((office_id, month), {}), office.currency)

    def remaining(self, office_id: str, month: str) -> Money:
        """Can be negative after a coordinator-approved overspend."""
        return (
            self.budget(office_id, month)
            - self.spent(office_id, month)
            - self.reserved(office_id, month)
        )

    def spend_entries(self, office_id: str, month: str) -> dict[str, Money]:
        return dict(self._spent.get((office_id, month), {}))

    # ---- commands ---------------------------------------------------
    def _check_currency(self, office_id: str, amount: Money) -> None:
        expected = self._office(office_id).currency
        if amount.currency != expected:
            raise CurrencyMismatchError(
                f"{office_id} budget is in {expected}, got {amount.currency}"
            )

    def reserve(self, office_id: str, month: str, refill_id: str, amount: Money) -> None:
        self._check_currency(office_id, amount)
        self._reserved.setdefault((office_id, month), {})[refill_id] = amount

    def release(self, office_id: str, month: str, refill_id: str) -> None:
        self._reserved.get((office_id, month), {}).pop(refill_id, None)

    def record_spend(self, office_id: str, month: str, refill_id: str, amount: Money) -> None:
        self._check_currency(office_id, amount)
        self._spent.setdefault((office_id, month), {})[refill_id] = amount

    def reverse_spend(self, office_id: str, month: str, refill_id: str) -> None:
        self._spent.get((office_id, month), {}).pop(refill_id, None)
