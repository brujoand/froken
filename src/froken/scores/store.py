"""Where finished attempts are kept.

SQLite, from the standard library. The whole dataset is one row per quiz a child
finishes -- a family's worth of that is measured in kilobytes per year, and a
single file that can be copied, inspected with `sqlite3` and deleted by hand is
the right shape for data about children.

What is stored is deliberately the summary, not the transcript: which checkpoint,
how many right, and the per-goal tally the result page already shows. Not which
answer was given to which question. The tally is what an adult can act on; the
transcript is a record of a seven-year-old's mistakes, and Frøken has no use for
it that justifies keeping it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    key         TEXT PRIMARY KEY,
    user_sub    TEXT NOT NULL,
    user_name   TEXT NOT NULL,
    subject     TEXT NOT NULL,
    goal_set    TEXT NOT NULL,
    grade       INTEGER NOT NULL,
    correct     INTEGER NOT NULL,
    total       INTEGER NOT NULL,
    by_goal     TEXT NOT NULL,
    finished_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS attempts_by_user ON attempts (user_sub, finished_at DESC);
"""


def attempt_key(session_id: str) -> str:
    """A stable, non-reversible id for one attempt.

    Hashed rather than stored raw: a quiz session id is a live bearer token for
    two hours, and a table of them would be a table of credentials. The hash
    still makes recording idempotent, which is the only property needed -- the
    result page can be reloaded, and reloading it must not double-count.
    """
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GoalTally:
    """How one competence goal went in one attempt."""

    goal: str
    correct: int
    total: int

    @property
    def all_correct(self) -> bool:
        return self.correct == self.total


@dataclass(frozen=True)
class Attempt:
    """One finished quiz, by one signed-in pupil."""

    key: str
    user_sub: str
    user_name: str
    subject: str
    goal_set: str
    grade: int
    correct: int
    total: int
    by_goal: tuple[GoalTally, ...]
    finished_at: datetime

    @property
    def percentage(self) -> int:
        return round(100 * self.correct / self.total) if self.total else 0


@dataclass(frozen=True)
class UserSummary:
    """One pupil's row on the roster."""

    sub: str
    name: str
    attempts: int
    correct: int
    total: int
    latest_at: datetime

    @property
    def percentage(self) -> int:
        """Averaged over questions, not over quizzes.

        A pupil who answered two of two on one quiz and ten of twenty on another
        did not score 75%. Weighting by question is the only average that
        survives quizzes of different lengths.
        """
        return round(100 * self.correct / self.total) if self.total else 0


class AttemptStore:
    """Reads and writes attempts. One instance per app.

    A connection per operation rather than one held open: SQLite connections are
    cheap, the write rate is a handful per hour, and FastAPI will run these from
    more than one thread.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        try:
            # A reader must never block on the writer -- an admin refreshing the
            # roster while a child finishes a quiz is the expected collision.
            connection.execute("PRAGMA journal_mode=WAL")
            with connection:
                yield connection
        finally:
            connection.close()

    def record(self, attempt: Attempt) -> None:
        """Write an attempt, or do nothing if it is already there.

        Idempotent by key, because the result page is reloadable and a refresh
        must not turn one quiz into two.
        """
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO attempts
                    (key, user_sub, user_name, subject, goal_set, grade,
                     correct, total, by_goal, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.key,
                    attempt.user_sub,
                    attempt.user_name,
                    attempt.subject,
                    attempt.goal_set,
                    attempt.grade,
                    attempt.correct,
                    attempt.total,
                    json.dumps(
                        [
                            {"goal": t.goal, "correct": t.correct, "total": t.total}
                            for t in attempt.by_goal
                        ]
                    ),
                    attempt.finished_at.astimezone(UTC).isoformat(),
                ),
            )

    def users(self) -> list[UserSummary]:
        """Everyone who has finished at least one quiz, by name.

        The display name comes from their most recent attempt: people rename
        themselves in the provider, and the roster should show what they are
        called now rather than what they were called the first time.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    a.user_sub AS sub,
                    (SELECT user_name FROM attempts
                      WHERE user_sub = a.user_sub
                      ORDER BY finished_at DESC LIMIT 1) AS name,
                    COUNT(*)       AS attempts,
                    SUM(a.correct) AS correct,
                    SUM(a.total)   AS total,
                    MAX(a.finished_at) AS latest_at
                FROM attempts a
                GROUP BY a.user_sub
                ORDER BY name COLLATE NOCASE
                """
            ).fetchall()

        return [
            UserSummary(
                sub=row["sub"],
                name=row["name"],
                attempts=row["attempts"],
                correct=row["correct"] or 0,
                total=row["total"] or 0,
                latest_at=datetime.fromisoformat(row["latest_at"]),
            )
            for row in rows
        ]

    def attempts_for(self, user_sub: str) -> list[Attempt]:
        """One pupil's history, most recent first."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM attempts WHERE user_sub = ? ORDER BY finished_at DESC",
                (user_sub,),
            ).fetchall()
        return [_attempt_from_row(row) for row in rows]


def _attempt_from_row(row: sqlite3.Row) -> Attempt:
    tallies = json.loads(row["by_goal"])
    return Attempt(
        key=row["key"],
        user_sub=row["user_sub"],
        user_name=row["user_name"],
        subject=row["subject"],
        goal_set=row["goal_set"],
        grade=row["grade"],
        correct=row["correct"],
        total=row["total"],
        by_goal=tuple(
            GoalTally(goal=t["goal"], correct=t["correct"], total=t["total"]) for t in tallies
        ),
        finished_at=datetime.fromisoformat(row["finished_at"]),
    )
