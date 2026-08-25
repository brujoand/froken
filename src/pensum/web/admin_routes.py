"""The admin view of recorded scores.

Two pages: who has taken quizzes, and how one of them has done. Both are behind
`require_admin`, and both exist only when score history is configured -- there is
no page here that renders an empty table as though the feature were merely
unused.

The pages are read-only by design. An admin can see a score; there is nothing
here to edit, re-grade or delete a child's record with, because none of those
would be an improvement on `sqlite3 pensum.db`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from pensum.catalogue.loader import Catalogue
from pensum.scores.store import Attempt, AttemptStore
from pensum.web.deps import get_store, require_admin
from pensum.web.rendering import context, templates, validate_locale

router = APIRouter(include_in_schema=False)


def _store_or_404(request: Request) -> AttemptStore:
    store = get_store(request)
    if store is None:
        raise HTTPException(status_code=404, detail="score history is not configured")
    return store


def _describe(catalogue: Catalogue, attempt: Attempt, lang: str) -> dict[str, object]:
    """One attempt, with the curriculum resolved for display.

    A stored attempt holds codes, not text. Resolving them here rather than at
    write time is what keeps a re-ingest from stranding history: if a goal code
    is gone, the row still renders -- with the code -- instead of the page
    failing.
    """
    subject = catalogue.subject(attempt.subject)
    goal_set = subject.goal_set(attempt.goal_set) if subject else None
    goals = {goal.code: goal for goal in goal_set.goals} if goal_set else {}

    return {
        "attempt": attempt,
        "subject": subject,
        "subject_title": subject.display_title.get(lang) if subject else attempt.subject,
        "goals": [
            {
                "tally": tally,
                "text": goals[tally.goal].text.get(lang) if tally.goal in goals else tally.goal,
                "source_url": goals[tally.goal].source_url if tally.goal in goals else None,
            }
            for tally in attempt.by_goal
        ],
    }


@router.get("/{locale}/admin", response_class=HTMLResponse)
async def roster(request: Request, locale: str) -> HTMLResponse:
    validate_locale(locale)
    require_admin(request)
    store = _store_or_404(request)

    return templates.TemplateResponse(
        request,
        "pages/admin_roster.html",
        context(request, locale, users=store.users()),
    )


@router.get("/{locale}/admin/elev/{user_sub}", response_class=HTMLResponse)
async def pupil(request: Request, locale: str, user_sub: str) -> HTMLResponse:
    validate_locale(locale)
    require_admin(request)
    store = _store_or_404(request)

    attempts = store.attempts_for(user_sub)
    if not attempts:
        # No rows means either no such user or nothing recorded for them. Both
        # are "nothing to show here", and telling the two apart would be a user
        # enumeration oracle for no benefit.
        raise HTTPException(status_code=404, detail="no attempts recorded for this user")

    lang = context(request, locale)["lang"]
    return templates.TemplateResponse(
        request,
        "pages/admin_pupil.html",
        context(
            request,
            locale,
            # The most recent attempt carries the name they go by now.
            pupil_name=attempts[0].user_name,
            pupil_sub=user_sub,
            entries=[_describe(request.app.state.catalogue, a, str(lang)) for a in attempts],
        ),
    )
