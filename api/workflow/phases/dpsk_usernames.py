"""
Relating a Cloudpath username to what the import leaves in RuckusONE.

A Cloudpath DPSK username carries the resident's speed tier as a trailing
segment. The import takes it apart and spreads the pieces across four
different objects:

    file        4021_ultrafast
    identity    4021              (renamed by create_access_policies)
    policy      4021              (sanitize_policy_name of the account)
    RADIUS      ultrafast         (the tier names the attribute group)

Every one of those relationships runs through split_account_suffix. That is
the point of this module: the rule has to be ONE rule.

WHY IT LIVES IN ITS OWN FILE

It was briefly a function in create_access_policies, which validate then
imported -- and that is a cycle, because create_access_policies imports the
cloudpath package that validate lives in. A leaf module with no workflow
imports of its own is importable from either side. (ap_fields.py exists for
the same reason and the same kind of bug.)

WHY ONE RULE MATTERS

validate decides whether a resident already exists; create_access_policies
decides what to rename them to. While those two split differently -- validate
matching the raw file name, the rename writing the stripped one -- a second
import could not recognise its own previous output. It minted a fresh
identity from the file name and then tried to rename it onto the original,
which R1 refused as a duplicate (GENERAL-010), leaving the resident with two
identities and the tier suffix still attached.
"""

from typing import Set, Tuple

# Assumed tier for a username that carries none.
DEFAULT_SUFFIX = "gigabit"


def split_account_suffix(
    username: str, default_suffix: str = DEFAULT_SUFFIX
) -> Tuple[str, str]:
    """
    "4021_ultrafast" -> ("4021", "ultrafast");  "4021" -> ("4021", default).

    Splits on the LAST underscore, with no allowlist, because that is what
    the import has always done and the audit has to describe the import as it
    is rather than as it should be. Callers that act on the result -- renaming
    an identity, say -- should check the suffix against known_suffixes()
    first: this function will happily read "DPSK_User_a7f3k9" as the account
    "DPSK_User" with tier "a7f3k9", which is true to the rule and wrong about
    the world.
    """
    if "_" in username:
        account, suffix = username.rsplit("_", 1)
        return account, suffix
    return username, default_suffix


def known_suffixes(
    usernames, default_suffix: str = DEFAULT_SUFFIX
) -> Set[str]:
    """
    The speed tiers this roster actually uses.

    Derived from the file rather than assumed, so a caller can tell
    "4021_ultrafast" (account + tier) from "DPSK_User_a7f3k9" (an R1
    auto-generated name that merely contains an underscore). Renaming the
    latter strips it to "DPSK_User" -- as does every other one of them, so
    they collide and R1 deduplicates into "DPSK_User_1", "DPSK_User_2", ...
    """
    suffixes = {default_suffix}
    for name in usernames:
        account, suffix = split_account_suffix(name, default_suffix)
        if account != name:
            suffixes.add(suffix)
    return suffixes
