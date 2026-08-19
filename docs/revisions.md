# Revising a Plan

What the revision box will change on a plan that already exists, and what it
hands back untouched instead of guessing. The short version is in the
[README](../README.md).

Once a design has been planned, the revision box takes one instruction and
either changes the number it names or says why it did not. Nothing in between:
a revision that goes through is recompiled and rebuilt from the earliest step
it touched, so the plan on screen and the record on disk always agree.

What it can change is a parameter some step in the plan carries.
`change the wall thickness from 3 mm to 5 mm` sets 5, not the 3 the sentence
starts with, and the response says which of the two numbers it read.

What it declines, and says so instead of half-applying:

- topology. `add a lid` is not a number in a step, and no patch can express it.
- a number it had to guess at. Two numbers with nothing tying them together
  (`make the wall thickness 5 mm and the height 120 mm`), a stated change with
  a second request bolted on, or an amount rather than a destination
  (`increase the height by 5 mm`) all stop short of a rebuild and ask for
  confirmation.
- a parameter this plan does not have. A mug has no width, so
  `make the width 120 mm` comes back as a clarification request with the plan
  untouched, rather than writing a key nothing reads.

Every decline carries the engine's own reasoning in the response warnings,
which is what the web app renders, so the answer on screen says which number
was read and which was not.
