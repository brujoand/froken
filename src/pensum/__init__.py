"""Pensum -- the Norwegian grunnskole curriculum, and quizzes to test it."""

import os

# Injected at image build time from the tag semantic-release minted, so the
# version the app reports and the tag it was published under cannot drift apart.
# A running image that says "dev" is telling you its build was wrong, which is
# more use than a confident lie.
__version__ = os.environ.get("APP_VERSION", "dev")
