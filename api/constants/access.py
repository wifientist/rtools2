"""
Feature-level access restrictions by company domain.

Super users always bypass these checks. For everyone else,
the user's company domain must appear in the relevant set.

Uses domains (not IDs) so the same config works across dev/prod.
Update the sets here — no other files need changing.
"""

# Migration Tracker — shared site status board for the last few migrations.
# Who can view it (only super users can edit):
#   - super users
#   - anyone a super admin granted it to (users.tracker_enabled, set on the Users page)
#   - beta users whose company domain is listed here
TRACKER_DOMAINS = {"comcast.com"}


def has_tracker_access(user) -> bool:
    if user.role.value == "super":
        return True
    if getattr(user, "tracker_enabled", False):
        return True
    return bool(user.beta_enabled and user.company and user.company.domain in TRACKER_DOMAINS)
