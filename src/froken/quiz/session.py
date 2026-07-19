"""Quiz sessions.

Held in memory for the length of a sitting and then discarded. Frøken is used by
children, so the design goal is that there is nothing to leak: no account, no
identifier that outlives the quiz, nothing written to disk. The cost is that an
in-flight quiz does not survive a restart, which is the right trade for a
practice tool.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from froken.items.schema import QuizItem

# Long enough that a 7-year-old is not rushed, short enough that abandoned
# sessions do not accumulate.
SESSION_TTL = timedelta(hours=2)
DEFAULT_LENGTH = 10


@dataclass
class QuizSession:
    """One pupil's attempt at one checkpoint."""

    id: str
    subject: str
    goal_set: str
    grade: int
    items: list[QuizItem]
    created_at: datetime
    answers: dict[str, str] = field(default_factory=dict)

    @property
    def answered(self) -> int:
        return len(self.answers)

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def finished(self) -> bool:
        return self.answered >= self.total

    def current(self) -> QuizItem | None:
        """The first unanswered item, or None when the quiz is done."""
        return next((item for item in self.items if item.id not in self.answers), None)

    def answer(self, item_id: str, response: str) -> QuizItem | None:
        """Record a response. Returns the item answered, or None if unknown.

        Re-answering is refused rather than overwritten: the score should reflect
        the first attempt, not the one after the explanation was read.
        """
        if item_id in self.answers:
            return None
        item = next((i for i in self.items if i.id == item_id), None)
        if item is None:
            return None
        self.answers[item_id] = response
        return item

    def expired(self, now: datetime) -> bool:
        return now - self.created_at > SESSION_TTL


class SessionStore:
    """In-memory session storage, swept lazily.

    Deliberately process-local. A restart loses in-flight quizzes; nothing about
    a pupil is ever persisted, which is the property worth having.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, QuizSession] = {}

    def create(
        self, subject: str, goal_set: str, grade: int, items: list[QuizItem], now: datetime
    ) -> QuizSession:
        self._sweep(now)
        session = QuizSession(
            # Opaque and unguessable, so a session id reveals nothing and cannot
            # be enumerated.
            id=secrets.token_urlsafe(16),
            subject=subject,
            goal_set=goal_set,
            grade=grade,
            items=items,
            created_at=now,
        )
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str, now: datetime) -> QuizSession | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.expired(now):
            del self._sessions[session_id]
            return None
        return session

    def _sweep(self, now: datetime) -> None:
        for key in [k for k, s in self._sessions.items() if s.expired(now)]:
            del self._sessions[key]

    def __len__(self) -> int:
        return len(self._sessions)
