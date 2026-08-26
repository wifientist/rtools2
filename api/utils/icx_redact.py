"""
Redaction for RUCKUS ICX / FastIron configurations.

WiredWiz pulls running configs off switches. Those configs carry enable
passwords, local user hashes, SNMP communities, RADIUS/TACACS keys, routing
protocol auth keys and PEM blobs. Nothing here should ever reach a log file,
a database row, an export, or a terminal.

Design: fail closed.
  1. Specific rules rewrite known secret-bearing commands so the surrounding
     structure survives (you still see *that* a RADIUS key is set, and on which
     host) -- that structure is what the analysis actually needs.
  2. A catch-all then nukes the tail of any *other* line mentioning a secret
     keyword, so a command we have never seen still gets censored.
  3. Multi-line PEM/certificate blocks are dropped wholesale.

Round-tripping is explicitly NOT supported: redacted output cannot be restored
to a switch. That is intentional.
"""

import re
from typing import Dict, List, Tuple

MASK = "<REDACTED>"

# (compiled pattern, replacement) -- applied in order, first match wins per line.
# Each pattern keeps the identifying prefix as group 1 and masks the rest.
_RULES: List[Tuple[re.Pattern, str]] = [
    # `enable password-display` is a boolean toggle, not a secret. It must be
    # matched before the generic `enable ... password` rule swallows it.
    (re.compile(r"^\s*enable\s+password-display\b.*$", re.I), r"\g<0>"),

    # enable super-user-password 8 $e$..., enable telnet password ...
    (re.compile(r"^(\s*enable\s+(?:super-user-|read-only-|port-config-)?password\b).*$", re.I), r"\1 " + MASK),
    (re.compile(r"^(\s*enable\s+telnet\s+password\b).*$", re.I), r"\1 " + MASK),

    # username <name> password 8 $..., username <name> create-password ...
    (re.compile(r"^(\s*username\s+\S+(?:\s+privilege\s+\d+)?\s+(?:password|create-password|nopassword)\b).*$", re.I),
     r"\1 " + MASK),

    # snmp-server community <string> ro|rw ...  /  snmp-server community 2 $...
    (re.compile(r"^(\s*snmp-server\s+community\b)\s+\S+(.*)$", re.I), r"\1 " + MASK + r"\2"),
    # snmp-server host 1.2.3.4 version 2c <community>
    (re.compile(r"^(\s*snmp-server\s+host\s+\S+(?:\s+version\s+\S+)?)\s+\S+(.*)$", re.I), r"\1 " + MASK + r"\2"),
    # snmp-server user <n> <group> v3 auth md5 <pw> priv des <pw>
    (re.compile(r"^(\s*snmp-server\s+user\s+\S+\s+\S+\s+v3\b).*$", re.I), r"\1 " + MASK),
    (re.compile(r"^(\s*snmp-server\s+(?:group|engineid|view)\b.*?\b(?:auth|priv)\b).*$", re.I), r"\1 " + MASK),

    # radius-server host 1.2.3.4 auth-port 1812 acct-port 1813 default key 2 $...
    # tacacs-server host 1.2.3.4 ... key ...
    (re.compile(r"^(.*\b(?:radius|tacacs)-server\b.*?\bkey\b).*$", re.I), r"\1 " + MASK),
    (re.compile(r"^(\s*(?:radius|tacacs)-server\s+key\b).*$", re.I), r"\1 " + MASK),

    # BGP / OSPF / RIP / ISIS neighbour + interface authentication
    (re.compile(r"^(\s*neighbor\s+\S+\s+password\b).*$", re.I), r"\1 " + MASK),
    (re.compile(r"^(\s*ip\s+ospf\s+(?:authentication-key|md5-authentication\s+key-id\s+\d+\s+key)\b).*$", re.I),
     r"\1 " + MASK),
    (re.compile(r"^(\s*ip\s+rip\s+authentication\b).*$", re.I), r"\1 " + MASK),
    (re.compile(r"^(\s*(?:key-string|authentication-key|auth-key)\b).*$", re.I), r"\1 " + MASK),
    (re.compile(r"^(\s*isis\s+(?:password|auth\s+key)\b).*$", re.I), r"\1 " + MASK),

    # VRRP / VSRP simple-text auth
    (re.compile(r"^(\s*(?:ip\s+)?(?:vrrp|vsrp)[\w\s-]*?\bauth(?:-type)?\b).*$", re.I), r"\1 " + MASK),

    # NTP authentication keys
    (re.compile(r"^(\s*ntp\s+authentication-key\s+\d+\b).*$", re.I), r"\1 " + MASK),

    # 802.1X / MAC-auth / web-auth shared secrets and pre-shared keys
    (re.compile(r"^(\s*(?:dot1x|mac-authentication|web-authentication)\b.*?\b(?:key|secret|password|passphrase)\b).*$", re.I),
     r"\1 " + MASK),

    # Cloud / management onboarding tokens
    (re.compile(r"^(\s*manager\s+(?:registrar|active-list)\b.*?\b(?:key|token|secret)\b).*$", re.I), r"\1 " + MASK),

    # crypto / ssh / cert material referenced inline
    (re.compile(r"^(\s*crypto\s+key\b).*$", re.I), r"\1 " + MASK),
    (re.compile(r"^(\s*ip\s+ssh\s+pub-key-file\b).*$", re.I), r"\1 " + MASK),
]

# Any line mentioning one of these, that no specific rule caught, is truncated
# at the keyword. This is the fail-closed net for commands not enumerated above.
_CATCHALL = re.compile(
    r"^(.*?\b(?:password|passwd|pre-shared-key|passphrase|secret|community|"
    r"private-key|shared-key|auth-key|authentication-key|key-string|"
    r"credential|token)\b)\s*\S.*$",
    re.I,
)

# Lines that mention a keyword but carry no secret -- do not mangle these.
_CATCHALL_EXEMPT = re.compile(
    r"\b(?:no\s+(?:password|snmp-server\s+community)|"
    r"password-thresh|password-retries|password-min-length|password-display|"
    r"aaa\s+authentication|username\s+\S+\s+access-time|"
    r"key\s+chain|key\s+\d+$)\b",
    re.I,
)

# Multi-line blobs dropped entirely.
_BLOCK_START = re.compile(r"-----BEGIN [A-Z ]+-----|^\s*certificate\s+\S+\s*$", re.I)
_BLOCK_END = re.compile(r"-----END [A-Z ]+-----|^\s*quit\s*$", re.I)


def redact_icx_config(config: str) -> Tuple[str, Dict[str, int]]:
    """
    Redact secrets from an ICX/FastIron configuration.

    Returns (redacted_config, stats) where stats counts what was hit:
      {"rule": n, "catchall": n, "block_lines": n, "total_lines": n}

    The stats are the audit trail -- a `catchall` count above zero means the
    config contained a secret-bearing command we have no specific rule for, and
    is worth adding one.
    """
    if not config:
        return "", {"rule": 0, "catchall": 0, "block_lines": 0, "total_lines": 0}

    out: List[str] = []
    stats = {"rule": 0, "catchall": 0, "block_lines": 0, "total_lines": 0}
    in_block = False

    for line in config.splitlines():
        stats["total_lines"] += 1

        if in_block:
            stats["block_lines"] += 1
            if _BLOCK_END.search(line):
                in_block = False
                out.append(f"! {MASK} (end of encoded block)")
            continue

        if _BLOCK_START.search(line):
            in_block = True
            stats["block_lines"] += 1
            out.append(f"! {MASK} (encoded block removed)")
            continue

        for pattern, replacement in _RULES:
            new_line, n = pattern.subn(replacement, line)
            if n:
                stats["rule"] += 1
                out.append(new_line)
                break
        else:
            if _CATCHALL.match(line) and not _CATCHALL_EXEMPT.search(line):
                stats["catchall"] += 1
                out.append(_CATCHALL.sub(r"\1 " + MASK, line))
            else:
                out.append(line)

    return "\n".join(out), stats


def assert_clean(redacted: str) -> List[str]:
    """
    Paranoia check for tests and for the ingest path: return any line in an
    already-redacted config that still looks like it carries a live secret.
    An empty list means the redactor believes the text is safe to persist.
    """
    suspicious: List[str] = []
    for i, line in enumerate(redacted.splitlines(), 1):
        if MASK in line:
            continue
        if _CATCHALL.match(line) and not _CATCHALL_EXEMPT.search(line):
            suspicious.append(f"{i}: {line.strip()}")
        # FastIron encodes hashed secrets as `<type-digit> $<base64ish>`
        elif re.search(r"\s[27]\s+\$\S{8,}", line):
            suspicious.append(f"{i}: {line.strip()}")
    return suspicious
