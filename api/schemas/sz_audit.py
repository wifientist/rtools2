"""
SmartZone Audit Schemas

Pydantic models for SZ audit responses.
"""

from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from datetime import datetime


class WlanSummary(BaseModel):
    """Summary of a WLAN configuration"""
    id: str
    name: str
    ssid: str
    auth_type: str = Field(description="Authentication type: Open, WPA2-PSK, WPA2-Enterprise, DPSK, etc.")
    encryption: str = Field(description="Encryption algorithm: AES, TKIP, None, etc.")
    vlan: Optional[int] = Field(default=None, description="VLAN ID if configured")


class ApGroupSummary(BaseModel):
    """Summary of an AP Group"""
    id: str
    name: str
    ap_count: int = Field(default=0, description="Number of APs in this group")


class WlanGroupSummary(BaseModel):
    """Summary of a WLAN Group"""
    id: str
    name: str
    wlan_count: int = Field(default=0, description="Number of WLANs in this group")


class ApStatusBreakdown(BaseModel):
    """Breakdown of AP statuses"""
    online: int = Field(default=0, description="APs with 'Connect' status")
    offline: int = Field(default=0, description="APs with 'Disconnect' status")
    flagged: int = Field(default=0, description="APs with warning/flagged status")
    total: int = Field(default=0, description="Total AP count")


class FirmwareDistribution(BaseModel):
    """Distribution of firmware versions"""
    version: str
    count: int


class ModelDistribution(BaseModel):
    """Distribution of AP/switch models"""
    model: str
    count: int


class ZoneAudit(BaseModel):
    """Audit data for a single zone"""
    zone_id: str
    zone_name: str
    domain_id: str
    domain_name: str
    external_ips: List[str] = Field(default_factory=list, description="Unique external IPs seen from APs in this zone")

    # AP Info
    ap_status: ApStatusBreakdown
    ap_model_distribution: List[ModelDistribution] = Field(
        default_factory=list,
        description="Count of APs per model (e.g., R750: 45, R650: 30)"
    )
    ap_groups: List[ApGroupSummary] = Field(default_factory=list)
    ap_firmware_distribution: List[FirmwareDistribution] = Field(default_factory=list)

    # WLAN Info
    wlan_count: int = Field(default=0)
    wlan_groups: List[WlanGroupSummary] = Field(default_factory=list)
    wlans: List[WlanSummary] = Field(default_factory=list)
    wlan_type_breakdown: Dict[str, int] = Field(
        default_factory=dict,
        description="Count by auth type: {'WPA2-PSK': 5, 'DPSK': 3, ...}"
    )

    # Switch Groups matched to this zone by name
    matched_switch_groups: List["SwitchGroupSummary"] = Field(
        default_factory=list,
        description="Switch groups matched to this zone by name similarity"
    )

    # Flag indicating user manually set the mapping (takes priority over auto-matching)
    user_set_mapping: bool = Field(
        default=False,
        description="True if user manually set the switch group mapping (prevents auto-match override)"
    )


class SwitchGroupSummary(BaseModel):
    """Summary of a Switch Group"""
    id: str
    name: str
    switch_count: int = Field(default=0)
    switches_online: int = Field(default=0)
    switches_offline: int = Field(default=0)
    firmware_versions: List[FirmwareDistribution] = Field(
        default_factory=list,
        description="Firmware distribution for switches in this group"
    )
    user_set: bool = Field(
        default=False,
        description="True if user manually assigned this switch group to a zone"
    )


class SwitchSummary(BaseModel):
    """Summary of a single switch"""
    id: str
    name: str
    model: str
    status: str
    firmware: str
    ip_address: Optional[str] = None


class DomainAudit(BaseModel):
    """Audit data for a domain (admin domain)"""
    domain_id: str
    domain_name: str
    parent_domain_id: Optional[str] = Field(default=None, description="Parent domain ID for nested domains")
    parent_domain_name: Optional[str] = Field(default=None)

    # Aggregated stats for this domain
    zone_count: int = Field(default=0)
    total_aps: int = Field(default=0)
    total_wlans: int = Field(default=0)

    # Switching
    switch_groups: List[SwitchGroupSummary] = Field(default_factory=list)
    total_switches: int = Field(default=0)
    switch_firmware_distribution: List[FirmwareDistribution] = Field(default_factory=list)

    # Child domains (for hierarchy)
    children: List["DomainAudit"] = Field(default_factory=list)


# Enable forward reference resolution
SwitchGroupSummary.model_rebuild()
ZoneAudit.model_rebuild()
DomainAudit.model_rebuild()


class SZAuditResult(BaseModel):
    """Complete audit result for a single SmartZone controller"""
    controller_id: int
    controller_name: str
    host: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Controller Info
    cluster_ip: Optional[str] = Field(default=None, description="Cluster management IP")
    controller_firmware: Optional[str] = Field(default=None, description="Controller firmware version")

    # Domain Hierarchy
    domains: List[DomainAudit] = Field(default_factory=list)

    # Zones (flat list with domain references)
    zones: List[ZoneAudit] = Field(default_factory=list)

    # Global Summaries
    total_domains: int = Field(default=0)
    total_zones: int = Field(default=0)
    total_aps: int = Field(default=0)
    total_wlans: int = Field(default=0)
    total_switches: int = Field(default=0)

    # Global Breakdowns
    ap_model_summary: List[ModelDistribution] = Field(
        default_factory=list,
        description="Aggregate AP models across all zones"
    )
    ap_firmware_summary: List[FirmwareDistribution] = Field(default_factory=list)
    switch_firmware_summary: List[FirmwareDistribution] = Field(default_factory=list)
    wlan_type_summary: Dict[str, int] = Field(
        default_factory=dict,
        description="Aggregate WLAN auth types across all zones"
    )

    # Error handling
    error: Optional[str] = Field(default=None, description="Error message if audit failed")
    partial_errors: List[str] = Field(
        default_factory=list,
        description="Non-fatal errors encountered during audit"
    )


class BatchAuditRequest(BaseModel):
    """Request for auditing multiple controllers"""
    controller_ids: List[int] = Field(..., description="List of controller IDs to audit")


class ZoneSwitchGroupMapping(BaseModel):
    """Mapping of zone IDs to switch group IDs for a single controller"""
    controller_id: int
    mappings: Dict[str, str] = Field(
        default_factory=dict,
        description="Dict of zone_id -> switch_group_id"
    )


class ExportAuditRequest(BaseModel):
    """Request for exporting audit data with optional switch group mappings"""
    controller_ids: List[int] = Field(..., description="List of controller IDs to audit")
    switch_group_mappings: List[ZoneSwitchGroupMapping] = Field(
        default_factory=list,
        description="Optional manual zone-to-switch-group mappings per controller"
    )


class BatchAuditResponse(BaseModel):
    """Response containing audit results for multiple controllers"""
    results: List[SZAuditResult]
    total_requested: int
    successful: int
    failed: int


class MatchCandidate(BaseModel):
    """A potential switch group match for a zone"""
    switch_group_id: str
    switch_group_name: str
    switch_count: int = Field(default=0)
    switches_online: int = Field(default=0)
    switches_offline: int = Field(default=0)
    score: int = Field(description="Match score (0-100+)")
    match_type: str = Field(description="Type of match: exact, normalized, contains, word-overlap, prefix")
    match_reason: str = Field(description="Human-readable explanation of the match")
    same_domain: bool = Field(default=False, description="Whether switch group is in same domain as zone")


class ZoneMatchCandidates(BaseModel):
    """Match candidates for a single zone"""
    zone_id: str
    zone_name: str
    domain_id: str
    domain_name: str
    current_match: Optional[SwitchGroupSummary] = Field(
        default=None,
        description="Currently assigned switch group (if any)"
    )
    is_user_set: bool = Field(
        default=False,
        description="Whether current match was manually set by user"
    )
    candidates: List[MatchCandidate] = Field(
        default_factory=list,
        description="Top match candidates sorted by score descending"
    )


class ControllerMatchCandidates(BaseModel):
    """Match candidates for all zones in a controller"""
    controller_id: int
    zones: List[ZoneMatchCandidates] = Field(default_factory=list)
