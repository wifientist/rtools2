"""
Migration Audit Schemas

Three-way comparison: SZ Source → Expected R1 → Actual R1.
Used by the auditor service to produce structured audit reports.
"""

from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any
from enum import Enum


class AuditStatus(str, Enum):
    """Per-item audit verdict."""
    OK = "ok"
    WARNING = "warning"
    MISSING = "missing"
    EXTRA = "extra"
    UNSUPPORTED = "unsupported"


class FieldDiff(BaseModel):
    """Single field-level discrepancy."""
    field: str
    expected: Optional[Any] = None
    actual: Optional[Any] = None
    severity: str = "info"  # info | warning | error


class FieldComparisonItem(BaseModel):
    """Deep field-by-field comparison between SZ and R1 for a single field."""
    section: str
    label: str
    sz_value: Optional[Any] = None
    r1_value: Optional[Any] = None
    match: bool = True
    sz_only: bool = False


class NetworkAuditItem(BaseModel):
    """Per-WLAN audit result: three-column comparison."""
    # SZ source
    sz_wlan_id: str
    sz_wlan_name: str
    sz_ssid: str
    sz_auth_type: str
    sz_vlan_id: Optional[int] = None
    sz_encryption_method: Optional[str] = None

    # Expected R1 (derived from mapper)
    expected_r1_name: str
    expected_r1_type: str  # psk | open | aaa | dpsk | unsupported
    expected_r1_ssid: str

    # Actual R1 (from live inventory)
    actual_r1_id: Optional[str] = None
    actual_r1_name: Optional[str] = None
    actual_r1_ssid: Optional[str] = None
    actual_r1_type: Optional[str] = None
    actual_r1_vlan: Optional[int] = None

    # PSK passphrase SZ vs R1, compared server-side as keyed fingerprints:
    # match | differs | missing_in_r1 | missing_in_sz | unknown; None when not a PSK pair.
    # Never carries a value.
    passphrase_check: Optional[str] = None
    passphrase_note: str = ""

    # Deep field comparisons (from field_mappings.compare_fields)
    field_comparisons: List[FieldComparisonItem] = Field(default_factory=list)

    # Verdict
    status: AuditStatus
    diffs: List[FieldDiff] = Field(default_factory=list)
    notes: str = ""


class APGroupActivationAudit(BaseModel):
    """Per-AP-Group SSID activation coverage check."""
    sz_ap_group_name: str
    sz_ssid_count: int
    expected_ssids: List[str] = Field(default_factory=list)
    actual_ssids: List[str] = Field(default_factory=list)
    missing_ssids: List[str] = Field(default_factory=list)
    extra_ssids: List[str] = Field(default_factory=list)
    r1_ap_group_id: Optional[str] = None
    r1_ap_group_found: bool = False


class ResourceCoverage(BaseModel):
    """Supporting resource coverage (DPSK pools, identity groups, etc.)."""
    resource_type: str
    expected_count: int = 0
    actual_count: int = 0
    matched: List[str] = Field(default_factory=list)
    missing: List[str] = Field(default_factory=list)


class AuthPath(str, Enum):
    """How an R1 network authenticates, read from what is actually linked to it."""
    EXTERNAL = "external"   # RADIUS server profile linked (e.g. still Cloudpath)
    INTERNAL = "internal"   # R1 DPSK service + identity groups
    HYBRID = "hybrid"       # both linked -- reported, not audited in depth
    NONE = "none"           # neither -- nothing can authenticate


class AuthIssue(BaseModel):
    severity: str  # info | warning | error
    message: str


class RadiusServerCheck(BaseModel):
    """One RADIUS role (authentication or accounting): SZ service vs linked R1 profile."""
    role: str  # authentication | accounting
    sz_service_name: Optional[str] = None
    sz_primary: Optional[str] = None      # "ip:port"
    sz_secondary: Optional[str] = None
    r1_profile_id: Optional[str] = None
    r1_profile_name: Optional[str] = None
    r1_primary: Optional[str] = None
    r1_secondary: Optional[str] = None
    primary_match: Optional[bool] = None  # None = nothing to compare
    secondary_match: Optional[bool] = None
    # True = the migration's placeholder is still the shared secret.
    # The secret itself is never read into the report.
    placeholder_secret: Optional[bool] = None


class IdentityGroupCount(BaseModel):
    id: str
    name: Optional[str] = None
    identity_count: Optional[int] = None  # None = could not be read


class DpskPoolCheck(BaseModel):
    id: str
    name: Optional[str] = None
    passphrase_count: Optional[int] = None  # None = could not be read
    identity_groups: List[IdentityGroupCount] = Field(default_factory=list)


class AuthPathAudit(BaseModel):
    """
    Per-WLAN authentication audit for RADIUS- and DPSK-backed WLANs.

    Quantities only. Passphrases and shared secrets are never part of this
    model; an external server's identities are not visible at all, so on the
    external path the counts stay None.
    """
    sz_wlan_id: str
    sz_wlan_name: str
    sz_ssid: str
    sz_auth_type: str
    # Where SZ sent authentication: external_radius | external_dpsk | sz_dpsk
    sz_backend: str
    r1_network_id: Optional[str] = None
    r1_network_name: Optional[str] = None
    r1_network_type: Optional[str] = None

    path: AuthPath
    radius: List[RadiusServerCheck] = Field(default_factory=list)
    auth_proxy: Optional[bool] = None
    accounting_proxy: Optional[bool] = None

    pools: List[DpskPoolCheck] = Field(default_factory=list)
    passphrase_total: Optional[int] = None
    identity_total: Optional[int] = None

    status: AuditStatus
    issues: List[AuthIssue] = Field(default_factory=list)


class MatchMethod(str, Enum):
    """How an SZ object was paired with an R1 one. Shown, so a guess is visible as a guess."""
    NAME = "name"              # exact name
    NAME_CASE = "name_case"    # name, ignoring case
    SSID = "ssid"              # by SSID (name differed)
    DEFAULT = "default"        # SZ "default" AP Group <-> the venue's default R1 group
    SERIAL = "serial"
    MAC = "mac"
    NONE = "none"              # no R1 counterpart


class WlanMatch(BaseModel):
    sz_wlan_id: str
    sz_name: str
    sz_ssid: str
    r1_network_id: Optional[str] = None
    r1_name: Optional[str] = None
    r1_ssid: Optional[str] = None
    method: MatchMethod = MatchMethod.NONE
    # Found in the tenant but not activated in this venue
    activated_in_venue: bool = False
    notes: List[str] = Field(default_factory=list)


class ApGroupMatch(BaseModel):
    sz_ap_group_id: str
    sz_name: str
    sz_ap_count: int = 0
    r1_ap_group_id: Optional[str] = None
    r1_name: Optional[str] = None
    r1_ap_count: int = 0
    method: MatchMethod = MatchMethod.NONE
    notes: List[str] = Field(default_factory=list)


class ApMatch(BaseModel):
    """One physical AP across both controllers. Either side may be absent."""
    name: Optional[str] = None
    serial: Optional[str] = None
    mac: Optional[str] = None
    model: Optional[str] = None
    sz_ap_group: Optional[str] = None
    r1_ap_group: Optional[str] = None
    # R1 group the AP should be in, from the AP Group pairing (None if unpaired)
    expected_r1_ap_group: Optional[str] = None
    method: MatchMethod = MatchMethod.NONE
    # in_both / sz_only (not in the R1 venue yet) / r1_only
    presence: str = "in_both"
    group_aligned: Optional[bool] = None
    r1_status: Optional[str] = None
    # SZ deviceName vs R1 name: exact / case / differs (None if one side is absent)
    sz_name: Optional[str] = None
    r1_name: Optional[str] = None
    name_alignment: Optional[str] = None
    # Raw keys as each controller reports them (for lookups, not display)
    sz_ap_mac: Optional[str] = None
    r1_serial: Optional[str] = None


class AuditMatching(BaseModel):
    """Checkpoint 1: what pairs with what, before any setting is compared."""
    wlans: List[WlanMatch] = Field(default_factory=list)
    r1_only_networks: List[Dict[str, Any]] = Field(default_factory=list)
    ap_groups: List[ApGroupMatch] = Field(default_factory=list)
    r1_only_ap_groups: List[Dict[str, Any]] = Field(default_factory=list)
    aps: List[ApMatch] = Field(default_factory=list)
    counts: Dict[str, int] = Field(default_factory=dict)


class SettingComparison(BaseModel):
    """One zone/venue-level setting, SZ vs R1, both normalised to R1's encoding."""
    section: str
    label: str
    sz_value: Optional[Any] = None
    r1_value: Optional[Any] = None
    # match | differs | sz_only (no R1 field) | unmappable (SZ value has no R1
    # encoding) | unavailable (R1 read failed) | review (needs a human eye)
    status: str
    note: str = ""


class VenueSettingsAudit(BaseModel):
    """Checkpoint 1b: SZ zone settings vs R1 venue Wi-Fi settings."""
    rows: List[SettingComparison] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class ApBroadcast(BaseModel):
    """Which SSIDs one AP broadcasts on each controller (radio-agnostic for now)."""
    name: Optional[str] = None
    serial: Optional[str] = None
    mac: Optional[str] = None
    sz_ap_group: Optional[str] = None
    r1_ap_group: Optional[str] = None
    presence: str = "in_both"            # in_both | sz_only | r1_only
    sz_ssids: List[str] = Field(default_factory=list)
    r1_ssids: List[str] = Field(default_factory=list)
    missing_in_r1: List[str] = Field(default_factory=list)
    extra_in_r1: List[str] = Field(default_factory=list)
    # match | differs | pending (not in R1 yet) | r1_only | unknown (SZ overrides unreadable)
    status: str = "match"
    # Where each SZ radio's WLAN Group came from, e.g. "5 GHz: AP override → wg-101"
    sz_sources: List[str] = Field(default_factory=list)


class SsidCoverage(BaseModel):
    """How many APs broadcast one SSID on each controller."""
    ssid: str
    sz_ap_count: int = 0
    r1_ap_count: int = 0                  # from AP Group membership (APs in this venue)
    r1_reported_ap_count: Optional[int] = None   # R1's own apCount on the network
    missing_in_r1: int = 0
    extra_in_r1: int = 0
    sample_missing: List[str] = Field(default_factory=list)
    sample_extra: List[str] = Field(default_factory=list)
    status: str = "match"


class BroadcastGroup(BaseModel):
    """
    APs that broadcast exactly the same SSID set on SZ -- the groups SZ's
    overrides effectively create -- and where those APs sit in R1.
    """
    ssids: List[str] = Field(default_factory=list)
    sz_ap_count: int = 0
    sz_ap_groups: Dict[str, int] = Field(default_factory=dict)
    r1_ap_groups: Dict[str, int] = Field(default_factory=dict)   # R1 group -> APs of this set in it
    r1_not_in_venue: int = 0
    # R1 groups whose activations carry exactly these SSIDs
    r1_groups_matching_ssids: List[str] = Field(default_factory=list)
    status: str = "match"   # match | split | differs | pending
    sample_aps: List[str] = Field(default_factory=list)


class BroadcastAudit(BaseModel):
    """Checkpoint 2: per-AP SSID broadcast, resolved through each controller's hierarchy."""
    aps: List[ApBroadcast] = Field(default_factory=list)
    ssids: List[SsidCoverage] = Field(default_factory=list)
    groups: List[BroadcastGroup] = Field(default_factory=list)
    counts: Dict[str, int] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)


class AlignmentCategory(BaseModel):
    """
    One row of the end-of-run scorecard: SZ vs R1 for one category.

    status:
      aligned      everything that should match does
      partial      some aligned, some not
      misaligned   nothing that should match does
      pending      not comparable YET for a known reason (e.g. APs not moved)
      manual       the source isn't readable here; R1's side shown for a manual check
      not_checked  a later audit checkpoint covers this
    """
    key: str
    label: str
    sz_count: Optional[int] = None
    r1_count: Optional[int] = None
    aligned_count: Optional[int] = None
    status: str = "not_checked"
    details: List[str] = Field(default_factory=list)
    # Sub-rows, e.g. per-unit vs property-wide WLANs, per-field mismatches
    breakdown: List[Dict[str, Any]] = Field(default_factory=list)


class AuditSummary(BaseModel):
    """Top-level summary statistics."""
    total_sz_wlans: int = 0
    ok_count: int = 0
    warning_count: int = 0
    missing_count: int = 0
    extra_count: int = 0
    unsupported_count: int = 0
    ap_group_coverage: int = 0  # percentage of AP groups fully covered
    total_diffs: int = 0
    auth_external_count: int = 0
    auth_internal_count: int = 0
    auth_hybrid_count: int = 0
    auth_none_count: int = 0
    auth_error_count: int = 0


class MigrationAuditReport(BaseModel):
    """Complete cross-controller migration audit report."""
    # Metadata
    sz_zone_name: str
    r1_venue_name: str
    sz_snapshot_job_id: str
    r1_snapshot_job_id: str
    audit_timestamp: str

    # Results
    summary: AuditSummary
    scorecard: List[AlignmentCategory] = Field(default_factory=list)
    matching: AuditMatching = Field(default_factory=AuditMatching)
    venue_settings: Optional[VenueSettingsAudit] = None   # None: no live R1 read this run
    broadcast: Optional[BroadcastAudit] = None
    # Per side: {cached, captured_at, age_seconds, expires_in_seconds, ...}
    data_sources: Dict[str, Any] = Field(default_factory=dict)
    networks: List[NetworkAuditItem] = Field(default_factory=list)
    extra_r1_networks: List[Dict[str, Any]] = Field(default_factory=list)
    ap_group_activations: List[APGroupActivationAudit] = Field(default_factory=list)
    resource_coverage: List[ResourceCoverage] = Field(default_factory=list)
    auth_paths: List[AuthPathAudit] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
