"""
How a Cloudpath SSID name is split into unit and property.

The rule: split on the LAST '@'. Everything before it is the unit, everything
after is the property.

    "1-101@Cedar Point"  -> unit "1-101",  property "Cedar Point"
    "101@Cedar Point"    -> unit "101",    property "Cedar Point"
    "A-101@Cedar Point"  -> unit "A-101",  property "Cedar Point"
    "Cedar Point WiFi"   -> not a unit SSID (no '@') -> property-wide

This deliberately places no constraint on the unit token. It used to be
`^(\\d+)@(.+)$`, which required an all-digit unit and silently ignored every
building-scoped SSID like "1-101@Property" -- a property of 30+ units would
detect 3, and unit detection drives AP Groups, per-unit SSIDs, policy scoping
and the AP-assignment CSV all at once, so they were all wrong together.

An SSID with no '@' is how a property-wide SSID is recognised, so that is the
one shape that must NOT parse as a unit.

Defined once and imported: this pattern has consumers in validate_and_plan,
create_access_policies and the frontend, and a previous duplicated definition
(sanitize_policy_name) drifted between plan and run and caused 409s.
"""
import re
from typing import Optional

# Greedy first group, so the split lands on the LAST '@'.
UNIT_SSID_PATTERN = re.compile(r'^(.+)@([^@]+)$')


# Unit tokens that are a broken export, not a unit. Seen in the wild as
# "undefined@Property" -- a JavaScript extractor interpolating an undefined
# variable. Left alone these parse as a perfectly good unit and the import
# creates a real SSID, AP Group and policies in R1 called "undefined@...".
JUNK_UNIT_TOKENS = {"undefined", "null", "none", "nan", "nil", "-"}


def is_junk_unit_token(token: Optional[str]) -> bool:
    """True if a unit token is an export artefact rather than a real unit."""
    if token is None:
        return True
    t = token.strip()
    return not t or t.lower() in JUNK_UNIT_TOKENS


def unit_from_ssid(ssid: Optional[str]) -> Optional[str]:
    """
    Unit token for a usable unit SSID, else None.

    None means "do not treat this as a unit" for BOTH a property-wide SSID
    (no '@') and a junk one. Callers that need to tell those apart -- notably
    property-wide detection, which must not adopt a junk SSID -- should use
    is_property_ssid() / is_junk_unit_ssid() rather than inverting this.
    """
    if not ssid:
        return None
    match = UNIT_SSID_PATTERN.match(ssid)
    if not match:
        return None
    token = match.group(1).strip()
    return None if is_junk_unit_token(token) else token


def is_property_ssid(ssid: Optional[str]) -> bool:
    """True for an SSID carrying no '@' at all -- the property-wide shape."""
    return bool(ssid) and not UNIT_SSID_PATTERN.match(ssid)


def is_junk_unit_ssid(ssid: Optional[str]) -> bool:
    """True for an SSID shaped like a unit whose unit token is unusable."""
    if not ssid:
        return False
    match = UNIT_SSID_PATTERN.match(ssid)
    return bool(match) and is_junk_unit_token(match.group(1))


def property_from_ssid(ssid: Optional[str]) -> Optional[str]:
    """Property portion of a unit SSID, or None when there is no '@'."""
    if not ssid:
        return None
    match = UNIT_SSID_PATTERN.match(ssid)
    return match.group(2) if match else None


def is_unit_ssid(ssid: Optional[str]) -> bool:
    """True only for a unit SSID we will actually build something for."""
    return unit_from_ssid(ssid) is not None
