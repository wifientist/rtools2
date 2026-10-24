"""
Does an R1 PSK network carry the same passphrase as its SZ WLAN?

Answered on the server, as a verdict only -- match / differs / missing on
one side / unknown. No passphrase, and no fingerprint of one, is ever put
in the report.

Values are compared as keyed fingerprints (HMAC-SHA256), so the check also
works on cached captures: the capture cache stores the fingerprint instead
of the passphrase, and two captures taken days apart can still be compared.
A fingerprint can't be reversed, and without the server-held key it can't
be brute-forced offline either -- which matters, since unit PSKs are often
weak. The key is AUDIT_FINGERPRINT_KEY, else derived from FERNET_KEY; a key
id is part of every fingerprint, so after a key change old cached
fingerprints read as "unknown (refresh)" rather than as a mismatch.
"""

import hashlib
import hmac
import os
from typing import Any, Dict, Optional

FP_PREFIX = "hmac-sha256:"

PSK_AUTH_TYPES = {"WPA2-PSK", "WPA3-SAE", "WPA-PSK", "WPA3", "WPA2-PSK + Portal"}


def _key() -> bytes:
    explicit = os.getenv("AUDIT_FINGERPRINT_KEY")
    if explicit:
        return explicit.encode()
    base = os.getenv("FERNET_KEY", "")
    return hashlib.sha256(b"rtools-audit-passphrase-fingerprint:" + base.encode()).digest()


def _key_id() -> str:
    return hashlib.sha256(b"kid:" + _key()).hexdigest()[:8]


def is_fingerprint(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(FP_PREFIX)


def fingerprint(value: Optional[str]) -> Optional[str]:
    """Keyed fingerprint of a credential; fingerprints and empties pass through."""
    if not value or is_fingerprint(value):
        return value or None
    digest = hmac.new(_key(), value.encode(), hashlib.sha256).hexdigest()
    return f"{FP_PREFIX}{_key_id()}:{digest}"


def _effective(enc: Dict[str, Any]) -> Optional[str]:
    return enc.get("passphrase") or enc.get("saePassphrase") or None


def compare_passphrases(sz_encryption: Optional[Dict[str, Any]],
                        r1_wlan: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """
    {status, note}. status: match | differs | missing_in_r1 | missing_in_sz | unknown.

    Compares each side's effective PSK (passphrase, else saePassphrase -- the
    migration writes SZ's WPA3 saePassphrase into R1's passphrase), and the
    SAE passphrase separately when both sides carry one of their own.
    """
    if r1_wlan is None:
        return {"status": "unknown", "note": "R1 network detail not read this run"}
    sz_enc = sz_encryption or {}
    a, b = fingerprint(_effective(sz_enc)), fingerprint(_effective(r1_wlan))
    if not a and not b:
        return {"status": "unknown", "note": "No passphrase on either side"}
    if not a:
        return {"status": "missing_in_sz", "note": "R1 has a passphrase; SZ shows none"}
    if not b:
        return {"status": "missing_in_r1", "note": "SZ has a passphrase; R1 shows none"}
    if a.split(":")[1] != b.split(":")[1]:
        return {"status": "unknown", "note": "Captured under different fingerprint keys — refresh to compare"}
    if a != b:
        return {"status": "differs", "note": "Passphrase differs from SZ"}
    sa, sb = fingerprint(sz_enc.get("saePassphrase")), fingerprint(r1_wlan.get("saePassphrase"))
    if sz_enc.get("passphrase") and sa and sb and sa != sb:
        return {"status": "differs", "note": "WPA2 passphrase matches; the separate SAE passphrase differs"}
    return {"status": "match", "note": ""}
