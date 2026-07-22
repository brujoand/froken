"""Surface what a human reviewer should look at first.

Not a substitute for reading the questions -- nothing is. This finds the classes
of defect that are mechanically detectable, so a reviewer's attention goes to
the ones that are not: whether the question is fair, age-appropriate, and
actually tests the goal rather than the reading.

    uv run python tools/review_items.py                # everything unreviewed
    uv run python tools/review_items.py MAT01-06       # one subject
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from froken.catalogue.loader import Catalogue  # noqa: E402
from froken.items.loader import ItemBank  # noqa: E402
from froken.items.schema import ItemSet, QuizItem  # noqa: E402

# A prompt longer than this is likely testing reading rather than the skill,
# especially in the early years.
LONG_PROMPT = {2: 90, 4: 120, 7: 180, 10: 240}


def smells(item: QuizItem, after_year: int) -> list[str]:
    """Mechanical checks. Each is a hint to look, not a verdict."""
    found: list[str] = []

    limit = next(v for k, v in sorted(LONG_PROMPT.items()) if after_year <= k or k == 10)
    if len(item.prompt.nb) > limit:
        found.append(f"prompt is {len(item.prompt.nb)} chars for year {after_year} (>{limit})")

    if item.type == "multiple_choice":
        # Case-sensitive on purpose: spelling and capitalisation items
        # deliberately offer options that differ only in case ("i have" vs
        # "I have"), and casefolding here would flag the exact thing they test.
        texts = [c.text.nb.strip() for c in item.choices]
        if len(set(texts)) != len(texts):
            found.append("duplicate choice text")
        # An answer noticeably longer than its distractors is a giveaway.
        correct = next(c for c in item.choices if c.correct)
        others = [c for c in item.choices if not c.correct]
        if others and len(correct.text.nb) > 2 * max(len(c.text.nb) for c in others):
            found.append("correct choice is much longer than the distractors")

    if item.type == "short_text":
        for locale, accepted in item.accept.items():
            if len(accepted) < 2:
                found.append(
                    f"only one accepted {locale} answer; spelling variants will be marked wrong"
                )

    if not item.explanation.nb.strip():
        found.append("empty explanation")
    if item.explanation.nb.strip() == item.prompt.nb.strip():
        found.append("explanation restates the prompt")

    return found


def report(item_set: ItemSet, catalogue: Catalogue) -> None:
    subject = catalogue.subject(item_set.subject)
    goal_set = subject.goal_set(item_set.goal_set)
    goals = {g.code: g for g in goal_set.goals}

    unreviewed = [i for i in item_set.items if not i.reviewed]
    print(f"\n{item_set.subject} / {item_set.goal_set}  (etter {goal_set.after_year}. trinn)")
    print(f"  {len(item_set.items)} items, {len(unreviewed)} awaiting review")
    print(
        f"  {len(item_set.goals_covered)}/{len(goals)} goals tested, "
        f"{len(item_set.goals_excused)} marked not assessable"
    )

    by_type: dict[str, int] = {}
    for item in item_set.items:
        by_type[item.type] = by_type.get(item.type, 0) + 1
    print(f"  types: {', '.join(f'{k}={v}' for k, v in sorted(by_type.items()))}")

    for item in item_set.items:
        if problems := smells(item, goal_set.after_year):
            print(f"    {item.id}: {'; '.join(problems)}")

    for excused in item_set.not_assessable:
        goal = goals.get(excused.goal)
        text = goal.text.get("nob")[:70] if goal else "?"
        print(f"    excused {excused.goal}: {text}...")


def main() -> int:
    wanted = sys.argv[1] if len(sys.argv) > 1 else None
    catalogue = Catalogue.load()
    bank = ItemBank.load(include_unreviewed=True)

    sets = [s for s in bank.item_sets if not wanted or s.subject == wanted]
    if not sets:
        print(f"no item sets found{f' for {wanted}' if wanted else ''}")
        return 1

    for item_set in sorted(sets, key=lambda s: (s.subject, s.goal_set)):
        report(item_set, catalogue)

    total = sum(len(s.items) for s in sets)
    unreviewed = sum(1 for s in sets for i in s.items if not i.reviewed)
    print(f"\n{total} items across {len(sets)} goal sets; {unreviewed} awaiting review.")
    print("Nothing unreviewed is served until a human sets reviewed: true.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
