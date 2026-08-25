"""Parsing Udir payloads.

Every case here is a shape observed in the live API, not a hypothetical. The
awkward ones -- the `referanse` wrapper, the `dato`/`overskrift` split, the
free-text goal-set titles -- are exactly what a naive parser gets wrong, and each
failure is silent rather than loud.
"""

from __future__ import annotations

from datetime import date

from pensum.domain.models import BOKMAAL, ENGLISH, NYNORSK
from pensum.ingest.normalise import goal_from, goal_set_from, localised, subject_from


def test_localised_reads_every_maalform() -> None:
    text = localised(
        {
            "tekst": [
                {"spraak": "default", "verdi": "standard"},
                {"spraak": "nob", "verdi": "bokmål"},
                {"spraak": "nno", "verdi": "nynorsk"},
                {"spraak": "eng", "verdi": "english"},
            ]
        }
    )
    assert text.get(BOKMAAL) == "bokmål"
    assert text.get(NYNORSK) == "nynorsk"
    assert text.get(ENGLISH) == "english"


def test_localised_accepts_a_bare_string() -> None:
    """The goal-set endpoint returns plain strings where the goal endpoint nests."""
    assert localised("Kompetansemål og vurdering 2. trinn").get(BOKMAAL) == (
        "Kompetansemål og vurdering 2. trinn"
    )


def test_missing_bokmaal_falls_back_to_nynorsk_before_english() -> None:
    """A Norwegian pupil is better served by nynorsk than by English."""
    text = localised(
        {"tekst": [{"spraak": "nno", "verdi": "nynorsk"}, {"spraak": "eng", "verdi": "english"}]}
    )
    assert text.get(BOKMAAL) == "nynorsk"
    assert text.has(BOKMAAL) is False


def test_goal_unwraps_the_referanse_nesting() -> None:
    """Related entities arrive as [{"referanse": {...}}], not [{...}]."""
    goal = goal_from(
        {
            "kode": "KM13228",
            "tittel": {"tekst": [{"spraak": "nob", "verdi": "gruppere tall"}]},
            "tilknyttede-kjerneelementer": [{"referanse": {"kode": "KE14"}}],
            "tilknyttede-tverrfaglige-temaer": [],
        }
    )
    assert goal.code == "KM13228"
    assert goal.core_elements == ("KE14",)
    assert goal.source_url.endswith("/kompetansemaal-lk20/KM13228")


def test_goal_set_uses_aarstrinn_codes_not_titles() -> None:
    """Titles are dirty free text; the codes are clean.

    Observed upstream: "7.trinn" without a space, "10. trinn " with a trailing
    space, and both "vg1" and "Vg1". Parsing them would be a standing bug.
    """
    goal_set = goal_set_from(
        {
            "kode": "KV1021",
            "tittel": "7.trinn ",
            "etter-aarstrinn": [{"kode": "aarstrinn2"}],
            "benyttes-paa-aarstrinn": [{"kode": "aarstrinn1"}, {"kode": "aarstrinn2"}],
        },
        [],
    )
    assert goal_set is not None
    assert goal_set.after_year == 2
    assert goal_set.applies_to_years == (1, 2)


def test_non_grunnskole_goal_sets_are_rejected() -> None:
    """NAT01-05 carries a dozen yrkesfag sets we must never ingest."""
    assert goal_set_from({"kode": "KV77", "etter-aarstrinn": [{"kode": "vg1"}]}, []) is None
    assert goal_set_from({"kode": "KV68", "etter-aarstrinn": []}, []) is None


def test_goal_set_without_benyttes_paa_falls_back_to_its_checkpoint() -> None:
    goal_set = goal_set_from({"kode": "KV1", "etter-aarstrinn": [{"kode": "aarstrinn4"}]}, [])
    assert goal_set.applies_to_years == (4,)


def test_subject_reads_validity_from_dato_not_overskrift() -> None:
    """`overskrift` is a localised label template, not a value.

    Reading `verdi` off it yields "Gjelder fra <>" and silently drops every
    validity window -- which is how a superseded curriculum stays live.
    """
    subject = subject_from(
        {
            "kode": "MAT01-06",
            "tittel": {"tekst": [{"spraak": "nob", "verdi": "Matematikk"}]},
            "gyldighetsperiode": {
                "gyldig-fra": {
                    "overskrift": [{"spraak": "nob", "verdi": "Gjelder fra <>"}],
                    "dato": "2026-08-01T00:00:00",
                },
                "gyldig-til": None,
            },
            "erstatter": [{"kode": "MAT01-05"}],
            "sist-endret": "2026-01-01T00:00:00",
        },
        [],
    )
    assert subject.valid_from == date(2026, 8, 1)
    assert subject.valid_to is None
    assert subject.replaces == ("MAT01-05",)
    assert subject.base_code == "MAT01"


def test_subject_in_force_respects_both_bounds() -> None:
    subject = subject_from(
        {
            "kode": "MAT01-05",
            "gyldighetsperiode": {
                "gyldig-fra": {"dato": "2020-08-01T00:00:00"},
                "gyldig-til": {"dato": "2026-07-31T00:00:00"},
            },
        },
        [],
    )
    assert subject.is_in_force(date(2026, 7, 19)) is True
    assert subject.is_in_force(date(2026, 8, 1)) is False
    assert subject.is_in_force(date(2019, 1, 1)) is False


def test_goal_sets_are_ordered_by_checkpoint() -> None:
    """Upstream lists them unordered -- MAT01-06 arrives 2, 3, 4, 5, ... shuffled."""

    def raw(code: str, year: int) -> dict:
        return {"kode": code, "etter-aarstrinn": [{"kode": f"aarstrinn{year}"}]}

    sets = [
        goal_set_from(raw("C", 7), []),
        goal_set_from(raw("A", 2), []),
        goal_set_from(raw("B", 4), []),
    ]
    subject = subject_from({"kode": "X01-01"}, sets)
    assert [gs.after_year for gs in subject.goal_sets] == [2, 4, 7]
