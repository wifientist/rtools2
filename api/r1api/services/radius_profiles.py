"""
RuckusONE RADIUS Server Profile Service

CRUD operations for RADIUS server profiles used by AAA (Enterprise 802.1X) networks.

R1 API endpoints:
  POST   /radiusServerProfiles                               — create (202 async)
  GET    /radiusServerProfiles/{radiusId}                     — get by ID
  POST   /radiusServerProfiles/query                         — query/list
  PUT    /radiusServerProfiles/{radiusId}                     — update (202 async)
  DELETE /radiusServerProfiles/{radiusId}                     — delete (202 async)
  PUT    /wifiNetworks/{networkId}/radiusServerProfiles/{radiusId}    — link to network (202 async)
  DELETE /wifiNetworks/{networkId}/radiusServerProfiles/{radiusId}    — unlink from network (202 async)
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List

from r1api.constants import R1StatusCode

logger = logging.getLogger(__name__)


class RadiusProfileService:
    def __init__(self, client):
        self.client = client

    def get_radius_profiles(
        self,
        tenant_id: str = None,
    ) -> List[Dict[str, Any]]:
        """
        Get all RADIUS server profiles via GET endpoint.

        Args:
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            List of RADIUS profile dicts
        """
        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.get("/radiusServerProfiles", override_tenant_id=tenant_id)
        else:
            response = self.client.get("/radiusServerProfiles")

        data = response.json()
        # GET returns a plain list
        if isinstance(data, list):
            return data
        return data.get("data", [])

    def find_radius_profile_by_name(
        self,
        tenant_id: str,
        name: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Find a RADIUS server profile by exact name match.

        Args:
            tenant_id: Tenant/EC ID
            name: Profile name to search for

        Returns:
            Profile dict if found, None otherwise
        """
        profiles = self.get_radius_profiles(tenant_id)
        for p in profiles:
            if p.get("name") == name:
                return p
        return None

    async def create_radius_profile(
        self,
        tenant_id: str,
        name: str,
        primary_ip: str,
        primary_port: int = 1812,
        primary_secret: str = "",
        secondary_ip: str = None,
        secondary_port: int = 1812,
        secondary_secret: str = "",
        profile_type: str = "AUTHENTICATION",
        wait_for_completion: bool = True,
    ) -> Dict[str, Any]:
        """
        Create a RADIUS server profile.

        Args:
            tenant_id: Tenant/EC ID
            name: Profile name (2-32 chars)
            primary_ip: Primary RADIUS server IP
            primary_port: Primary port (default: 1812)
            primary_secret: Primary shared secret
            secondary_ip: Optional secondary server IP
            secondary_port: Secondary port (default: 1812)
            secondary_secret: Secondary shared secret
            profile_type: "AUTHENTICATION" or "ACCOUNTING"
            wait_for_completion: If True, wait for async task

        Returns:
            Created profile dict (from query after creation)
        """
        payload = {
            "name": name,
            "type": profile_type,
            "primary": {
                "ip": primary_ip,
                "port": primary_port,
                "sharedSecret": primary_secret,
                "autoFallbackDisable": True,
            },
        }

        if secondary_ip:
            payload["secondary"] = {
                "ip": secondary_ip,
                "port": secondary_port,
                "sharedSecret": secondary_secret,
                "autoFallbackDisable": True,
            }

        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.post(
                "/radiusServerProfiles",
                payload=payload,
                override_tenant_id=tenant_id,
            )
        else:
            response = self.client.post("/radiusServerProfiles", payload=payload)

        if response.status_code in [R1StatusCode.OK, R1StatusCode.CREATED, R1StatusCode.ACCEPTED]:
            result = response.json() if response.content else {"status": "accepted"}

            if response.status_code == R1StatusCode.ACCEPTED and wait_for_completion:
                request_id = result.get("requestId")
                if request_id:
                    await self.client.await_task_completion(
                        request_id, override_tenant_id=tenant_id
                    )
                    # Fetch created profile by name
                    created = self.find_radius_profile_by_name(tenant_id, name)
                    if created:
                        return created
                    logger.warning(f"Task completed but could not find created RADIUS profile '{name}'")
                    return result

            return result
        else:
            logger.error(f"Failed to create RADIUS profile: {response.status_code} - {response.text}")
            response.raise_for_status()
            return {}

    async def find_or_create_radius_profile(
        self,
        tenant_id: str,
        name: str,
        primary_ip: str,
        primary_port: int = 1812,
        primary_secret: str = "",
        secondary_ip: str = None,
        secondary_port: int = 1812,
        secondary_secret: str = "",
        profile_type: str = "AUTHENTICATION",
        wait_for_completion: bool = True,
    ) -> Dict[str, Any]:
        """
        Find an existing RADIUS profile by name, or create one.

        Idempotent — safe to call multiple times.

        Returns:
            Profile dict with 'id' field
        """
        existing = self.find_radius_profile_by_name(tenant_id, name)
        if existing:
            logger.info(f"Found existing RADIUS profile '{name}' (id={existing.get('id')})")
            return existing

        logger.info(f"Creating RADIUS profile '{name}'")
        return await self.create_radius_profile(
            tenant_id=tenant_id,
            name=name,
            primary_ip=primary_ip,
            primary_port=primary_port,
            primary_secret=primary_secret,
            secondary_ip=secondary_ip,
            secondary_port=secondary_port,
            secondary_secret=secondary_secret,
            profile_type=profile_type,
            wait_for_completion=wait_for_completion,
        )

    async def link_radius_to_network(
        self,
        network_id: str,
        radius_profile_id: str,
        tenant_id: str = None,
        wait_for_completion: bool = True,
    ) -> Dict[str, Any]:
        """
        Link a RADIUS server profile to an AAA WiFi network.

        Args:
            network_id: WiFi network ID
            radius_profile_id: RADIUS profile ID to link
            tenant_id: Tenant/EC ID (required for MSP)
            wait_for_completion: If True, wait for async task

        Returns:
            API response
        """
        endpoint = f"/wifiNetworks/{network_id}/radiusServerProfiles/{radius_profile_id}"

        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.put(endpoint, payload={}, override_tenant_id=tenant_id)
        else:
            response = self.client.put(endpoint, payload={})

        if response.status_code in [R1StatusCode.OK, R1StatusCode.ACCEPTED]:
            result = response.json() if response.content else {"status": "accepted"}

            if response.status_code == R1StatusCode.ACCEPTED and wait_for_completion:
                request_id = result.get("requestId")
                if request_id:
                    await self.client.await_task_completion(
                        request_id, override_tenant_id=tenant_id
                    )
            return result
        else:
            logger.error(f"Failed to link RADIUS to network: {response.status_code} - {response.text}")
            response.raise_for_status()
            return {}

    async def unlink_radius_from_network(
        self,
        network_id: str,
        radius_profile_id: str,
        tenant_id: str = None,
        wait_for_completion: bool = True,
    ) -> Dict[str, Any]:
        """
        Unlink a RADIUS server profile from an AAA WiFi network.

        Args:
            network_id: WiFi network ID
            radius_profile_id: RADIUS profile ID to unlink
            tenant_id: Tenant/EC ID (required for MSP)
            wait_for_completion: If True, wait for async task

        Returns:
            API response
        """
        endpoint = f"/wifiNetworks/{network_id}/radiusServerProfiles/{radius_profile_id}"

        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.delete(endpoint, override_tenant_id=tenant_id)
        else:
            response = self.client.delete(endpoint)

        if response.status_code in [R1StatusCode.OK, R1StatusCode.ACCEPTED]:
            result = response.json() if response.content else {"status": "accepted"}

            if response.status_code == R1StatusCode.ACCEPTED and wait_for_completion:
                request_id = result.get("requestId")
                if request_id:
                    await self.client.await_task_completion(
                        request_id, override_tenant_id=tenant_id
                    )
            return result
        else:
            logger.error(f"Failed to unlink RADIUS from network: {response.status_code} - {response.text}")
            response.raise_for_status()
            return {}

    # ── Read-only views for audits ───────────────────────────────────
    #
    # GET /radiusServerProfiles and GET /radiusServerProfiles/{id} both return
    # sharedSecret. Nothing below hands one back: the query endpoint's rows
    # carry primary/secondary as "ip:port" strings with no secret, and
    # shared_secret_equals() reads the profile only to answer yes/no.

    async def query_radius_profiles(
        self,
        tenant_id: str = None,
        page_size: int = 500,
    ) -> List[Dict[str, Any]]:
        """
        Every RADIUS server profile in the tenant, with the networks using it.

        POST /radiusServerProfiles/query (1-indexed, ES-backed). Each row is
        {id, name, type, primary, secondary, wifiNetworkIds}; `type` is
        AUTHENTICATION or ACCOUNTING and `primary` is an "ip:port" string.
        wifiNetworkIds is the only cheap way to answer "which profile is this
        network pointing at" -- the network itself does not say.
        """
        fields = ["id", "name", "type", "primary", "secondary", "wifiNetworkIds"]
        rows: List[Dict[str, Any]] = []
        page = 1
        while True:
            body = {"fields": fields, "page": page, "pageSize": page_size}
            if self.client.ec_type == "MSP" and tenant_id:
                response = await asyncio.to_thread(
                    self.client.post, "/radiusServerProfiles/query",
                    payload=body, override_tenant_id=tenant_id,
                )
            else:
                response = await asyncio.to_thread(
                    self.client.post, "/radiusServerProfiles/query", payload=body,
                )
            response.raise_for_status()
            data = self.client.safe_json(response) or {}
            batch = data.get("data", []) if isinstance(data, dict) else []
            # Keep only the declared fields, in case the endpoint ever starts
            # returning more than it was asked for.
            rows.extend({k: r.get(k) for k in fields} for r in batch)
            total = data.get("totalCount", len(rows)) if isinstance(data, dict) else len(rows)
            if not batch or len(rows) >= total:
                return rows
            page += 1

    async def shared_secret_equals(
        self,
        radius_profile_id: str,
        candidate: str,
        tenant_id: str = None,
    ) -> Optional[bool]:
        """
        Whether the profile's primary or secondary shared secret equals `candidate`.

        Exists to detect a known placeholder left by the migration. The secret
        is compared and dropped inside this function: it is never returned,
        logged, or put in an exception. None means the profile could not be read.
        """
        endpoint = f"/radiusServerProfiles/{radius_profile_id}"
        try:
            if self.client.ec_type == "MSP" and tenant_id:
                response = await asyncio.to_thread(
                    self.client.get, endpoint, override_tenant_id=tenant_id,
                )
            else:
                response = await asyncio.to_thread(self.client.get, endpoint)
        except Exception:
            logger.warning(f"Could not read RADIUS profile {radius_profile_id}")
            return None
        if response.status_code != R1StatusCode.OK:
            logger.warning(
                f"Could not read RADIUS profile {radius_profile_id}: HTTP {response.status_code}"
            )
            return None
        # Not safe_json: on a parse failure it logs and raises with the body,
        # and this body carries the secret.
        try:
            profile = response.json() or {}
        except ValueError:
            logger.warning(f"RADIUS profile {radius_profile_id} returned unparseable JSON")
            return None
        return any(
            isinstance(server, dict) and server.get("sharedSecret") == candidate
            for server in (profile.get("primary"), profile.get("secondary"))
        )
