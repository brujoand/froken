"""Optional sign-in against an OIDC provider (pocket-id).

Optional is the operative word. Nothing here runs unless an issuer, client id
and client secret are configured, and even then a pupil can take every quiz
without ever signing in. Signing in buys exactly one thing: the attempt is
attributed, so it can be recorded and shown to an adult who is allowed to see it.
"""

from froken.auth.models import User

__all__ = ["User"]
