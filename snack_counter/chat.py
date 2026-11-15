"""Chat tool boundary + a simulated stand-in.

The real implementation would call the company chat tool's bot API.
Incoming commands ("/snack empty", "accept", ...) would be parsed by a thin
bot layer that calls RefillService; that layer is not part of the core.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ChatTool(Protocol):
    def send_direct(self, volunteer_id: str, text: str) -> None: ...
    def post_to_channel(self, channel: str, text: str) -> None: ...


@dataclass(frozen=True)
class ChatMessage:
    to: str  # volunteer id, or "#channel"
    text: str


class FakeChat:
    """Records every message so tests can assert on who was told what."""

    def __init__(self) -> None:
        self.messages: list[ChatMessage] = []

    def send_direct(self, volunteer_id: str, text: str) -> None:
        self.messages.append(ChatMessage(volunteer_id, text))

    def post_to_channel(self, channel: str, text: str) -> None:
        self.messages.append(ChatMessage(f"#{channel}", text))

    def to(self, recipient: str) -> list[str]:
        return [m.text for m in self.messages if m.to == recipient]

    def channel(self, channel: str) -> list[str]:
        return self.to(f"#{channel}")
