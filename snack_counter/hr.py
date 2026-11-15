"""HR system boundary + a simulated stand-in.

What the brief says the real HR system accepts: amount, currency,
description, receipt image and employee ID. It approves in 1-10 days and pays
in the employee's payroll currency (the conversion is HR's job, not ours).

Assumption: it also offers a claim-status lookup we can poll. Submissions
carry an idempotency key (the refill id) so a retry after a timeout can never
create two claims for one purchase.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from itertools import count
from typing import Protocol


class ClaimStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    PAID = "paid"
    REJECTED = "rejected"


class HRUnavailableError(RuntimeError):
    """The external HR system could not be reached. Safe to retry later."""


@dataclass(frozen=True)
class ClaimRequest:
    employee_id: str
    amount: Decimal
    currency: str
    description: str
    receipt_image: bytes
    idempotency_key: str


class HRSystem(Protocol):
    def submit_claim(self, request: ClaimRequest) -> str: ...
    def claim_status(self, claim_id: str) -> ClaimStatus: ...


class FakeHR:
    def __init__(self) -> None:
        self.claims: dict[str, ClaimRequest] = {}
        self.statuses: dict[str, ClaimStatus] = {}
        self._by_key: dict[str, str] = {}
        self._ids = count(1)
        self.unavailable = False  # flip to simulate an outage

    # -- the API our system calls -----------------------------------------
    def submit_claim(self, request: ClaimRequest) -> str:
        if self.unavailable:
            raise HRUnavailableError("HR system timed out")
        if request.idempotency_key in self._by_key:
            return self._by_key[request.idempotency_key]
        claim_id = f"HR-{next(self._ids):04d}"
        self.claims[claim_id] = request
        self.statuses[claim_id] = ClaimStatus.PENDING
        self._by_key[request.idempotency_key] = claim_id
        return claim_id

    def claim_status(self, claim_id: str) -> ClaimStatus:
        if self.unavailable:
            raise HRUnavailableError("HR system timed out")
        return self.statuses[claim_id]

    # -- what HR staff do on their side (used by tests) -----------------
    def approve(self, claim_id: str) -> None:
        self.statuses[claim_id] = ClaimStatus.APPROVED

    def pay(self, claim_id: str) -> None:
        self.statuses[claim_id] = ClaimStatus.PAID

    def reject(self, claim_id: str) -> None:
        self.statuses[claim_id] = ClaimStatus.REJECTED
