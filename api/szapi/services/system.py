"""
SmartZone System/Cluster Service

Handles system and cluster information operations for SmartZone.
"""

from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class SystemService:
    def __init__(self, client):
        self.client = client  # back-reference to main SZClient

    async def get_api_info(self) -> Dict[str, Any]:
        """
        Get API version information from the controller.

        Calls GET /wsg/api/public/apiInfo (no version prefix) to discover
        which API versions the controller supports.

        Returns:
            API info object (typically contains supported API versions)
        """
        endpoint = "/apiInfo"
        try:
            result = await self.client._request("GET", endpoint)
            logger.info(f"Retrieved apiInfo: {result}")
            return result
        except Exception as e:
            logger.warning(f"Could not fetch /apiInfo: {e}")
            return {}

    async def get_cluster_info(self) -> Dict[str, Any]:
        """
        Get cluster information including management IP and cluster state

        Returns:
            Cluster info object containing:
            - clusterState: Cluster operational state
            - managementIp: Management IP address
            - nodes: List of cluster nodes
        """
        endpoint = f"/{self.client.api_version}/cluster"
        result = await self.client._request("GET", endpoint)

        logger.info(f"Retrieved cluster info: state={result.get('clusterState')}")
        return result

    async def get_controller_info(self) -> Dict[str, Any]:
        """
        Get controller information including firmware version

        Returns:
            Controller info object containing:
            - version: Firmware version
            - hostName: Controller hostname
            - model: Controller model
        """
        endpoint = f"/{self.client.api_version}/controller"
        result = await self.client._request("GET", endpoint)

        logger.info(f"Retrieved controller info: version={result.get('version')}")
        return result

    async def get_system_summary(self) -> Dict[str, Any]:
        """
        Get system summary information

        Returns:
            System summary object
        """
        endpoint = f"/{self.client.api_version}/system/systemSummary"

        try:
            result = await self.client._request("GET", endpoint)
            return result
        except Exception as e:
            logger.warning(f"Could not fetch system summary: {e}")
            return {}

    async def get_cluster_state(self) -> Dict[str, Any]:
        """
        Get detailed cluster state information

        Returns:
            Cluster state object including node health and status
        """
        endpoint = f"/{self.client.api_version}/cluster/state"

        try:
            result = await self.client._request("GET", endpoint)
            return result
        except Exception as e:
            logger.warning(f"Could not fetch cluster state: {e}")
            return {}

    async def get_management_ip(self) -> Optional[str]:
        """
        Get the management/external IP of the cluster

        Returns:
            Management IP address string or None if not available
        """
        try:
            cluster_info = await self.get_cluster_info()
            return cluster_info.get("managementIp")
        except Exception as e:
            logger.warning(f"Could not fetch management IP: {e}")
            return None

    async def get_firmware_version(self) -> Optional[str]:
        """
        Get the controller firmware version

        Returns:
            Firmware version string or None if not available
        """
        try:
            controller_info = await self.get_controller_info()
            return controller_info.get("version")
        except Exception as e:
            logger.warning(f"Could not fetch firmware version: {e}")
            return None

    async def get_audit_info(self) -> Dict[str, Any]:
        """
        Get combined system info useful for auditing

        Returns:
            Dict containing:
            - cluster_ip: Management IP
            - firmware_version: Controller firmware
            - cluster_state: Cluster operational state
            - hostname: Controller hostname
        """
        audit_info = {
            "cluster_ip": None,
            "firmware_version": None,
            "cluster_state": None,
            "hostname": None
        }

        try:
            cluster_info = await self.get_cluster_info()
            audit_info["cluster_ip"] = cluster_info.get("managementIp")
            audit_info["cluster_state"] = cluster_info.get("clusterState")
        except Exception as e:
            logger.warning(f"Could not fetch cluster info for audit: {e}")

        try:
            controller_info = await self.get_controller_info()
            audit_info["firmware_version"] = controller_info.get("version")
            audit_info["hostname"] = controller_info.get("hostName")
        except Exception as e:
            logger.warning(f"Could not fetch controller info for audit: {e}")

        return audit_info

    async def get_licenses_summary(self) -> Dict[str, Any]:
        """
        Get used/total counts per license type

        Returns:
            Dict with 'list' of {licenseTypeDescription, capacityControlLicenseCount}
        """
        endpoint = f"/{self.client.api_version}/licensesSummary"
        return await self.client._request("GET", endpoint)

    async def get_licenses(self) -> Dict[str, Any]:
        """
        Get the licenses assigned to the controller

        Returns:
            Dict with 'list' of {name, description, count, createTime, expireDate}
        """
        endpoint = f"/{self.client.api_version}/licenses"
        return await self.client._request("GET", endpoint)

    async def get_devices_summary(self) -> Dict[str, Any]:
        """
        Get cluster-wide device totals and capacity

        Returns:
            Devices summary object including totalAps and totalSwitches
        """
        endpoint = f"/{self.client.api_version}/system/devicesSummary"
        return await self.client._request("GET", endpoint)

    async def get_connected_ap_count(self) -> Dict[str, int]:
        """
        Sum connected APs across zones from the system inventory.

        Inventory order shifts between calls, so pages can repeat or skip a
        zone. Zones are deduped by ID, and zones_read vs zones_total shows
        whether any were missed.

        Returns:
            Dict with connected, zones_with_connected, zones_read, zones_total
        """
        endpoint = f"/{self.client.api_version}/system/inventory"
        zones: Dict[str, int] = {}
        index = 0  # index is an offset, not a page number
        zones_total = 0

        while True:
            result = await self.client._request(
                "GET", endpoint, params={"index": index, "listSize": 1000}
            )
            page = result.get("list", [])
            zones_total = result.get("totalCount", zones_total)
            for zone in page:
                zones[zone.get("zoneId")] = zone.get("connectedAPs") or 0

            index += len(page)
            if not page or not result.get("hasMore"):
                break

        return {
            "connected": sum(zones.values()),
            "zones_with_connected": sum(1 for count in zones.values() if count > 0),
            "zones_read": len(zones),
            "zones_total": zones_total,
        }
