"""The ordered checkpoints a pupil can be measured against, per subject.

A nivåtest asks "what level is this pupil actually at?", and the only honest
answer LK20 supports is one of its own checkpoints. So the ladder is not a scale
we invented -- it is the subject's kompetansemaalsett, sorted.

The shape differs per subject, and that is not an inconsistency to smooth over:

    matematikk                          9 rungs -- etter 2., 3., ... 10. trinn
    norsk/engelsk/naturfag/samfunnsfag  4 rungs -- roughly 2., 4., 7., 10.
    KRLE                                3 rungs -- etter 4., 7., 10.

A matematikk result therefore resolves to a single school year while a KRLE
result resolves to a three-year span. The UI has to say which it is rather than
implying both are "level 3 of N".

`difficulty` on a quiz item deliberately plays no part here. `pensum.items.schema`
defines it as relative to its checkpoint -- a hard 2.-trinn item is not a hard
10.-trinn item -- so it cannot order anything across rungs. The rung is the
ladder; difficulty is grain within one.

Pure: no I/O and no item loading. Callers pass in which goal sets are servable,
because "is there a quiz for this checkpoint?" is a question about the item bank,
not about the curriculum.
"""

from __future__ import annotations

from dataclasses import dataclass

from pensum.domain.models import GoalSet, Subject


@dataclass(frozen=True)
class Rung:
    """One checkpoint, with its position on its subject's ladder."""

    index: int
    goal_set: GoalSet

    @property
    def after_year(self) -> int:
        return self.goal_set.after_year

    @property
    def applies_to_years(self) -> tuple[int, ...]:
        return self.goal_set.applies_to_years

    @property
    def spans_one_year(self) -> bool:
        """Whether this rung pins down a single klasse.

        True for every matematikk rung and for nothing else. It is the
        difference between "you are at 5.-trinn level" and "you are somewhere in
        5.-7. trinn", and a result page that blurs the two overstates what the
        test found.
        """
        return len(self.applies_to_years) == 1


@dataclass(frozen=True)
class Ladder:
    """A subject's testable checkpoints, lowest first."""

    subject: str
    rungs: tuple[Rung, ...]

    @classmethod
    def build(cls, subject: Subject, servable: set[str]) -> Ladder:
        """Rungs for `subject`, keeping only checkpoints that have a quiz.

        A checkpoint with no served items is not a rung a pupil can be measured
        on, and leaving it in would put a hole in the middle of the ladder that
        the bracket search would read as "failed". Filtering is therefore part
        of building the ladder, not something the search has to defend against.
        """
        usable = sorted(
            (gs for gs in subject.goal_sets if gs.code in servable),
            key=lambda gs: gs.after_year,
        )
        return cls(
            subject=subject.code,
            rungs=tuple(Rung(index=i, goal_set=gs) for i, gs in enumerate(usable)),
        )

    def __len__(self) -> int:
        return len(self.rungs)

    def __bool__(self) -> bool:
        return bool(self.rungs)

    def __getitem__(self, index: int) -> Rung:
        return self.rungs[index]

    @property
    def top(self) -> int:
        """Index of the highest rung. Meaningless on an empty ladder."""
        return len(self.rungs) - 1

    def holds(self, index: int) -> bool:
        return 0 <= index < len(self.rungs)

    def rung(self, index: int) -> Rung | None:
        return self.rungs[index] if self.holds(index) else None

    def by_goal_set(self, code: str) -> Rung | None:
        return next((r for r in self.rungs if r.goal_set.code == code), None)

    def index_for_grade(self, grade: int) -> int | None:
        """The rung governing `grade`, by Udir's own benyttes-paa-aarstrinn.

        Returns None where the subject genuinely covers no checkpoint for that
        year. That is not hypothetical: naturfag's 2.-trinn set covers years 1-2
        and its next set covers 4-5, so a 3rd-grader falls in the gap. Callers
        decide what to do about it; inventing a nearest rung here would hide a
        real hole in the data.
        """
        return next((r.index for r in self.rungs if grade in r.applies_to_years), None)

    def start_index(self, grade: int | None) -> int:
        """Where a nivåtest should begin its climb.

        One rung *below* the pupil's own, floored at the bottom of the ladder.
        Starting at their own checkpoint asks a 5th-grader to prove 5th-grade
        work cold, and a wrong first answer then reads as failure; starting one
        below means the first block is material they have already been taught,
        and the ceiling gets bracketed from underneath rather than guessed at
        from above. It costs roughly one extra block.

        An unknown grade starts at the bottom, which is the assumption-free
        answer and the only defensible one when we were told nothing.
        """
        if not self.rungs:
            return 0
        if grade is None:
            return 0
        own = self.index_for_grade(grade)
        if own is None:
            # In a gap (see index_for_grade). The rung below the first one that
            # is above them is the closest thing to "one below their own".
            above = [r.index for r in self.rungs if r.after_year >= grade]
            own = min(above) if above else self.top
        return max(0, own - 1)
