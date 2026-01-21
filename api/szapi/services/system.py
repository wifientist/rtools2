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
