"""Reading fluency: read a text aloud, and see how it went.

Separate from the quiz for a reason. A quiz item has one right answer and is
graded exactly; a reading is graded by aligning what was heard against what was
printed, and every number that comes out of it is approximate. Keeping the two
apart stops the quiz's exactness from lending false authority to a WPM figure.

Nothing here writes audio anywhere. A recording arrives in a request body, is
decoded in memory, and is gone when the response is sent.
"""
