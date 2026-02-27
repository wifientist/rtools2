"""
Feature-level access restrictions by company domain.

Super users always bypass these checks. For everyone else,
the user's company domain must appear in the relevant set.

Uses domains (not IDs) so the same config works across dev/prod.
Update the sets here — no other files need changing.
"""
