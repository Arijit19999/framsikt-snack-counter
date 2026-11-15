"""Who is where today.

Assumption: the chat tool already shows a status (in office / remote / on
leave), and visitors set their location with "/snack here oslo". The core
only needs one question answered: where is this person today?
"""
from __future__ import annotations

from typing import Iterable, Protocol

from .models import Presence, PresenceStatus, Volunteer


class PresenceDirectory(Protocol):
    def presence_of(self, volunteer_id: str) -> Presence: ...


class FakePresence:
    """Everyone starts in their home office; tests change that."""

    def __init__(self, volunteers: Iterable[Volunteer]) -> None:
        self._state = {
            v.id: Presence(PresenceStatus.IN_OFFICE, v.home_office) for v in volunteers
        }

    def presence_of(self, volunteer_id: str) -> Presence:
        return self._state.get(volunteer_id, Presence(PresenceStatus.REMOTE))

    def set_in_office(self, volunteer_id: str, office_id: str) -> None:
        self._state[volunteer_id] = Presence(PresenceStatus.IN_OFFICE, office_id)

    def set_remote(self, volunteer_id: str) -> None:
        self._state[volunteer_id] = Presence(PresenceStatus.REMOTE)

    def set_on_leave(self, volunteer_id: str) -> None:
        self._state[volunteer_id] = Presence(PresenceStatus.ON_LEAVE)
