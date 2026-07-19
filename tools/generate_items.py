"""Draft quiz items with an LLM, for a human to review.

Maintainer tooling. Not part of the shipped app, not importable from it, and not
installed into the container -- the running site must never need an API key.

Two properties matter more than throughput:

  * Everything is written `reviewed: false`. The released build withholds
    unreviewed items, so generating is proposing, never publishing.
  * A goal may come back with no items. Competence goals framed as *utforske*
    or *samtale om* cannot be tested in writing, and the prompt says so
    explicitly. Refusals are recorded as `not_assessable`, which is a real
    outcome rather than a failed generation.

Usage:
    export ANTHROPIC_API_KEY=...
    uv run --group generate python tools/generate_items.py MAT01-06 --goal-set KV1021
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from froken.catalogue.loader import Catalogue  # noqa: E402
from froken.domain.models import BOKMAAL  # noqa: E402
from froken.items.schema import ItemSet, NotAssessable, QuizItem  # noqa: E402

PROMPT_PATH = Path(__file__).parent / "prompts" / "item_generation.md"
ITEMS_DIR = REPO_ROOT / "data" / "items"

MODEL = "claude-opus-4-8"
ITEMS_PER_GOAL = 3

# Norwegian pupils start school the year they turn six, so year N is roughly
# age N+5. Used only to pitch the reading level.
AGE_OFFSET = 5

RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "not_assessable_reason": {
            "type": ["string", "null"],
            "description": (
                "If this goal cannot honestly be tested in writing, explain why "
                "in one or two sentences and return no items."
            ),
        },
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"enum": ["multiple_choice", "numeric", "short_text"]},
                    "difficulty": {"type": "integer", "minimum": 1, "maximum": 3},
                    "prompt": {
                        "type": "object",
                        "properties": {"nb": {"type": "string"}, "en": {"type": "string"}},
                        "required": ["nb", "en"],
                    },
                    "explanation": {
                        "type": "object",
                        "properties": {"nb": {"type": "string"}, "en": {"type": "string"}},
                        "required": ["nb", "en"],
                    },
                    "choices": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "text": {
                                    "type": "object",
                                    "properties": {
                                        "nb": {"type": "string"},
                                        "en": {"type": "string"},
                                    },
                                    "required": ["nb", "en"],
                                },
                                "correct": {"type": "boolean"},
                            },
                            "required": ["id", "text", "correct"],
                        },
                    },
                    "answer": {"type": ["number", "null"]},
                    "tolerance": {"type": "number"},
                    "accept": {
                        "type": "object",
                        "properties": {
                            "nb": {"type": "array", "items": {"type": "string"}},
                            "en": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "required": ["type", "difficulty", "prompt", "explanation"],
            },
        },
    },
    "required": ["items"],
}


def build_prompt(subject_title: str, year: int, goal_code: str, goal_text: str) -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").format(
        subject=subject_title,
        year=year,
        age=year + AGE_OFFSET,
        goal_code=goal_code,
        goal_text=goal_text,
        count=ITEMS_PER_GOAL,
    )


def generate_for_goal(client: Any, prompt: str) -> dict[str, Any]:
    """One goal, one call. Structured output, so a malformed draft is impossible."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        tools=[
            {
                "name": "submit_items",
                "description": "Submit the drafted quiz items, or none with a reason.",
                "input_schema": RESULT_SCHEMA,
            }
        ],
        tool_choice={"type": "tool", "name": "submit_items"},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return block.input
    return {"items": []}


def to_items(goal_code: str, drafted: list[dict[str, Any]]) -> tuple[list[QuizItem], list[str]]:
    """Validate drafts against the real schema.

    Anything that fails is dropped rather than patched: a question we had to
    repair is a question nobody has read properly, and it would arrive wearing
    the same `reviewed: false` flag as a sound one.
    """
    items: list[QuizItem] = []
    rejected: list[str] = []

    for index, draft in enumerate(drafted, start=1):
        payload = {**draft, "id": f"{goal_code}-{index:02d}", "goal": goal_code, "reviewed": False}
        payload.setdefault("tolerance", 0.0)
        # The model returns lists; the schema wants tuples, and empty rather
        # than absent for the fields this item type does not use.
        payload["choices"] = tuple(payload.get("choices") or ())
        payload["accept"] = {k: tuple(v) for k, v in (payload.get("accept") or {}).items()}
        try:
            items.append(QuizItem.model_validate(payload))
        except Exception as exc:  # noqa: BLE001 -- report and move on
            rejected.append(f"{goal_code}-{index:02d}: {exc}")

    return items, rejected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subject", help="curriculum code, e.g. MAT01-06")
    parser.add_argument("--goal-set", required=True, help="goal set code, e.g. KV1021")
    parser.add_argument("--goal", action="append", help="limit to specific goal codes")
    parser.add_argument("--dry-run", action="store_true", help="print instead of writing")
    args = parser.parse_args(argv)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        return 2

    try:
        from anthropic import Anthropic
    except ImportError:
        print("Install the generate group: uv sync --group generate", file=sys.stderr)
        return 2

    catalogue = Catalogue.load()
    subject = catalogue.subject(args.subject)
    if subject is None:
        print(f"unknown subject {args.subject}", file=sys.stderr)
        return 1

    goal_set = subject.goal_set(args.goal_set)
    if goal_set is None:
        print(f"{args.subject} has no goal set {args.goal_set}", file=sys.stderr)
        return 1

    goals = [g for g in goal_set.goals if not args.goal or g.code in args.goal]
    client = Anthropic()

    all_items: list[QuizItem] = []
    excused: list[NotAssessable] = []
    rejected: list[str] = []

    for goal in goals:
        prompt = build_prompt(
            subject.display_title.get(BOKMAAL),
            goal_set.after_year,
            goal.code,
            goal.text.get(BOKMAAL),
        )
        result = generate_for_goal(client, prompt)

        items, bad = to_items(goal.code, result.get("items") or [])
        rejected.extend(bad)

        if not items:
            reason = result.get("not_assessable_reason") or (
                "The model returned no usable questions for this goal."
            )
            excused.append(NotAssessable(goal=goal.code, reason=reason))
            print(f"  {goal.code}: not assessable -- {reason[:70]}")
        else:
            all_items.extend(items)
            print(f"  {goal.code}: {len(items)} item(s)")

    item_set = ItemSet(
        subject=subject.code,
        goal_set=goal_set.code,
        items=tuple(all_items),
        not_assessable=tuple(excused),
    )

    if rejected:
        print(f"\n{len(rejected)} draft(s) failed validation and were dropped:", file=sys.stderr)
        for problem in rejected:
            print(f"  {problem}", file=sys.stderr)

    payload = json.loads(item_set.model_dump_json(exclude_defaults=False))
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    import yaml

    destination = ITEMS_DIR / subject.code / f"{goal_set.code}.yaml"
    if destination.exists():
        # Never clobber reviewed work. Re-running writes alongside so a human
        # can diff and merge deliberately.
        destination = destination.with_suffix(".generated.yaml")

    destination.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# Generated by tools/generate_items.py using {MODEL}.\n"
        f"# Every item is reviewed: false and will NOT be served until a human\n"
        f"# reads it and flips the flag. Questions here are ours, not Udir's.\n"
    )
    destination.write_text(
        header + yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    print(f"\nWrote {len(all_items)} unreviewed item(s) to {destination.relative_to(REPO_ROOT)}")
    print("Review them, set `reviewed: true`, then run: uv run python -m froken.items.validate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
