"""
Keeping DPSK passphrases out of anything the browser receives.

WHY

The workflow status endpoint returns each global phase's result verbatim:

    phase_data["result"] = job.global_phase_results.get(defn.id)

and validate_and_plan's result is the parsed roster -- every resident, with
their passphrase. Measured on a real job: 153 plaintext passphrases in the
response, returned again on every poll, for the whole duration of an import
that the monitor polls continuously.

Nothing in the UI reads the field. That is not a defence: a value the page
ignores is still in the response body, in browser memory, in devtools, in
any proxy or log along the way, and in whatever a support screenshot
captures. The fix is not to send it.

WHAT THE UI ACTUALLY NEEDS

Whether one exists. "checked and confirmed", never the value. So a redacted
payload keeps the shape and swaps the secret for a boolean, rather than
dropping the key and making downstream code guess whether the passphrase is
absent or merely unset.

The backend still reads passphrases freely -- to join identities to pools,
to confirm one exists, to match an orphaned identity back to a resident.
This is a boundary, not a ban.
"""

from typing import Any

# Keys whose value is a DPSK passphrase. Deliberately exact: passphrase_id,
# passphrase_format and passphrase_count are not secrets and must survive.
SECRET_KEYS = {"passphrase", "passphrase_value", "pass_phrase"}

# What replaces the key, so a caller can still tell "set" from "not set".
PRESENCE_SUFFIX = "_set"


def redact_passphrases(value: Any) -> Any:
    """
    A copy of `value` with every passphrase replaced by a presence boolean.

    Walks dicts and lists to any depth, because phase results nest: a job
    holds phases, a phase holds a roster, a roster row holds the secret.
    Leaves every other key untouched, and never mutates the input -- the
    caller is usually holding live job state that must not be altered.
    """
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in SECRET_KEYS:
                out[f"{key}{PRESENCE_SUFFIX}"] = bool(item)
                continue
            out[key] = redact_passphrases(item)
        return out
    if isinstance(value, list):
        return [redact_passphrases(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_passphrases(item) for item in value)
    return value
