"""
Credential gate for the migration audit report.

Rule: no passphrase or shared secret ever reaches the audit page or anything
exported from it. The audit reads objects that carry them -- SZ WLAN detail
(encryption.passphrase), R1 network detail (wlan.passphrase), SZ auth services
-- and the field-comparison accordion walks those objects generically, so the
rule cannot rest on every caller remembering it.

HOW

1. collect_secrets() walks the audit's INPUTS and gathers every string stored
   under a credential-named key.
2. scrub_report() walks the OUTPUT and withholds any string leaf that sits
   under a credential-named key, equals a collected secret, or contains one.
3. assert_clean() re-walks the scrubbed output and raises if anything got
   through. It should never fire; it exists so a bug in step 2 fails the
   request instead of shipping a passphrase.

A withheld leaf is replaced, not dropped, so the page shows that something was
there. Counts, booleans, and keys like `passphrase_count` are untouched: only
string values are credential material.

Substring matching is limited to secrets of MIN_SUBSTRING_LEN or more, so a
short value (say a 4-character shared secret) can't knock out unrelated text;
shorter secrets are still caught on an exact match.
"""

import json
import re
from typing import Any, Iterable, Set, Tuple

WITHHELD = "[withheld]"
# Prefix of a keyed passphrase fingerprint (passphrase_check); mirrored here to
# keep this module free of imports.
FP_PREFIX = "hmac-sha256:"
# Stands in for a credential in cached captures (audit_cache); not itself a secret.
CREDENTIAL_PRESENT = "__credential_present__"
MIN_SUBSTRING_LEN = 8

# Key names whose string values are credentials. Anchored at the end so that
# pskFormat, passphraseFormat, passphrase_count, placeholder_secret and the
# like are not caught -- none of those hold a credential string.
_SECRET_KEY = re.compile(
    r"(passphrase|sharedsecret|shared_secret|secret|password|wephexkey|wepkey)$",
    re.IGNORECASE,
)


class CredentialLeakError(RuntimeError):
    """The report still contained credential material after scrubbing."""


def _is_placeholder(value: str) -> bool:
    """Cache stand-ins for credentials (marker or keyed fingerprint) -- not secrets themselves."""
    return value == CREDENTIAL_PRESENT or value.startswith(FP_PREFIX)


def is_secret_key(key: Any) -> bool:
    return isinstance(key, str) and bool(_SECRET_KEY.search(key))


def collect_secrets(*sources: Any) -> Set[str]:
    """Every non-empty string stored under a credential-named key, anywhere in sources."""
    found: Set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if is_secret_key(k) and isinstance(v, str) and v and not _is_placeholder(v):
                    found.add(v)
                else:
                    walk(v)
        elif isinstance(node, (list, tuple, set)):
            for v in node:
                walk(v)

    for s in sources:
        walk(s)
    return found


def _leaks(value: str, secrets: Iterable[str]) -> bool:
    for s in secrets:
        if value == s or (len(s) >= MIN_SUBSTRING_LEN and s in value):
            return True
    return False


def scrub_report(report: Any, secrets: Set[str]) -> Tuple[Any, int]:
    """Return (scrubbed copy, number of leaves withheld)."""
    withheld = 0

    def walk(node: Any, under_secret_key: bool = False) -> Any:
        nonlocal withheld
        if isinstance(node, dict):
            return {k: walk(v, is_secret_key(k)) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, under_secret_key) for v in node]
        if isinstance(node, str) and node and (under_secret_key or _leaks(node, secrets)):
            withheld += 1
            return WITHHELD
        return node

    return walk(report), withheld


def assert_clean(report: Any, secrets: Set[str]) -> None:
    """Raise CredentialLeakError if any credential survived. Never echoes it."""
    def walk(node: Any, under_secret_key: bool = False) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, is_secret_key(k))
        elif isinstance(node, list):
            for v in node:
                walk(v, under_secret_key)
        elif isinstance(node, str) and node != WITHHELD:
            if under_secret_key and node:
                raise CredentialLeakError("credential-named field survived scrubbing")
            if _leaks(node, secrets):
                raise CredentialLeakError("credential value survived scrubbing")

    walk(report)
    # Belt and braces: keys are strings too, and json.dumps sees everything.
    blob = json.dumps(report, default=str)
    if any(len(s) >= MIN_SUBSTRING_LEN and s in blob for s in secrets):
        raise CredentialLeakError("credential value present in serialized report")
