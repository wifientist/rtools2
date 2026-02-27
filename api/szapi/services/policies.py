"""
SmartZone Policies & Profiles Reference Chaser

Generic service to fetch any referenced object by type and ID.
Used by the migration extractor to chase WLAN foreign key references
that aren't covered by the AAA service.

SZ API paths (from sz_openapi_v13_1.json):
  Reference Type              → SZ API Endpoint
  ─────────────────────────────────────────────────────────
  device_policy               → GET /rkszones/{zoneId}/devicePolicy/{id}
  l2_acl                      → GET /rkszones/{zoneId}/l2ACL/{id}
  firewall_profile            → GET /firewallProfiles/{id}
  firewall_l2_policy          → GET /rkszones/{zoneId}/l2ACL/{id}
  firewall_l3_policy          → GET /l3AccessControlPolicies/{id}
  firewall_app_policy         → (no direct GET — log warning)
  firewall_device_policy      → GET /devicePolicy/{id}
  firewall_url_filtering_policy → GET /urlFiltering/urlFilteringPolicy/{id}
  user_traffic_profile        → GET /profiles/utp/{id}
  diff_serv_profile           → GET /rkszones/{zoneId}/diffserv/{id}
  ipsec_profile               → GET /profiles/tunnel/ipsec/{id}
  tunnel_profile              → GET /profiles/tunnel/softgre/{id}
  split_tunnel_profile        → GET /rkszones/{zoneId}/splitTunnelProfiles/{id}
  portal_service_profile      → GET /rkszones/{zoneId}/portals/guest/{id}
  hotspot20_profile           → GET /rkszones/{zoneId}/hs20s/{id}
  vlan_pooling                → GET /vlanpoolings/{id}
  dns_server_profile          → GET /profiles/dnsserver/{id}
  precedence_profile          → GET /precedence/{id}
"""

from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


# Mapping from reference type to SZ API path template.
# {zoneId} and {id} are substituted at runtime.
REFERENCE_TYPE_PATHS: Dict[str, str] = {
    "device_policy": "/rkszones/{zoneId}/devicePolicy/{id}",
    "l2_acl": "/rkszones/{zoneId}/l2ACL/{id}",
    "firewall_profile": "/firewallProfiles/{id}",
    "firewall_l2_policy": "/rkszones/{zoneId}/l2ACL/{id}",
    "firewall_l3_policy": "/l3AccessControlPolicies/{id}",
    "firewall_device_policy": "/devicePolicy/{id}",
    "firewall_url_filtering_policy": "/urlFiltering/urlFilteringPolicy/{id}",
    "user_traffic_profile": "/profiles/utp/{id}",
    "diff_serv_profile": "/rkszones/{zoneId}/diffserv/{id}",
    "ipsec_profile": "/profiles/tunnel/ipsec/{id}",
    "tunnel_profile": "/profiles/tunnel/softgre/{id}",
    "split_tunnel_profile": "/rkszones/{zoneId}/splitTunnelProfiles/{id}",
    "portal_service_profile": "/rkszones/{zoneId}/portals/guest/{id}",
    "hotspot20_profile": "/rkszones/{zoneId}/hs20s/{id}",
    "vlan_pooling": "/vlanpoolings/{id}",
    "dns_server_profile": "/profiles/dnsserver/{id}",
    "precedence_profile": "/precedence/{id}",
}


class PoliciesService:
    def __init__(self, client):
        self.client = client

    async def get_referenced_object(
        self,
        zone_id: str,
        ref_type: str,
        ref_id: str
    ) -> Dict[str, Any]:
        """
        Fetch any referenced object by type and ID.

        Dispatches to the correct SZ API path based on the reference type.
        Unknown or unsupported types return a stub with an _error field.

        Args:
            zone_id: Zone UUID (needed for zone-scoped endpoints)
            ref_type: Reference type key (e.g., 'device_policy', 'l2_acl')
            ref_id: Object UUID

        Returns:
            Object detail dict, or stub with _error if fetch fails
        """
        path_template = REFERENCE_TYPE_PATHS.get(ref_type)

        if not path_template:
            logger.warning(f"Unknown reference type '{ref_type}' for ID {ref_id} — skipping")
            return {
                "id": ref_id,
                "ref_type": ref_type,
                "_error": f"No API path mapped for reference type '{ref_type}'"
            }

        # Build the endpoint path
        path = path_template.replace("{zoneId}", zone_id).replace("{id}", ref_id)
        endpoint = f"/{self.client.api_version}{path}"

        try:
            result = await self.client._request("GET", endpoint)
            logger.debug(f"Fetched {ref_type} {ref_id}: {result.get('name', 'unnamed')}")
            return result
        except Exception as e:
            logger.warning(f"Failed to fetch {ref_type} {ref_id} from {endpoint}: {e}")
            return {
                "id": ref_id,
                "ref_type": ref_type,
                "_error": str(e)
            }
