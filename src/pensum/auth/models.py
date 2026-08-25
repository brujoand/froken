"""Who is signed in.

Deliberately the smallest thing that works: a stable id, something to call them,
and the groups the provider asserted. No email, no picture, no profile -- Pensum
has no use for them, and the safest field is the one never read.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    """A signed-in pupil or adult, as the provider described them."""

    # The provider's stable subject identifier. Everything recorded is keyed on
    # this rather than on a name, because names are edited and reused.
    sub: str
    name: str
    groups: tuple[str, ...] = ()

    def in_group(self, group: str) -> bool:
        return group in self.groups

    def as_claims(self) -> dict[str, object]:
        """The shape stored in the login cookie."""
        return {"sub": self.sub, "name": self.name, "groups": list(self.groups)}

    @classmethod
    def from_cookie(cls, payload: object) -> User | None:
        """Rebuild from a cookie payload, or None if it is not one.

        The cookie is signed, so a malformed payload means our own format
        changed rather than that someone forged one. Either way the answer is
        "not signed in" -- never a traceback on a page a child is looking at.
        """
        if not isinstance(payload, dict):
            return None
        sub = payload.get("sub")
        name = payload.get("name")
        if not isinstance(sub, str) or not sub or not isinstance(name, str):
            return None
        raw_groups = payload.get("groups")
        groups = (
            tuple(g for g in raw_groups if isinstance(g, str))
            if isinstance(raw_groups, list)
            else ()
        )
        return cls(sub=sub, name=name, groups=groups)
