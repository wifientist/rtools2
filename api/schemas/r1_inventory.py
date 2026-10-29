"""
R1 Venue Inventory Schemas

Pydantic models for capturing the complete state of an R1 venue.
Built as a standalone, reusable model — not specific to sz_migration.
"""

from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any
from datetime import datetime


class R1VenueInventory(BaseModel):
    """Complete state snapshot of an R1 venue before any writes."""
    venue_id: str
    venue_name: str
    tenant_id: str
    venue: Dict[str, Any] = Field(default_factory=dict, description="Full venue details")

    wifi_networks: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="All WiFi networks active in this venue (name, SSID, type, id, venueApGroups)"
    )
    tenant_networks: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Every network in the tenant reduced to id/name/ssid/nwSubType -- so the "
            "audit can tell \"exists but is not activated here\" from \"missing\""
        ),
    )
    ap_groups: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="All AP groups in this venue"
    )
    aps: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="All APs in this venue"
    )
    dpsk_pools: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="DPSK pools for this tenant (filtered by relevance if possible)"
    )
    identity_groups: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Identity groups for this tenant"
    )
    radius_attribute_groups: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="RADIUS attribute groups for this tenant"
    )

    tenant_ap_lookup: Optional[Dict[str, Dict[str, Any]]] = Field(
        default=None,
        description=(
            "Serial -> {venue_id, venue_name, name} for APs the audit looked up "
            "tenant-wide (SZ APs missing from this venue). None = not searched; "
            "a searched serial absent from the map is not in the tenant at all."
        ),
    )

    snapshot_metadata: Dict[str, Any] = Field(default_factory=dict)

    # ── Lookup helpers ───────────────────────────────────────────────

    def network_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Find a WiFi network by exact name match."""
        for n in self.wifi_networks:
            if n.get("name") == name:
                return n
        return None

    def network_in_tenant(self, name: str) -> Optional[Dict[str, Any]]:
        """
        A network by exact name anywhere in the tenant: this venue's first
        (full records), then the slim tenant list (id, name, ssid, nwSubType).
        Networks created but never activated here -- a partial run, staged
        WLANs -- are only in the tenant list.
        """
        return self.network_by_name(name) or next(
            (n for n in self.tenant_networks if n.get("name") == name), None)

    def network_by_ssid(self, ssid: str) -> Optional[Dict[str, Any]]:
        """Find a WiFi network by SSID."""
        for n in self.wifi_networks:
            if n.get("ssid") == ssid:
                return n
        return None

    def default_ap_group(self) -> Optional[Dict[str, Any]]:
        """The venue's default AP group: isDefault, or (older captures) an empty name."""
        for g in self.ap_groups:
            if g.get("isDefault") is True:
                return g
        for g in self.ap_groups:
            if not (g.get("name") or "").strip():
                return g
        return None

    def ap_group_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Find an AP group by exact name match."""
        for g in self.ap_groups:
            if g.get("name") == name:
                return g
        return None

    def dpsk_pool_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Find a DPSK pool by exact name match."""
        for p in self.dpsk_pools:
            if p.get("name") == name:
                return p
        return None

    def identity_group_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Find an identity group by exact name match."""
        for g in self.identity_groups:
            if g.get("name") == name:
                return g
        return None

    def summary(self) -> Dict[str, Any]:
        """Quick summary for API responses and logging."""
        return {
            "venue_id": self.venue_id,
            "venue_name": self.venue_name,
            "tenant_id": self.tenant_id,
            "wifi_network_count": len(self.wifi_networks),
            "ap_group_count": len(self.ap_groups),
            "ap_count": len(self.aps),
            "dpsk_pool_count": len(self.dpsk_pools),
            "identity_group_count": len(self.identity_groups),
            "radius_attribute_group_count": len(self.radius_attribute_groups),
        }
