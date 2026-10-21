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
    networks: List[NetworkAuditItem] = Field(default_factory=list)
    extra_r1_networks: List[Dict[str, Any]] = Field(default_factory=list)
    ap_group_activations: List[APGroupActivationAudit] = Field(default_factory=list)
    resource_coverage: List[ResourceCoverage] = Field(default_factory=list)
    auth_paths: List[AuthPathAudit] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
