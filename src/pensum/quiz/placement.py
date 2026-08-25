"""Finding a pupil's ceiling by bisecting a subject's ladder.

A trinntest asks a fixed question -- "can you pass 6. trinn i matte?" -- and
answers it against one checkpoint. A nivåtest asks the open one: what level is
this pupil actually at? It asks a block of questions at one rung, then halves
whatever span is still unknown, until it holds a checkpoint the pupil clears and
the next one they do not. Those two rungs are the answer.

**Why not item response theory.** IRT is the standard tool here and it is the
wrong one for us: it needs item parameters fitted to real response data, and we
have none. Our `difficulty` field is one author's guess made at authoring time.
Fitting a latent-ability model to guesses yields a number with a decimal point
and a confidence interval that means nothing at all, which is worse than a coarse
answer honest about being coarse. Bisection resolves to one Udir checkpoint,
which is exactly the resolution the curriculum itself defines, and it can be
explained to a parent in one sentence: it asks in the middle of whatever is
still unknown.

**Why bisection rather than a staircase.** A one-rung-at-a-time walk is the
obvious design and it does not fit the budget. Matematikk has nine rungs, and a
pupil who starts more than four away from their real level runs out of questions
just as it is about to be found -- measured at 38% of simulated pupils placed
inconclusively. Halving the remaining span brackets every ladder we have for
every pupil who clears anything at all, in at most four blocks.

**Why it terminates.** Each block is asked strictly inside the open span between
the highest rung cleared and the lowest rung failed, and afterwards becomes one
of those two bounds. The span therefore shrinks every step, and the walk stops
when nothing is left between them. The item budget is a second, independent
backstop.

Pure state machine. It decides *which rung to ask next and how many items*; it
does not pick items, hold a session, or know about the web layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pensum.domain.ladder import Ladder, Rung

# One block of questions at one rung. Five is a compromise: enough that a single
# careless slip does not move the verdict, few enough that a full run stays
# under the attention span of the seven-year-old at the bottom of the ladder.
PROBE_SIZE = 5

# 4 of 5 clears a rung, 2 of 5 or fewer fails it, 3 of 5 is partway. Deliberately
# asymmetric around the middle: "mastered" should be a stronger claim than "not
# mastered", because the first is what the result page asserts about a child.
MASTERY = 0.8
FLOOR = 0.4

# Total items a run may spend, including the deepening block. Beyond roughly
# this the pupil is answering worse from fatigue than from ability, which
# corrupts the very thing being measured.
MAX_ITEMS = 25

# Extra items at the frontier once bracketing is done. The ceiling is already
# known by then; this buys per-goal resolution, which is the part anyone can
# act on.
DEEPEN_SIZE = 5


class Verdict(StrEnum):
    """How one block at one rung went."""

    MASTERED = "mastered"
    FRONTIER = "frontier"
    BELOW = "below"


def verdict(correct: int, total: int) -> Verdict:
    """Grade one block.

    An empty block is BELOW rather than an error: a pupil who abandons a rung
    has not demonstrated it, and the alternative is a crash on a path a bored
    child can reach.
    """
    if total <= 0:
        return Verdict.BELOW
    share = correct / total
    if share >= MASTERY:
        return Verdict.MASTERED
    if share <= FLOOR:
        return Verdict.BELOW
    return Verdict.FRONTIER


@dataclass(frozen=True)
class Probe:
    """The record of one block, at one rung."""

    rung: int
    correct: int
    total: int

    @property
    def verdict(self) -> Verdict:
        return verdict(self.correct, self.total)


@dataclass(frozen=True)
class Step:
    """What to ask next: a rung, and how many items from it."""

    rung: Rung
    count: int
    deepening: bool = False


@dataclass(frozen=True)
class Outcome:
    """Where the run landed.

    `ceiling` and `frontier` are both optional and the two None cases mean
    opposite things, so neither can be collapsed into an index:

      * `ceiling is None` -- the pupil cleared no rung we can test. That is not
        "level zero"; it is "this test found nothing to stand on", and the page
        must say the second thing.
      * `frontier is None` -- they cleared the top rung and the ladder ran out.
        The test has no more to offer, which is a limit of the test rather than
        a statement about the pupil.
    """

    ladder: Ladder
    probes: tuple[Probe, ...]
    ceiling: Rung | None
    frontier: Rung | None

    @property
    def items_spent(self) -> int:
        return sum(p.total for p in self.probes)

    @property
    def topped_out(self) -> bool:
        """They cleared the highest rung the subject has.

        A real result, and the one case where a missing frontier is not a
        shortfall in the run. The page should say the ladder ended, not imply
        there is more above that went untested.
        """
        return self.ceiling is not None and self.ceiling.index == self.ladder.top

    @property
    def bracketed(self) -> bool:
        """A rung cleared, and the next one not."""
        return self.ceiling is not None and self.frontier is not None

    @property
    def conclusive(self) -> bool:
        """Whether the run may claim to have found a level at all.

        False in exactly two situations, and both have to be said out loud
        rather than rounded to a number: the pupil cleared no rung we can test,
        and the item budget ran out mid-climb. A run that stopped early still
        carries useful per-goal information; it just has not found a ceiling.
        """
        return self.bracketed or self.topped_out


@dataclass(frozen=True)
class Placement:
    """A nivåtest in progress: a ladder plus the blocks answered so far."""

    ladder: Ladder
    start: int
    probes: tuple[Probe, ...] = ()

    @classmethod
    def begin(cls, ladder: Ladder, grade: int | None) -> Placement:
        return cls(ladder=ladder, start=ladder.start_index(grade))

    def record(self, rung: int, correct: int, total: int) -> Placement:
        return Placement(
            ladder=self.ladder,
            start=self.start,
            probes=(*self.probes, Probe(rung=rung, correct=correct, total=total)),
        )

    # --- the walk ---------------------------------------------------------

    @property
    def spent(self) -> int:
        return sum(p.total for p in self.probes)

    @property
    def visited(self) -> frozenset[int]:
        return frozenset(p.rung for p in self.probes)

    def _verdict_at(self, index: int) -> Verdict | None:
        """The verdict for a rung, pooling every block asked at it.

        Pooling matters once deepening has happened: the frontier rung has two
        blocks, and reading only the first would let the deepening block change
        nothing while still costing the pupil five questions.
        """
        blocks = [p for p in self.probes if p.rung == index]
        if not blocks:
            return None
        return verdict(sum(p.correct for p in blocks), sum(p.total for p in blocks))

    @property
    def low(self) -> int:
        """Highest rung known mastered, or -1 if none is.

        -1 is a real state, not a sentinel for "unknown": it says the pupil has
        not yet stood on anything, and `outcome` reports that as no ceiling
        rather than as the bottom rung.
        """
        mastered = [i for i in self.visited if self._verdict_at(i) is Verdict.MASTERED]
        return max(mastered, default=-1)

    @property
    def high(self) -> int:
        """Lowest rung known *not* mastered, or one past the top if none is.

        FRONTIER and BELOW both land here. The difference between "you have half
        of this one" and "this one is out of reach" is worth showing on the
        result page, but it is not a difference the search needs: neither is a
        rung the pupil has cleared, so both bound the bracket from above.
        """
        unmastered = [i for i in self.visited if self._verdict_at(i) is not Verdict.MASTERED]
        return min(unmastered, default=len(self.ladder))

    @property
    def search_closed(self) -> bool:
        """Nothing left between the highest cleared rung and the lowest failed one."""
        return self.low + 1 >= self.high

    def next_step(self) -> Step | None:
        """The next block to ask, or None when the run is over."""
        if not self.ladder:
            return None
        if self.spent >= MAX_ITEMS:
            return None

        if not self.probes:
            return self._step(self.start)

        if self.search_closed:
            return self._deepen(self.high)

        # Bisect what is left. A staircase cannot cross matematikk's nine rungs
        # inside the item budget -- a pupil starting four rungs from their real
        # level runs out of questions just as it is about to be found -- whereas
        # halving the remaining span brackets any ladder we have in at most four
        # blocks. The jump is also explicable in one sentence: it asks in the
        # middle of whatever is still unknown.
        target = self.low + 1 + (self.high - self.low - 1) // 2
        return self._step(target)

    def _step(self, index: int) -> Step | None:
        rung = self.ladder.rung(index)
        if rung is None:
            return None
        return Step(rung=rung, count=min(PROBE_SIZE, MAX_ITEMS - self.spent))

    def _deepen(self, index: int) -> Step | None:
        """Spend what is left on the frontier, for per-goal resolution.

        Only ever one extra block, and only if the budget covers a full one -- a
        two-item deepening tells nobody anything and just makes the test longer.
        Skipped entirely when the ladder topped out, since there is then no
        frontier rung to resolve.
        """
        rung = self.ladder.rung(index)
        if rung is None:
            return None
        if sum(1 for p in self.probes if p.rung == index) > 1:
            return None  # already deepened here
        if MAX_ITEMS - self.spent < DEEPEN_SIZE:
            return None
        return Step(rung=rung, count=DEEPEN_SIZE, deepening=True)

    # --- the answer -------------------------------------------------------

    def outcome(self) -> Outcome:
        return Outcome(
            ladder=self.ladder,
            probes=self.probes,
            ceiling=self.ladder.rung(self.low),
            frontier=self.ladder.rung(self.high),
        )
