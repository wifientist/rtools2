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
    warnings: List[str] = Field(default_factory=list)
