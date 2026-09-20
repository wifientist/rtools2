"""
What is actually deployed at a venue: AP groups, their APs, and their SSIDs.

WHAT THIS REPLACES

The old /cloudpath-import/audit counted four things and returned the raw R1
payloads for the UI to dump as text. Counts and raw JSON answer neither
question anyone actually has when they open a venue audit:

    which SSIDs are broadcasting on which AP groups, and
    which APs are in those groups

and for a DPSK SSID, the thing that makes it work at all -- the DPSK service
behind it and the identity groups feeding that service. All of that was
present in the responses; none of it was joined.

THE JOIN

    wifiNetworks  ->  venueApGroups[]  ->  {venueId, apGroupIds[], isAllApGroups}
    venue         ->  AP groups        ->  id, name
    venue         ->  APs              ->  apGroupId
    DPSK network  ->  GET /wifiNetworks/{id}/dpskServices  ->  pool
    pool          ->  identity groups whose dpskPoolId is that pool

Two things make this cheap. `venueApGroups` already carries the activation
binding, so no per-network activation lookup is needed; and `nwSubType` is
'dpsk' right there in the list, so DPSK networks are identifiable without
fetching each one. The only per-item calls are the DPSK service readback,
which runs once per DPSK SSID at this venue (a handful), and an identity
count per identity group.

TWO VIEWS, ONE FETCH

Both orientations answer real questions -- "what is this AP group serving?"
and "where is this SSID live?" -- and once the data is joined, emitting both
costs nothing. `ap_groups` is group-first, `ssids` is SSID-first, and they
are built from the same maps so they cannot disagree.

VENUE-WIDE IS NOT AN AP GROUP

A network with isAllApGroups=true broadcasts on every AP group at the venue,
including ones created later. It is reported separately rather than copied
into all of them: listing it under 249 groups would bury the per-group
activations that were configured deliberately, and would misrepresent one
binding as many.

INFORM ONLY. Nothing here writes to R1.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Bounded fan-out for the two per-item reads (DPSK link, identity counts).
DETAIL_CONCURRENCY = 8

# R1 names a venue's default AP group with an empty string. Give it something
# readable rather than rendering a blank row.
DEFAULT_AP_GROUP_LABEL = "(default AP group)"


# ==================== Models ====================


class VenueAuditRequest(BaseModel):
    controller_id: int = Field(..., description="RuckusONE controller ID")
    tenant_id: Optional[str] = Field(None, description="Tenant/EC ID (required for MSP)")
    venue_id: str = Field(..., description="Venue to audit")


class AuditAp(BaseModel):
    name: str = ""
    serial: str = ""
    model: Optional[str] = None
    status: Optional[str] = None


class AuditIdentityGroup(BaseModel):
    id: str
    name: str
    identity_count: Optional[int] = None   # None = could not be read


class AuditSsid(BaseModel):
    id: str
    name: str = ""
    ssid: str = ""
    security: Optional[str] = None
    vlan: Optional[int] = None
    is_dpsk: bool = False
    venue_wide: bool = False
    ap_group_ids: List[str] = Field(default_factory=list)
    ap_group_names: List[str] = Field(default_factory=list)
    # DPSK only
    dpsk_service_id: Optional[str] = None
    dpsk_service_name: Optional[str] = None
    identity_groups: List[AuditIdentityGroup] = Field(default_factory=list)
    issues: List[str] = Field(default_factory=list)


class AuditApGroup(BaseModel):
    id: str
    name: str
    is_default: bool = False
    aps: List[AuditAp] = Field(default_factory=list)
    ssids: List[AuditSsid] = Field(default_factory=list)
    issues: List[str] = Field(default_factory=list)


class VenueAuditResponse(BaseModel):
    venue_id: str
    venue_name: str
    totals: Dict[str, int] = Field(default_factory=dict)
    ap_groups: List[AuditApGroup] = Field(default_factory=list)
    venue_wide_ssids: List[AuditSsid] = Field(default_factory=list)
    ssids: List[AuditSsid] = Field(default_factory=list)
    unassigned_aps: List[AuditAp] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


# ==================== Collection ====================


def _ap_record(ap: Dict[str, Any]) -> AuditAp:
    return AuditAp(
        name=str(ap.get("name") or ""),
        serial=str(ap.get("serialNumber") or ap.get("serial") or ""),
        model=ap.get("model"),
        status=ap.get("status") or ap.get("networkStatus"),
    )


async def _dpsk_links(
    r1_client, tenant_id: str, network_ids: List[str]
) -> Dict[str, Dict[str, Any]]:
    """
    network id -> its linked DPSK pool.

    The link is write-only from the network side: a wifiNetworks/query row
    never carries a dpskService field however many you ask for, so each DPSK
    network has to be asked directly. Only DPSK networks at this venue are
    asked, which is a handful rather than the tenant.
    """
    semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)
    out: Dict[str, Dict[str, Any]] = {}

    async def fetch(network_id: str) -> None:
        async with semaphore:
            try:
                linked = await r1_client.networks.get_dpsk_services_on_network(
                    network_id=network_id, tenant_id=tenant_id
                )
            except Exception as e:
                logger.warning(f"Could not read DPSK link on {network_id}: {e}")
                out[network_id] = {"error": str(e)}
                return
            rows = (
                linked.get("data", linked.get("content", []))
                if isinstance(linked, dict) else (linked or [])
            )
            pool = next((r for r in rows if isinstance(r, dict) and r.get("id")), None)
            out[network_id] = {"pool": pool, "error": None}

    await asyncio.gather(*(fetch(n) for n in network_ids))
    return out


async def _identity_counts(
    r1_client, tenant_id: str, group_ids: List[str]
) -> Dict[str, Optional[int]]:
    """
    group id -> identity count, from page 0's totalElements.

    One cheap call per group rather than paging every identity: the audit
    wants "is this group populated", not the roster.
    """
    semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)
    out: Dict[str, Optional[int]] = {}

    async def fetch(group_id: str) -> None:
        async with semaphore:
            try:
                resp = await r1_client.identity.get_identities_in_group(
                    group_id=group_id, tenant_id=tenant_id, page=0, size=1
                )
            except Exception as e:
                logger.warning(f"Could not count identities in {group_id}: {e}")
                out[group_id] = None
                return
            if isinstance(resp, dict):
                out[group_id] = (
                    resp.get("totalElements")
                    if resp.get("totalElements") is not None
                    else resp.get("totalItems")
                )
            else:
                out[group_id] = None

    await asyncio.gather(*(fetch(g) for g in group_ids))
    return out


# ==================== The audit ====================


async def run_venue_audit(r1_client, request: VenueAuditRequest) -> VenueAuditResponse:
    """Join a venue's AP groups, APs and SSIDs into something readable."""
    tenant_id = request.tenant_id
    venue_id = request.venue_id
    warnings: List[str] = []

    venue = await r1_client.venues.get_venue(tenant_id, venue_id)
    venue_name = venue.get("name", "Unknown") if isinstance(venue, dict) else "Unknown"

    # ---- AP groups at this venue ----
    try:
        raw_groups = await r1_client.venues.list_ap_groups_in_venue(tenant_id, venue_id)
    except Exception as e:
        logger.warning(f"Could not list AP groups for {venue_id}: {e}")
        raw_groups = []
        warnings.append(f"Could not list AP groups: {e}")

    group_names: Dict[str, str] = {}
    for g in raw_groups:
        gid = g.get("id")
        if gid:
            group_names[gid] = str(g.get("name") or "").strip() or DEFAULT_AP_GROUP_LABEL

    # ---- APs, bucketed by their group ----
    try:
        raw_aps = await r1_client.venues.get_aps_by_tenant_venue(tenant_id, venue_id)
    except Exception as e:
        logger.warning(f"Could not list APs for {venue_id}: {e}")
        raw_aps = []
        warnings.append(f"Could not list APs: {e}")
    if isinstance(raw_aps, dict):
        raw_aps = raw_aps.get("data", [])

    aps_by_group: Dict[str, List[AuditAp]] = {}
    unassigned: List[AuditAp] = []
    for ap in raw_aps or []:
        gid = ap.get("apGroupId")
        record = _ap_record(ap)
        if gid and gid in group_names:
            aps_by_group.setdefault(gid, []).append(record)
        elif gid:
            # In a group the venue listing did not return. Worth surfacing
            # rather than silently dropping the AP.
            aps_by_group.setdefault(gid, []).append(record)
            group_names.setdefault(gid, f"(unlisted group {gid[:8]})")
        else:
            unassigned.append(record)

    # ---- networks bound to this venue ----
    networks_response = await r1_client.networks.get_wifi_networks(tenant_id)
    all_networks = (
        networks_response.get("data", []) if isinstance(networks_response, dict) else []
    )

    venue_networks: List[Dict[str, Any]] = []
    for network in all_networks:
        for vag in network.get("venueApGroups") or []:
            if vag.get("venueId") == venue_id:
                venue_networks.append({"network": network, "binding": vag})
                break

    # ---- DPSK detail, only for the DPSK SSIDs actually here ----
    dpsk_ids = [
        item["network"]["id"] for item in venue_networks
        if str(item["network"].get("nwSubType") or "").lower() == "dpsk"
        and item["network"].get("id")
    ]
    links = await _dpsk_links(r1_client, tenant_id, dpsk_ids) if dpsk_ids else {}

    pool_ids = {
        (v.get("pool") or {}).get("id")
        for v in links.values() if v.get("pool")
    }
    pool_ids.discard(None)

    groups_by_pool: Dict[str, List[Dict[str, Any]]] = {}
    if pool_ids:
        try:
            ig_response = await r1_client.identity.query_identity_groups(
                tenant_id=tenant_id, page=0, size=1000
            )
            ig_rows = (
                ig_response.get("content", ig_response.get("data", []))
                if isinstance(ig_response, dict) else (ig_response or [])
            )
            for ig in ig_rows:
                pid = ig.get("dpskPoolId")
                if pid in pool_ids:
                    groups_by_pool.setdefault(pid, []).append(ig)
        except Exception as e:
            logger.warning(f"Could not list identity groups: {e}")
            warnings.append(f"Could not list identity groups: {e}")

    counts = await _identity_counts(
        r1_client, tenant_id,
        [ig["id"] for rows in groups_by_pool.values() for ig in rows if ig.get("id")],
    ) if groups_by_pool else {}

    # ---- build the SSID records ----
    ssids: List[AuditSsid] = []
    for item in venue_networks:
        network, binding = item["network"], item["binding"]
        is_dpsk = str(network.get("nwSubType") or "").lower() == "dpsk"
        venue_wide = bool(binding.get("isAllApGroups"))
        gids = [g for g in (binding.get("apGroupIds") or []) if g]

        record = AuditSsid(
            id=network.get("id") or "",
            name=network.get("name") or "",
            ssid=network.get("ssid") or "",
            security=network.get("securityProtocol"),
            vlan=network.get("vlan"),
            is_dpsk=is_dpsk,
            venue_wide=venue_wide,
            ap_group_ids=gids,
            ap_group_names=[group_names.get(g, f"(unknown {g[:8]})") for g in gids],
        )

        if is_dpsk:
            link = links.get(record.id) or {}
            if link.get("error"):
                record.issues.append(
                    "Could not read this network's DPSK service link"
                )
            pool = link.get("pool")
            if pool:
                record.dpsk_service_id = pool.get("id")
                record.dpsk_service_name = pool.get("name")
                for ig in groups_by_pool.get(pool.get("id"), []):
                    record.identity_groups.append(AuditIdentityGroup(
                        id=ig.get("id") or "",
                        name=ig.get("name") or "",
                        identity_count=counts.get(ig.get("id")),
                    ))
                if not record.identity_groups:
                    record.issues.append(
                        "DPSK service has no identity group, so it has no "
                        "passphrases to authenticate"
                    )
            elif not link.get("error"):
                record.issues.append(
                    "DPSK network with no DPSK service linked — nobody can "
                    "authenticate to it"
                )

        if not venue_wide and not gids:
            record.issues.append(
                "Activated at this venue but bound to no AP group, so it is "
                "not broadcasting anywhere"
            )

        ssids.append(record)

    # ---- group-first view, from the same records ----
    by_group_id: Dict[str, List[AuditSsid]] = {}
    for record in ssids:
        # A venue-wide network can ALSO carry every apGroupId (R1 populates
        # both), so listing it per group would repeat one binding across all
        # 249 of them and bury the per-group activations that were configured
        # deliberately. It is reported once, in venue_wide_ssids.
        if record.venue_wide:
            continue
        for gid in record.ap_group_ids:
            by_group_id.setdefault(gid, []).append(record)

    # A group with no per-group SSID is still serving the venue-wide ones, so
    # "no SSID activated" is only true when there are none of those either.
    venue_wide_count = sum(1 for s in ssids if s.venue_wide)

    ap_groups: List[AuditApGroup] = []
    for gid, name in sorted(group_names.items(), key=lambda kv: kv[1].lower()):
        group = AuditApGroup(
            id=gid,
            name=name,
            is_default=(name == DEFAULT_AP_GROUP_LABEL),
            aps=sorted(aps_by_group.get(gid, []), key=lambda a: a.name.lower()),
            ssids=by_group_id.get(gid, []),
        )
        if not group.aps and group.ssids:
            group.issues.append(
                "SSIDs are activated on this group but it contains no APs, so "
                "nothing is broadcasting them"
            )
        if group.aps and not group.ssids and not venue_wide_count:
            group.issues.append(
                "APs are in this group but no SSID is activated on it"
            )
        ap_groups.append(group)

    venue_wide = [s for s in ssids if s.venue_wide]

    totals = {
        "ap_groups": len(ap_groups),
        "aps": len(raw_aps or []),
        "aps_unassigned": len(unassigned),
        "ssids": len(ssids),
        "dpsk_ssids": sum(1 for s in ssids if s.is_dpsk),
        "venue_wide_ssids": len(venue_wide),
        "dpsk_services": len(pool_ids),
        "identity_groups": sum(len(v) for v in groups_by_pool.values()),
        "identities": sum(c for c in counts.values() if isinstance(c, int)),
        "empty_ap_groups": sum(1 for g in ap_groups if not g.aps),
        "ssids_with_issues": sum(1 for s in ssids if s.issues),
    }

    logger.info(
        f"Venue audit {venue_name}: {totals['ap_groups']} AP groups, "
        f"{totals['aps']} APs, {totals['ssids']} SSIDs "
        f"({totals['dpsk_ssids']} DPSK)"
    )

    return VenueAuditResponse(
        venue_id=venue_id,
        venue_name=venue_name,
        totals=totals,
        ap_groups=ap_groups,
        venue_wide_ssids=venue_wide,
        ssids=sorted(ssids, key=lambda s: (s.name or s.ssid).lower()),
        unassigned_aps=sorted(unassigned, key=lambda a: a.name.lower()),
        warnings=warnings,
    )
