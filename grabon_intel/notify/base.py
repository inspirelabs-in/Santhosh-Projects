"""Notifier interface + factory."""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


class NotifierError(RuntimeError):
    pass


@dataclass(slots=True)
class Notification:
    title: str
    summary: str
    facts: list[tuple[str, str]] = field(default_factory=list)  # [(label, value), ...]
    actions: list[tuple[str, str]] = field(default_factory=list)  # [(label, url), ...]
    severity: str = "info"  # info | warn | error
    extra: dict[str, Any] = field(default_factory=dict)


class Notifier(abc.ABC):
    name: str = "abstract"

    @property
    @abc.abstractmethod
    def available(self) -> bool: ...

    @abc.abstractmethod
    async def send(self, n: Notification) -> bool: ...


def get_notifiers() -> list[Notifier]:
    """All available notifiers, in order of priority (Teams first)."""
    from .email import EmailNotifier
    from .teams import TeamsNotifier

    out: list[Notifier] = [TeamsNotifier(), EmailNotifier()]
    return [n for n in out if n.available]
