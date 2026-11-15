"""The refill lifecycle: from "box is empty" to "volunteer reimbursed".

    OFFERED --accept--> COMMITTED --receipt--> PURCHASED --> CLAIM_SUBMITTED
       |  decline/timeout    |  deadline missed      ^            |
       v                     v                       |            v
    next volunteer      re-offered            NEEDS_REVIEW     APPROVED --> REIMBURSED
       |  (after 3)                          (over the cap)
       v
    OPEN_TO_ALL (channel + coordinator)

    BLOCKED_BUDGET: not enough budget left; the coordinator approves or not.

Every command runs under one lock, so check-and-update is atomic: two people
cannot both commit to the same refill. In production this would be a single
conditional UPDATE in the database ("... WHERE id = ? AND state IN (...)").

`tick()` is called by a scheduler (say every 5 minutes). It drives every
time-based rule: offer timeouts, buying deadlines, reminders, HR retries and
HR status polling.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import count
from typing import Iterable
from zoneinfo import ZoneInfo

from .budget import BudgetLedger
from .chat import ChatTool
from .clock import Clock
from .hr import ClaimRequest, ClaimStatus, HRSystem, HRUnavailableError
from .models import (
    BOX_EMPTY_STATES,
    UNPAID_STATES,
    Offer,
    OfferOutcome,
    Office,
    Receipt,
    Refill,
    RefillState,
    Volunteer,
)
from .money import CurrencyMismatchError, Money
from .presence import PresenceDirectory
from .selection import VolunteerStats, choose_buyer


class RefillError(Exception):
    """Base class: the message is safe to show to the user in chat."""


class NotFoundError(RefillError):
    pass


class InvalidStateError(RefillError):
    pass


class AlreadyTakenError(RefillError):
    pass


class NotAllowedError(RefillError):
    pass


class BudgetExhaustedError(RefillError):
    pass


@dataclass(frozen=True)
class Settings:
    offer_timeout: timedelta = timedelta(hours=3)
    buy_deadline: timedelta = timedelta(hours=24)
    reminder_before_deadline: timedelta = timedelta(hours=4)
    max_direct_offers: int = 3
    fairness_window: timedelta = timedelta(days=30)
    hr_nudge_after: timedelta = timedelta(days=5)


class RefillService:
    def __init__(
        self,
        offices: Iterable[Office],
        volunteers: Iterable[Volunteer],
        chat: ChatTool,
        hr: HRSystem,
        presence: PresenceDirectory,
        clock: Clock,
        settings: Settings | None = None,
    ) -> None:
        self.offices = {o.id: o for o in offices}
        self.volunteers = {v.id: v for v in volunteers}
        self.chat = chat
        self.hr = hr
        self.presence = presence
        self.clock = clock
        self.settings = settings or Settings()
        self.ledger = BudgetLedger(self.offices)
        self.refills: dict[str, Refill] = {}
        self._ids = count(1)
        self._lock = threading.RLock()

    # ================================================================
    # Queries
    # ================================================================
    def get(self, refill_id: str) -> Refill:
        try:
            return self.refills[refill_id]
        except KeyError:
            raise NotFoundError(f"No refill {refill_id}") from None

    def active_refill(self, office_id: str) -> Refill | None:
        for refill in self.refills.values():
            if refill.office_id == office_id and refill.state in BOX_EMPTY_STATES:
                return refill
        return None

    # ================================================================
    # Commands (each one is a chat command or a coordinator action)
    # ================================================================
    def report_empty(self, office_id: str, reported_by: str) -> Refill:
        """Anyone (not only volunteers) can say "the box is empty"."""
        with self._lock:
            office = self._office(office_id)
            now = self.clock.now()

            existing = self.active_refill(office_id)
            if existing is not None:
                existing.log(now, f"duplicate empty report from {reported_by}")
                self.chat.send_direct(
                    reported_by,
                    f"Thanks! The {office.name} box is already being handled: "
                    f"{self._describe(existing)}.",
                )
                return existing

            refill = Refill(
                id=f"R-{next(self._ids):04d}",
                office_id=office_id,
                reported_by=reported_by,
                reported_at=now,
                state=RefillState.OFFERED,
            )
            self.refills[refill.id] = refill
            refill.log(now, f"reported empty by {reported_by}")

            month = office.local_month(now)
            remaining = self.ledger.remaining(office_id, month)
            if remaining < office.typical_min:
                self._block_for_budget(refill, office, remaining, now)
                return refill

            self._offer_next(refill, now)
            return refill

    def accept(self, refill_id: str, volunteer_id: str) -> Refill:
        """Commit to buying. The first valid accept wins; later ones fail."""
        with self._lock:
            refill = self.get(refill_id)
            now = self.clock.now()
            if refill.state is RefillState.COMMITTED:
                raise AlreadyTakenError(
                    f"{self._name(refill.assignee_id)} is already buying for this refill"
                )
            if refill.state not in (RefillState.OFFERED, RefillState.OPEN_TO_ALL):
                raise InvalidStateError(
                    f"Refill {refill.id} cannot be accepted (it is {refill.state.value})"
                )
            volunteer = self._volunteer(volunteer_id)
            office = self._office(refill.office_id)
            offer = refill.current_offer
            is_offeree = offer is not None and offer.volunteer_id == volunteer_id
            if not is_offeree and not self._can_take(volunteer, office.id):
                raise NotAllowedError(
                    f"Only volunteers based in or visiting {office.name} can take this refill"
                )

            cap = self._spend_cap(refill, office, now)
            if not refill.budget_override and cap < office.typical_min:
                # The budget changed since the box was reported (e.g. an
                # over-cap purchase was approved in the meantime).
                if offer is not None:
                    offer.outcome = OfferOutcome.EXPIRED
                self._block_for_budget(refill, office, cap, now)
                raise BudgetExhaustedError(
                    f"The {office.name} snack budget is used up; the coordinator has been asked"
                )

            if offer is not None:
                offer.outcome = OfferOutcome.ACCEPTED if is_offeree else OfferOutcome.TAKEN_BY_OTHER
                if not is_offeree:
                    self.chat.send_direct(
                        offer.volunteer_id,
                        f"No need to buy for {refill.id}: {volunteer.name} has taken it.",
                    )

            month = office.local_month(now)
            self.ledger.reserve(office.id, month, refill.id, cap)
            refill.state = RefillState.COMMITTED
            refill.assignee_id = volunteer.id
            refill.committed_at = now
            refill.buy_deadline = now + self.settings.buy_deadline
            refill.reminder_sent = False
            refill.spend_cap = cap
            refill.reservation_month = month
            refill.log(now, f"{volunteer.id} committed, cap {cap}")

            self.chat.send_direct(
                volunteer.id,
                f"Thanks {volunteer.name}! You're buying for the {office.name} box ({refill.id}). "
                f"Spend up to {cap} and send the receipt photo by "
                f"{self._local(refill.buy_deadline, office)}.",
            )
            self.chat.post_to_channel(
                office.channel,
                f"{volunteer.name} is refilling the {office.name} box. "
                "Please don't buy snacks for it.",
            )
            return refill

    def decline(self, refill_id: str, volunteer_id: str) -> Refill:
        with self._lock:
            refill = self.get(refill_id)
            now = self.clock.now()
            offer = refill.current_offer
            if refill.state is not RefillState.OFFERED or offer is None:
                raise InvalidStateError(f"Refill {refill.id} has no open offer")
            if offer.volunteer_id != volunteer_id:
                raise NotAllowedError("This offer was not sent to you")
            offer.outcome = OfferOutcome.DECLINED
            refill.excluded.add(volunteer_id)
            refill.log(now, f"{volunteer_id} declined")
            self._offer_next(refill, now)
            return refill

    def submit_receipt(
        self,
        refill_id: str,
        volunteer_id: str,
        amount: Money,
        image: bytes,
        items: Iterable[str] = (),
    ) -> Refill:
        """The volunteer has bought and refilled. The box is full again."""
        with self._lock:
            refill = self.get(refill_id)
            now = self.clock.now()
            if refill.state is not RefillState.COMMITTED:
                raise InvalidStateError(
                    f"Refill {refill.id} is not waiting for a receipt (it is {refill.state.value})"
                )
            if volunteer_id != refill.assignee_id:
                raise NotAllowedError(
                    f"Only {self._name(refill.assignee_id)} can submit the receipt for {refill.id}"
                )
            office = self._office(refill.office_id)
            if amount.currency != office.currency:
                raise CurrencyMismatchError(
                    f"The {office.name} box is budgeted in {office.currency}; "
                    f"the receipt is in {amount.currency}"
                )
            if not amount.is_positive:
                raise ValueError("Receipt amount must be positive")
            if not image:
                raise ValueError("A receipt photo is required for the HR claim")

            refill.receipt = Receipt(amount, bytes(image), tuple(items), now)
            refill.spend_month = office.local_month(now)
            self.ledger.release(office.id, refill.reservation_month, refill.id)
            refill.log(now, f"receipt {amount}")
            self.chat.post_to_channel(
                office.channel,
                f"The {office.name} box has been refilled by {self._name(volunteer_id)}. Thanks!",
            )

            if amount > refill.spend_cap:
                refill.state = RefillState.NEEDS_REVIEW
                # Hold the amount so "remaining" stays honest while it is decided.
                self.ledger.reserve(office.id, refill.spend_month, refill.id, amount)
                self.chat.send_direct(
                    office.coordinator_id,
                    f"{self._name(volunteer_id)} spent {amount} on {refill.id}, above the "
                    f"agreed cap of {refill.spend_cap}. Please approve or reject.",
                )
                self.chat.send_direct(
                    volunteer_id,
                    f"Your receipt is above the {refill.spend_cap} cap, so the coordinator "
                    "will review it before it goes to HR.",
                )
            else:
                self._record_spend_and_claim(refill, office, now)
            return refill

    # ---- coordinator actions ----------------------------------------
    def approve_budget_override(self, refill_id: str, coordinator_id: str) -> Refill:
        with self._lock:
            refill = self.get(refill_id)
            office = self._require_coordinator(refill, coordinator_id)
            if refill.state is not RefillState.BLOCKED_BUDGET:
                raise InvalidStateError(f"Refill {refill.id} is not blocked on budget")
            now = self.clock.now()
            refill.budget_override = True
            refill.log(now, f"budget override by {coordinator_id}")
            self._offer_next(refill, now)
            return refill

    def approve_review(self, refill_id: str, coordinator_id: str) -> Refill:
        with self._lock:
            refill = self.get(refill_id)
            office = self._require_coordinator(refill, coordinator_id)
            if refill.state is not RefillState.NEEDS_REVIEW:
                raise InvalidStateError(f"Refill {refill.id} is not waiting for review")
            now = self.clock.now()
            self.ledger.release(office.id, refill.spend_month, refill.id)
            refill.log(now, f"over-cap receipt approved by {coordinator_id}")
            self._record_spend_and_claim(refill, office, now)
            return refill

    def reject_review(self, refill_id: str, coordinator_id: str, reason: str) -> Refill:
        with self._lock:
            refill = self.get(refill_id)
            office = self._require_coordinator(refill, coordinator_id)
            if refill.state is not RefillState.NEEDS_REVIEW:
                raise InvalidStateError(f"Refill {refill.id} is not waiting for review")
            now = self.clock.now()
            self.ledger.release(office.id, refill.spend_month, refill.id)
            refill.state = RefillState.REJECTED
            refill.log(now, f"rejected by {coordinator_id}: {reason}")
            self.chat.send_direct(
                refill.assignee_id, f"Your receipt for {refill.id} was not approved: {reason}"
            )
            return refill

    # ================================================================
    # Scheduler entry point: all time-based rules live here
    # ================================================================
    def tick(self) -> None:
        with self._lock:
            now = self.clock.now()
            for refill in list(self.refills.values()):
                if refill.state is RefillState.OFFERED:
                    self._expire_offer_if_due(refill, now)
                elif refill.state is RefillState.BLOCKED_BUDGET:
                    self._unblock_if_budget_available(refill, now)
                elif refill.state is RefillState.COMMITTED:
                    self._check_buy_deadline(refill, now)
                elif refill.state is RefillState.PURCHASED:
                    self._submit_claim(refill, self._office(refill.office_id), now)
                elif refill.state in (RefillState.CLAIM_SUBMITTED, RefillState.APPROVED):
                    self._sync_claim(refill, now)

    # ================================================================
    # Internals
    # ================================================================
    def _offer_next(self, refill: Refill, now: datetime) -> None:
        office = self._office(refill.office_id)
        if len(refill.offers) >= self.settings.max_direct_offers:
            self._open_to_all(refill, office, now, f"{len(refill.offers)} volunteers couldn't take it")
            return
        buyer = choose_buyer(
            office.id,
            self.volunteers.values(),
            self.presence,
            self._stats(now),
            refill.excluded,
        )
        if buyer is None:
            self._open_to_all(refill, office, now, "no other volunteer is in the office today")
            return
        refill.offers.append(Offer(buyer.id, now, now + self.settings.offer_timeout))
        refill.state = RefillState.OFFERED
        refill.log(now, f"offered to {buyer.id}")
        hours = int(self.settings.offer_timeout.total_seconds() // 3600)
        self.chat.send_direct(
            buyer.id,
            f"The {office.name} snack box is empty. Could you refill it ({refill.id})? "
            f"Spend up to {self._spend_cap(refill, office, now)}. "
            f"Please accept or decline within {hours} hours.",
        )

    def _open_to_all(self, refill: Refill, office: Office, now: datetime, reason: str) -> None:
        refill.state = RefillState.OPEN_TO_ALL
        refill.log(now, f"open to all: {reason}")
        self.chat.post_to_channel(
            office.channel,
            f"The {office.name} snack box is empty and needs a volunteer ({refill.id}). "
            "The first person to accept gets it.",
        )
        self.chat.send_direct(
            office.coordinator_id,
            f"No volunteer found for {refill.id} in {office.name} ({reason}). "
            "It has been posted to the volunteer channel.",
        )

    def _block_for_budget(self, refill: Refill, office: Office, remaining: Money, now: datetime) -> None:
        refill.state = RefillState.BLOCKED_BUDGET
        refill.log(now, f"blocked: only {remaining} left")
        self.chat.send_direct(
            office.coordinator_id,
            f"The {office.name} snack box is empty but only {remaining} of this month's "
            f"budget is left. Approve {refill.id} to allow one more refill.",
        )
        self.chat.post_to_channel(
            office.channel,
            f"The {office.name} snack box is empty, but this month's snack budget is used up. "
            "The coordinator has been asked. Please don't buy for now.",
        )

    def _unblock_if_budget_available(self, refill: Refill, now: datetime) -> None:
        """A blocked refill must not wait for the coordinator forever: when a
        new month starts (or an HR rejection frees money), restart it."""
        office = self._office(refill.office_id)
        remaining = self.ledger.remaining(office.id, office.local_month(now))
        if remaining < office.typical_min:
            return
        refill.log(now, f"budget available again ({remaining}), unblocked")
        self.chat.send_direct(
            office.coordinator_id,
            f"{refill.id}: budget is available again, so the {office.name} refill "
            "has restarted. No approval needed.",
        )
        self._offer_next(refill, now)

    def _expire_offer_if_due(self, refill: Refill, now: datetime) -> None:
        offer = refill.current_offer
        if offer is None or now < offer.expires_at:
            return
        offer.outcome = OfferOutcome.EXPIRED
        refill.excluded.add(offer.volunteer_id)
        refill.log(now, f"offer to {offer.volunteer_id} expired")
        self.chat.send_direct(
            offer.volunteer_id,
            f"No worries, {refill.id} has been passed to someone else.",
        )
        self._offer_next(refill, now)

    def _check_buy_deadline(self, refill: Refill, now: datetime) -> None:
        office = self._office(refill.office_id)
        if now >= refill.buy_deadline:
            dropped = refill.assignee_id
            self.ledger.release(office.id, refill.reservation_month, refill.id)
            refill.excluded.add(dropped)
            refill.assignee_id = None
            refill.committed_at = None
            refill.buy_deadline = None
            refill.spend_cap = None
            refill.reservation_month = None
            refill.log(now, f"{dropped} missed the buying deadline")
            self.chat.send_direct(
                dropped,
                f"The deadline for {refill.id} passed, so it has been handed to someone else. "
                "If you already bought snacks, please contact the coordinator.",
            )
            self._offer_next(refill, now)
        elif not refill.reminder_sent and now >= refill.buy_deadline - self.settings.reminder_before_deadline:
            refill.reminder_sent = True
            self.chat.send_direct(
                refill.assignee_id,
                f"Reminder: {refill.id} needs the receipt by {self._local(refill.buy_deadline, office)}, "
                "otherwise it will be handed to someone else.",
            )

    def _record_spend_and_claim(self, refill: Refill, office: Office, now: datetime) -> None:
        self.ledger.record_spend(office.id, refill.spend_month, refill.id, refill.receipt.amount)
        refill.state = RefillState.PURCHASED
        self._submit_claim(refill, office, now)

    def _submit_claim(self, refill: Refill, office: Office, now: datetime) -> None:
        volunteer = self._volunteer(refill.assignee_id)
        receipt = refill.receipt
        items = ", ".join(receipt.items) or "snacks"
        request = ClaimRequest(
            employee_id=volunteer.employee_id,
            amount=receipt.amount.amount,
            currency=receipt.amount.currency,
            description=f"Snack counter refill {refill.id}, {office.name} office: {items}",
            receipt_image=receipt.image,
            idempotency_key=refill.id,
        )
        try:
            claim_id = self.hr.submit_claim(request)
        except HRUnavailableError:
            refill.log(now, "HR unavailable, will retry")
            return
        refill.claim_id = claim_id
        refill.claim_submitted_at = now
        refill.state = RefillState.CLAIM_SUBMITTED
        refill.log(now, f"claim {claim_id} submitted")
        payroll_note = (
            ""
            if volunteer.payroll_currency == receipt.amount.currency
            else f" HR will pay you in {volunteer.payroll_currency}."
        )
        self.chat.send_direct(
            volunteer.id,
            f"Your claim {claim_id} for {receipt.amount} has been sent to HR.{payroll_note}",
        )

    def _sync_claim(self, refill: Refill, now: datetime) -> None:
        try:
            status = self.hr.claim_status(refill.claim_id)
        except HRUnavailableError:
            return
        office = self._office(refill.office_id)
        if status is ClaimStatus.APPROVED and refill.state is RefillState.CLAIM_SUBMITTED:
            refill.state = RefillState.APPROVED
            refill.log(now, "HR approved")
            self.chat.send_direct(refill.assignee_id, f"HR approved your claim {refill.claim_id}.")
        elif status is ClaimStatus.PAID:
            refill.state = RefillState.REIMBURSED
            refill.reimbursed_at = now
            refill.log(now, "reimbursed")
            self.chat.send_direct(refill.assignee_id, f"Claim {refill.claim_id} has been paid out.")
        elif status is ClaimStatus.REJECTED:
            refill.state = RefillState.REJECTED
            self.ledger.reverse_spend(office.id, refill.spend_month, refill.id)
            refill.log(now, "HR rejected")
            text = f"HR rejected claim {refill.claim_id} ({refill.id})."
            self.chat.send_direct(refill.assignee_id, text)
            self.chat.send_direct(office.coordinator_id, text)
        elif (
            status is ClaimStatus.PENDING
            and not refill.hr_nudge_sent
            and now - refill.claim_submitted_at >= self.settings.hr_nudge_after
        ):
            refill.hr_nudge_sent = True
            days = (now - refill.claim_submitted_at).days
            self.chat.send_direct(
                office.coordinator_id,
                f"Claim {refill.claim_id} for {self._name(refill.assignee_id)} has been pending "
                f"for {days} days. Please follow up with HR.",
            )

    def _stats(self, now: datetime) -> dict[str, VolunteerStats]:
        window_start = now - self.settings.fairness_window
        counts: dict[str, int] = {}
        last: dict[str, datetime] = {}
        unpaid: set[str] = set()
        for r in self.refills.values():
            if r.assignee_id is None:
                continue
            if r.receipt is not None:
                bought = r.receipt.purchased_at
                if bought >= window_start:
                    counts[r.assignee_id] = counts.get(r.assignee_id, 0) + 1
                if r.assignee_id not in last or bought > last[r.assignee_id]:
                    last[r.assignee_id] = bought
            if r.state in UNPAID_STATES:
                unpaid.add(r.assignee_id)
        return {
            vid: VolunteerStats(counts.get(vid, 0), last.get(vid), vid in unpaid)
            for vid in self.volunteers
        }

    def _spend_cap(self, refill: Refill, office: Office, now: datetime) -> Money:
        if refill.budget_override:
            return office.typical_max
        remaining = self.ledger.remaining(office.id, office.local_month(now))
        return office.typical_max.min(remaining)

    def _can_take(self, volunteer: Volunteer, office_id: str) -> bool:
        return (
            self.presence.presence_of(volunteer.id).is_in(office_id)
            or volunteer.home_office == office_id
        )

    def _require_coordinator(self, refill: Refill, coordinator_id: str) -> Office:
        office = self._office(refill.office_id)
        if coordinator_id != office.coordinator_id:
            raise NotAllowedError(f"Only the {office.name} coordinator can do this")
        return office

    def _describe(self, refill: Refill) -> str:
        if refill.state is RefillState.COMMITTED:
            return f"{self._name(refill.assignee_id)} is buying"
        if refill.state is RefillState.OFFERED:
            return f"{self._name(refill.current_offer.volunteer_id)} has been asked"
        if refill.state is RefillState.BLOCKED_BUDGET:
            return "waiting for the coordinator to approve budget"
        return "waiting for a volunteer to accept"

    def _office(self, office_id: str) -> Office:
        try:
            return self.offices[office_id]
        except KeyError:
            raise NotFoundError(f"Unknown office {office_id}") from None

    def _volunteer(self, volunteer_id: str) -> Volunteer:
        try:
            return self.volunteers[volunteer_id]
        except KeyError:
            raise NotAllowedError(f"{volunteer_id} is not a registered snack volunteer") from None

    def _name(self, volunteer_id: str | None) -> str:
        v = self.volunteers.get(volunteer_id or "")
        return v.name if v else str(volunteer_id)

    @staticmethod
    def _local(at: datetime, office: Office) -> str:
        return at.astimezone(ZoneInfo(office.timezone)).strftime("%a %d %b %H:%M")
