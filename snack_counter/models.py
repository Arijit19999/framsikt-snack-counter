"""Domain model: offices, volunteers, refills and the refill states."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from zoneinfo import ZoneInfo

from .money import Money


@dataclass(frozen=True)
class Office:
    id: str
    name: str
    currency: str
    timezone: str
    monthly_budget: Money
    typical_min: Money
    typical_max: Money
    coordinator_id: str  # office manager who handles escalations
    channel: str  # chat channel of the office's volunteers

    def __post_init__(self) -> None:
        for m in (self.monthly_budget, self.typical_min, self.typical_max):
            if m.currency != self.currency:
                raise ValueError(f"{self.name}: all amounts must be in {self.currency}")

    def local_month(self, at: datetime) -> str:
        """Budget month ('YYYY-MM') in the office's own time zone."""
        if at.tzinfo is None:
            raise ValueError("Timestamps must be timezone-aware")
        return at.astimezone(ZoneInfo(self.timezone)).strftime("%Y-%m")


@dataclass(frozen=True)
class Volunteer:
    id: str
    name: str
    employee_id: str  # the ID the HR system knows
    home_office: str
    payroll_currency: str


class PresenceStatus(Enum):
    IN_OFFICE = "in_office"
    REMOTE = "remote"
    ON_LEAVE = "on_leave"


@dataclass(frozen=True)
class Presence:
    status: PresenceStatus
    office_id: str | None = None  # set when status is IN_OFFICE

    def is_in(self, office_id: str) -> bool:
        return self.status is PresenceStatus.IN_OFFICE and self.office_id == office_id


class RefillState(Enum):
    OFFERED = "offered"                  # asked one specific volunteer, timer running
    OPEN_TO_ALL = "open_to_all"          # direct offers failed; first taker wins
    BLOCKED_BUDGET = "blocked_budget"    # not enough budget left; coordinator decides
    COMMITTED = "committed"              # one volunteer has it; budget reserved
    NEEDS_REVIEW = "needs_review"        # receipt above the agreed cap
    PURCHASED = "purchased"              # receipt accepted, claim not yet at HR
    CLAIM_SUBMITTED = "claim_submitted"  # waiting for HR
    APPROVED = "approved"                # HR approved, waiting for payroll
    REIMBURSED = "reimbursed"            # money paid out: done
    REJECTED = "rejected"                # coordinator or HR rejected


# While a refill is in one of these states the box is still empty and
# nobody else may start a second refill for the same office.
BOX_EMPTY_STATES = frozenset(
    {
        RefillState.OFFERED,
        RefillState.OPEN_TO_ALL,
        RefillState.BLOCKED_BUDGET,
        RefillState.COMMITTED,
    }
)

# The volunteer has paid but has not been reimbursed yet.
UNPAID_STATES = frozenset(
    {
        RefillState.NEEDS_REVIEW,
        RefillState.PURCHASED,
        RefillState.CLAIM_SUBMITTED,
        RefillState.APPROVED,
    }
)


class OfferOutcome(Enum):
    ACCEPTED = "accepted"
    DECLINED = "declined"
    EXPIRED = "expired"
    TAKEN_BY_OTHER = "taken_by_other"


@dataclass
class Offer:
    volunteer_id: str
    offered_at: datetime
    expires_at: datetime
    outcome: OfferOutcome | None = None


@dataclass(frozen=True)
class Receipt:
    amount: Money
    image: bytes
    items: tuple[str, ...]
    purchased_at: datetime


@dataclass
class Refill:
    id: str
    office_id: str
    reported_by: str
    reported_at: datetime
    state: RefillState
    offers: list[Offer] = field(default_factory=list)
    excluded: set[str] = field(default_factory=set)  # declined / timed out / dropped
    assignee_id: str | None = None
    committed_at: datetime | None = None
    buy_deadline: datetime | None = None
    reminder_sent: bool = False
    spend_cap: Money | None = None
    reservation_month: str | None = None
    budget_override: bool = False
    receipt: Receipt | None = None
    spend_month: str | None = None
    claim_id: str | None = None
    claim_submitted_at: datetime | None = None
    hr_nudge_sent: bool = False
    reimbursed_at: datetime | None = None
    history: list[tuple[datetime, str]] = field(default_factory=list)

    @property
    def current_offer(self) -> Offer | None:
        if self.offers and self.offers[-1].outcome is None:
            return self.offers[-1]
        return None

    def log(self, at: datetime, message: str) -> None:
        self.history.append((at, message))
