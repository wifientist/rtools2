"""
SZ Migration Schemas

Pydantic models for SZ deep extraction and migration snapshots.
Every model carries a `raw` field with the complete API response
so unmapped fields are always available for future use.
"""

from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any
from datetime import datetime


class SZRadioConfig(BaseModel):
    """Per-radio WLAN Group assignment from Zone or AP Group radioConfig"""
    radio_24g: Optional[str] = Field(default=None, description="WLAN Group ID for 2.4 GHz")
    radio_5g: Optional[str] = Field(default=None, description="WLAN Group ID for 5 GHz")
    radio_5g_lower: Optional[str] = Field(default=None, description="WLAN Group ID for 5 GHz lower")
    radio_5g_upper: Optional[str] = Field(default=None, description="WLAN Group ID for 5 GHz upper")
    radio_6g: Optional[str] = Field(default=None, description="WLAN Group ID for 6 GHz")

    @classmethod
    def from_sz_response(cls, radio_config: Dict[str, Any]) -> "SZRadioConfig":
        """Parse radioConfig from SZ zone or AP group response."""
        def get_wlan_group_id(band_config: Optional[Dict]) -> Optional[str]:
            if not band_config:
                return None
            return band_config.get("wlanGroupId")

        return cls(
            radio_24g=get_wlan_group_id(radio_config.get("radio24g")),
            radio_5g=get_wlan_group_id(radio_config.get("radio5g")),
            radio_5g_lower=get_wlan_group_id(radio_config.get("radio5gLower")),
            radio_5g_upper=get_wlan_group_id(radio_config.get("radio5gUpper")),
            radio_6g=get_wlan_group_id(radio_config.get("radio6g")),
        )


class SZZoneSnapshot(BaseModel):
    """Zone details with radioConfig and country code"""
    id: str
    name: str
    description: Optional[str] = None
    country_code: Optional[str] = None
    radio_config: Optional[SZRadioConfig] = None
    raw: Dict[str, Any] = Field(default_factory=dict, description="Complete zone API response")


class SZWLANGroupMember(BaseModel):
    """A WLAN member within a WLAN Group"""
    id: str
    name: Optional[str] = None
    ssid: Optional[str] = None
    access_vlan: Optional[int] = None
    nas_id: Optional[str] = None


class SZWLANGroup(BaseModel):
    """WLAN Group with resolved member WLANs"""
    id: str
    name: str
    description: Optional[str] = None
    members: List[SZWLANGroupMember] = Field(default_factory=list)
    raw: Dict[str, Any] = Field(default_factory=dict)


class SZWLANFull(BaseModel):
    """Complete WLAN configuration — all 54 properties parsed"""
    id: str
    name: str
    ssid: str
    description: Optional[str] = None

    # Classification (derived from raw via WlanService.extract_*)
    auth_type: str = Field(description="Classified auth type: WPA2-PSK, DPSK, WPA2-Enterprise, Open, etc.")
    encryption_method: Optional[str] = None
    vlan_id: Optional[int] = None

    # Foreign key references (IDs of objects we need to chase)
    auth_service_id: Optional[str] = None
    accounting_service_id: Optional[str] = None
    device_policy_id: Optional[str] = None
    l2_acl_id: Optional[str] = None
    firewall_profile_id: Optional[str] = None
    firewall_l2_policy_id: Optional[str] = None
    firewall_l3_policy_id: Optional[str] = None
    firewall_app_policy_id: Optional[str] = None
    firewall_device_policy_id: Optional[str] = None
    firewall_url_filtering_policy_id: Optional[str] = None
    default_user_traffic_profile_id: Optional[str] = None
    diff_serv_profile_id: Optional[str] = None
    access_ipsec_profile_id: Optional[str] = None
    access_tunnel_profile_id: Optional[str] = None
    split_tunnel_profile_id: Optional[str] = None
    portal_service_profile_id: Optional[str] = None
    hotspot20_profile_id: Optional[str] = None
    vlan_pooling_id: Optional[str] = None
    dns_server_profile_id: Optional[str] = None
    precedence_profile_id: Optional[str] = None

    # Inline config blocks (embedded in WLAN, no separate fetch)
    encryption: Optional[Dict[str, Any]] = None
    dpsk: Optional[Dict[str, Any]] = None
    external_dpsk: Optional[Dict[str, Any]] = None
    vlan: Optional[Dict[str, Any]] = None
    radius_options: Optional[Dict[str, Any]] = None
    mac_auth: Optional[Dict[str, Any]] = None
    advanced_options: Optional[Dict[str, Any]] = None
    schedule: Optional[Dict[str, Any]] = None

    raw: Dict[str, Any] = Field(default_factory=dict, description="Complete WLAN detail API response")

    @classmethod
    def from_sz_response(cls, wlan: Dict[str, Any], auth_type: str) -> "SZWLANFull":
        """Build from a SZ WLAN detail response."""
        def ref_id(obj) -> Optional[str]:
            """Extract ID from a reference field like {id: '...', name: '...'}"""
            if not obj or not isinstance(obj, dict):
                return None
            return obj.get("id")

        return cls(
            id=wlan["id"],
            name=wlan.get("name", ""),
            ssid=wlan.get("ssid", ""),
            description=wlan.get("description"),
            auth_type=auth_type,
            encryption_method=wlan.get("encryption", {}).get("method") if wlan.get("encryption") else None,
            vlan_id=wlan.get("vlan", {}).get("accessVlan") if wlan.get("vlan") else None,
            # Foreign key references
            auth_service_id=ref_id(wlan.get("authServiceOrProfile")),
            accounting_service_id=ref_id(wlan.get("accountingServiceOrProfile")),
            device_policy_id=ref_id(wlan.get("devicePolicy")),
            l2_acl_id=ref_id(wlan.get("l2ACL")),
            firewall_profile_id=wlan.get("firewallProfileId"),
            firewall_l2_policy_id=wlan.get("firewallL2AccessControlPolicyId"),
            firewall_l3_policy_id=wlan.get("firewallL3AccessControlPolicyId"),
            firewall_app_policy_id=wlan.get("firewallAppPolicyId"),
            firewall_device_policy_id=wlan.get("firewallDevicePolicyId"),
            firewall_url_filtering_policy_id=wlan.get("firewallUrlFilteringPolicyId"),
            default_user_traffic_profile_id=ref_id(wlan.get("defaultUserTrafficProfile")),
            diff_serv_profile_id=ref_id(wlan.get("diffServProfile")),
            access_ipsec_profile_id=ref_id(wlan.get("accessIpsecProfile")),
            access_tunnel_profile_id=ref_id(wlan.get("accessTunnelProfile")),
            split_tunnel_profile_id=wlan.get("splitTunnelProfileId"),
            portal_service_profile_id=ref_id(wlan.get("portalServiceProfile")),
            hotspot20_profile_id=ref_id(wlan.get("hotspot20Profile")),
            vlan_pooling_id=ref_id(wlan.get("vlan", {}).get("vlanPooling")) if wlan.get("vlan") else None,
            dns_server_profile_id=ref_id(wlan.get("dnsServerProfile")),
            precedence_profile_id=wlan.get("precedenceProfileId"),
            # Inline config blocks
            encryption=wlan.get("encryption"),
            dpsk=wlan.get("dpsk"),
            external_dpsk=wlan.get("externalDpsk"),
            vlan=wlan.get("vlan"),
            radius_options=wlan.get("radiusOptions"),
            mac_auth=wlan.get("macAuth"),
            advanced_options=wlan.get("advancedOptions"),
            schedule=wlan.get("schedule"),
            raw=wlan,
        )

    def get_all_reference_ids(self) -> List[tuple]:
        """
        Return all non-None foreign key references as (ref_type, ref_id) tuples.
        Used by the extractor to know which objects to chase.
        """
        refs = []
        ref_fields = [
            ("auth_service", self.auth_service_id),
            ("accounting_service", self.accounting_service_id),
            ("device_policy", self.device_policy_id),
            ("l2_acl", self.l2_acl_id),
            ("firewall_profile", self.firewall_profile_id),
            ("firewall_l2_policy", self.firewall_l2_policy_id),
            ("firewall_l3_policy", self.firewall_l3_policy_id),
            ("firewall_app_policy", self.firewall_app_policy_id),
            ("firewall_device_policy", self.firewall_device_policy_id),
            ("firewall_url_filtering_policy", self.firewall_url_filtering_policy_id),
            ("user_traffic_profile", self.default_user_traffic_profile_id),
            ("diff_serv_profile", self.diff_serv_profile_id),
            ("ipsec_profile", self.access_ipsec_profile_id),
            ("tunnel_profile", self.access_tunnel_profile_id),
            ("split_tunnel_profile", self.split_tunnel_profile_id),
            ("portal_service_profile", self.portal_service_profile_id),
            ("hotspot20_profile", self.hotspot20_profile_id),
            ("vlan_pooling", self.vlan_pooling_id),
            ("dns_server_profile", self.dns_server_profile_id),
            ("precedence_profile", self.precedence_profile_id),
        ]
        for ref_type, ref_id in ref_fields:
            if ref_id:
                refs.append((ref_type, ref_id))
        return refs


class SZAPGroupEnriched(BaseModel):
    """AP Group with parsed radioConfig per radio"""
    id: str
    name: str
    description: Optional[str] = None
    radio_config: Optional[SZRadioConfig] = None
    ap_count: int = 0
    raw: Dict[str, Any] = Field(default_factory=dict)


class SZReferencedObject(BaseModel):
    """Generic wrapper for a chased reference object (RADIUS server, policy, profile, etc.)"""
    ref_type: str = Field(description="Reference type: auth_service, device_policy, l2_acl, etc.")
    id: str
    name: Optional[str] = None
    raw: Dict[str, Any] = Field(default_factory=dict, description="Complete API response for this object")


class SZExtractionWarning(BaseModel):
    """A warning generated during extraction"""
    phase: str = Field(description="Extraction phase: wlans, ap_groups, references, etc.")
    message: str
    details: Optional[Dict[str, Any]] = None


class SZMigrationSnapshot(BaseModel):
    """Complete SZ extraction result for one zone — the M0 deliverable"""
    zone: SZZoneSnapshot
    wlans: List[SZWLANFull] = Field(default_factory=list)
    wlan_groups: List[SZWLANGroup] = Field(default_factory=list)
    ap_groups: List[SZAPGroupEnriched] = Field(default_factory=list)
    aps: List[Dict[str, Any]] = Field(default_factory=list, description="Raw AP data")
    referenced_objects: Dict[str, SZReferencedObject] = Field(
        default_factory=dict,
        description="Chased references keyed by 'ref_type:id'"
    )
    extraction_metadata: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[SZExtractionWarning] = Field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        """Quick summary for API responses and logging."""
        return {
            "zone_name": self.zone.name,
            "zone_id": self.zone.id,
            "wlan_count": len(self.wlans),
            "wlan_group_count": len(self.wlan_groups),
            "ap_group_count": len(self.ap_groups),
            "ap_count": len(self.aps),
            "referenced_object_count": len(self.referenced_objects),
            "warning_count": len(self.warnings),
            "auth_types": self._count_auth_types(),
        }

    def _count_auth_types(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for wlan in self.wlans:
            counts[wlan.auth_type] = counts.get(wlan.auth_type, 0) + 1
        return counts


# ── M2: WLAN Group Resolution Models ─────────────────────────────────────


class WLANActivationEntry(BaseModel):
    """One WLAN active on one AP Group, with radio band details."""
    wlan_id: str
    wlan_name: str
    ssid: str
    auth_type: str = Field(description="SZ auth type: WPA2-PSK, DPSK, WPA2-Enterprise, etc.")
    ap_group_id: str
    ap_group_name: str
    radios: List[str] = Field(description="Radio bands active: ['2.4', '5', '6']")
    source: str = Field(description="zone_default | ap_group_override")
    ap_count: int = Field(description="Number of APs in this AP Group")


class APGroupSSIDSummary(BaseModel):
    """Per-AP-Group SSID count and limit check."""
    ap_group_id: str
    ap_group_name: str
    ap_count: int
    ssid_count: int
    limit: int = 15
    over_limit: bool = False
    ssids: List[str] = Field(default_factory=list, description="Unique SSIDs on this AP Group")


class ResolverResult(BaseModel):
    """Complete output of the WLAN Group Resolution Engine."""
    activations: List[WLANActivationEntry] = Field(default_factory=list)
    ap_group_summaries: List[APGroupSSIDSummary] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    blocked: bool = Field(default=False, description="True if any AP Group exceeds 15-SSID limit")

    def summary(self) -> Dict[str, Any]:
        return {
            "activation_count": len(self.activations),
            "ap_group_count": len(self.ap_group_summaries),
            "blocked": self.blocked,
            "warning_count": len(self.warnings),
            "over_limit_groups": [
                s.ap_group_name for s in self.ap_group_summaries if s.over_limit
            ],
        }
