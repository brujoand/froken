"""A nivåtest in flight: the bracket search, plus the questions it has drawn.

`pensum.quiz.placement` decides *which rung to ask next and how many items*.
This is what turns that into a sitting: it draws a block, hands the pupil one
question at a time, scores the block when it is done, feeds the result back to
the search, and draws whatever comes next. The pupil sees an ordinary quiz; the
rung is moving underneath them.

Two properties are worth stating outright, because both are easy to lose:

  * **A question is never asked twice in one run.** Every id drawn is remembered
    and excluded from later draws. The frontier rung gets deepened, so without
    this the second block there would re-serve what the first already asked --
    and the banks are shallow enough that it would happen constantly (see the
    per-rung depth issue).
  * **The run does not know how long it is.** That is inherent to an adaptive
    test and not a defect to paper over: showing a progress bar out of a total
    we have not decided yet would be a lie. `asked` is honest; there is no
    `total`.

Item drawing is injected rather than imported. The web layer passes a callable
backed by the `ItemBank`; tests pass a fake. This module therefore has no I/O
and no idea where questions come from.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from pensum.auth.models import User
from pensum.domain.ladder import Ladder
from pensum.items.schema import QuizItem
from pensum.quiz.placement import Outcome, Placement, Step
from pensum.quiz.session import SESSION_TTL

# Given a goal set, how many items are wanted, and which ids are already spent,
# return what to ask. Returning fewer than asked for is allowed and expected --
# the banks are thin -- and returning none ends the run.
Draw = Callable[[str, int, set[str]], list[QuizItem]]


@dataclass(frozen=True)
class Block:
    """One block of questions, at one rung."""

    rung: int
    goal_set: str
    items: tuple[QuizItem, ...]
    deepening: bool = False


@dataclass
class PlacementRun:
    """One pupil's nivåtest in one subject."""

    id: str
    subject: str
    ladder: Ladder
    grade: int | None
    created_at: datetime
    placement: Placement
    blocks: list[Block] = field(default_factory=list)
    answers: dict[str, str] = field(default_factory=dict)

    # As for a trinntest: captured at the start if they were signed in, so
    # signing in halfway cannot retroactively attribute a run somebody else
    # began.
    user_sub: str | None = None
    user_name: str | None = None

    # --- lifecycle --------------------------------------------------------

    @classmethod
    def begin(
        cls,
        subject: str,
        ladder: Ladder,
        grade: int | None,
        draw: Draw,
        now: datetime,
        user: User | None = None,
    ) -> PlacementRun:
        run = cls(
            # Opaque and unguessable, exactly as for a trinntest session.
            id=secrets.token_urlsafe(16),
            subject=subject,
            ladder=ladder,
            grade=grade,
            created_at=now,
            placement=Placement.begin(ladder, grade),
            user_sub=user.sub if user else None,
            user_name=user.name if user else None,
        )
        run._extend(draw)
        return run

    @property
    def attributed(self) -> bool:
        return self.user_sub is not None

    def expired(self, now: datetime) -> bool:
        return now - self.created_at > SESSION_TTL

    # --- the sitting ------------------------------------------------------

    @property
    def served(self) -> set[str]:
        return {item.id for block in self.blocks for item in block.items}

    @property
    def asked(self) -> int:
        """Questions answered so far. Deliberately not "of N" -- see the module docstring."""
        return len(self.answers)

    @property
    def block(self) -> Block | None:
        return self.blocks[-1] if self.blocks else None

    @property
    def finished(self) -> bool:
        """True when the search has nothing left to ask and nothing left unanswered."""
        return self.current() is None

    def current(self) -> QuizItem | None:
        """The question to put in front of the pupil, or None when the run is over."""
        block = self.block
        if block is None:
            return None
        return next((item for item in block.items if item.id not in self.answers), None)

    def answer(self, item_id: str, response: str, draw: Draw) -> QuizItem | None:
        """Record a response, advancing the search when a block completes.

        Re-answering is refused rather than overwritten, matching the trinntest:
        the score should reflect the first attempt, not the one taken after the
        explanation was read.
        """
        block = self.block
        if block is None or item_id in self.answers:
            return None
        item = next((i for i in block.items if i.id == item_id), None)
        if item is None:
            return None

        self.answers[item_id] = response

        if all(i.id in self.answers for i in block.items):
            self._score_block(block)
            self._extend(draw)
        return item

    # --- feeding the search ------------------------------------------------

    def _score_block(self, block: Block) -> None:
        correct = sum(item.is_correct(self.answers.get(item.id, "")) for item in block.items)
        self.placement = self.placement.record(block.rung, correct, len(block.items))

    def _extend(self, draw: Draw) -> None:
        """Draw the block the search asks for next, if there is one.

        A step the bank cannot fill is treated as the end of the run rather than
        as a failed rung. Scoring an empty block would record a rung the pupil
        never saw as one they could not do, which would then be reported to them
        as a ceiling.
        """
        step: Step | None = self.placement.next_step()
        if step is None:
            return
        items = draw(step.rung.goal_set.code, step.count, self.served)
        if not items:
            return
        self.blocks.append(
            Block(
                rung=step.rung.index,
                goal_set=step.rung.goal_set.code,
                items=tuple(items),
                deepening=step.deepening,
            )
        )

    # --- the answer -------------------------------------------------------

    def outcome(self) -> Outcome:
        return self.placement.outcome()

    def tally(self) -> dict[str, tuple[int, int]]:
        """Correct and asked, per competence goal, across the whole run.

        This is the part anyone can act on. A ceiling says where the pupil is;
        this says which goals cost them the questions -- and it spans rungs, so
        a goal they missed at the frontier and a goal they missed on the way up
        both show.
        """
        counts: dict[str, tuple[int, int]] = {}
        for block in self.blocks:
            for item in block.items:
                response = self.answers.get(item.id)
                if response is None:
                    continue
                correct, total = counts.get(item.goal, (0, 0))
                counts[item.goal] = (correct + item.is_correct(response), total + 1)
        return counts

    def gaps(self) -> tuple[str, ...]:
        """Goals the pupil did not get every question right on."""
        return tuple(goal for goal, (correct, total) in sorted(self.tally().items()) if correct < total)
