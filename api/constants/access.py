"""
Feature-level access restrictions by company domain.

Super users always bypass these checks. For everyone else,
the user's company domain must appear in the relevant set.

Uses domains (not IDs) so the same config works across dev/prod.
Update the sets here — no other files need changing.
"""

# Migration Tracker — shared site status board for the last few migrations.
# Viewers also need alpha_enabled; only super users can edit.
TRACKER_DOMAINS = {"comcast.com"}


def has_tracker_access(user) -> bool:
    if user.role.value == "super":
        return True
    return bool(user.company and user.company.domain in TRACKER_DOMAINS)
