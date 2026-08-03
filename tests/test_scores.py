"""The attempt store.

The properties worth pinning down are the ones a bug would make quiet: recording
the same attempt twice, averaging quizzes of different lengths wrongly, and
showing a pupil the name they used to have.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from froken.scores.store import Attempt, AttemptStore, GoalTally, attempt_key

NOW = datetime(2026, 7, 19, 10, 0, tzinfo=UTC)


def attempt(
    session_id: str = "s-1",
    sub: str = "u-1",
    name: str = "Ola",
    correct: int = 7,
    total: int = 10,
    when: datetime = NOW,
) -> Attempt:
    return Attempt(
        key=attempt_key(session_id),
        user_sub=sub,
        user_name=name,
        subject="MAT01-06",
        goal_set="KV123",
        grade=2,
        correct=correct,
        total=total,
        by_goal=(GoalTally(goal="KM1", correct=correct, total=total),),
        finished_at=when,
    )


def store(tmp_path: Path) -> AttemptStore:
    return AttemptStore(tmp_path / "nested" / "froken.db")


def test_the_database_is_created_along_with_its_directory(tmp_path: Path) -> None:
    """A fresh volume mount is an empty directory, and that must just work."""
    assert store(tmp_path).path.exists()


def test_an_attempt_round_trips(tmp_path: Path) -> None:
    db = store(tmp_path)
    db.record(attempt())

    [stored] = db.attempts_for("u-1")
    assert stored.correct == 7
    assert stored.percentage == 70
    assert stored.by_goal == (GoalTally(goal="KM1", correct=7, total=10),)
    assert stored.finished_at == NOW


def test_recording_the_same_attempt_twice_does_not_double_count(tmp_path: Path) -> None:
    """The result page is reloadable, and a refresh is not a second quiz."""
    db = store(tmp_path)
    db.record(attempt())
    db.record(attempt())

    assert len(db.attempts_for("u-1")) == 1


def test_the_stored_key_is_not_the_session_id(tmp_path: Path) -> None:
    """A quiz session id is a live bearer token; a table of them is a liability."""
    db = store(tmp_path)
    db.record(attempt(session_id="secret-session-token"))

    [stored] = db.attempts_for("u-1")
    assert "secret-session-token" not in stored.key
    assert stored.key == attempt_key("secret-session-token")


def test_history_is_most_recent_first(tmp_path: Path) -> None:
    db = store(tmp_path)
    db.record(attempt(session_id="s-1", when=NOW))
    db.record(attempt(session_id="s-2", when=NOW.replace(day=20)))

    assert [a.finished_at.day for a in db.attempts_for("u-1")] == [20, 19]


def test_the_roster_averages_over_questions_not_over_quizzes(tmp_path: Path) -> None:
    """Two of two and ten of twenty is 55%, not 75%.

    Averaging the percentages would let a two-question quiz outweigh a twenty-
    question one, which is the kind of wrong that looks plausible on a page.
    """
    db = store(tmp_path)
    db.record(attempt(session_id="s-1", correct=2, total=2))
    db.record(attempt(session_id="s-2", correct=10, total=20))

    [row] = db.users()
    assert row.attempts == 2
    assert row.percentage == 55


def test_the_roster_shows_the_name_from_the_most_recent_attempt(tmp_path: Path) -> None:
    """People rename themselves in the provider; the roster should keep up."""
    db = store(tmp_path)
    db.record(attempt(session_id="s-1", name="Ola", when=NOW))
    db.record(attempt(session_id="s-2", name="Ola Nordmann", when=NOW.replace(day=20)))

    [row] = db.users()
    assert row.name == "Ola Nordmann"
    assert row.latest_at.day == 20


def test_the_roster_lists_every_pupil_by_name(tmp_path: Path) -> None:
    db = store(tmp_path)
    db.record(attempt(session_id="s-1", sub="u-1", name="Ola"))
    db.record(attempt(session_id="s-2", sub="u-2", name="Kari"))

    assert [row.name for row in db.users()] == ["Kari", "Ola"]


def test_an_empty_store_has_no_users(tmp_path: Path) -> None:
    assert store(tmp_path).users() == []


def test_attempts_survive_reopening_the_database(tmp_path: Path) -> None:
    """The entire point of a file: a restart must not take the history with it."""
    path = tmp_path / "froken.db"
    AttemptStore(path).record(attempt())

    assert len(AttemptStore(path).attempts_for("u-1")) == 1
