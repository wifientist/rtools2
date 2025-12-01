"""
R1 API Service for Network Clients (Wireless/Wired Clients)
Handles all client-related API calls to RuckusONE

Primary endpoint: POST /venues/aps/clients/query
Historical endpoint: POST /historicalClients/query
"""

from typing import Dict, Any, Optional, List
import logging

logger = logging.getLogger(__name__)


class ClientsService:
    def __init__(self, client):
        self.client = client  # back-reference to main R1Client

    async def get_active_clients(
        self,
        tenant_id: Optional[str] = None,
        venue_id: Optional[str] = None,
        ssid: Optional[str] = None,
        ap_mac: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get currently active/connected network clients
        Uses filters to narrow down to specific venue, SSID, or AP

        Args:
            tenant_id: Tenant ID to filter by (for MSP accounts)
            venue_id: Filter by venue ID
            ssid: Filter by SSID name
            ap_mac: Filter by AP MAC address
            limit: Max number of clients to return

        Returns:
            List of active client objects
        """
        match_fields = []
        filters = {}

        # Build match fields for specific criteria
        if venue_id:
            match_fields.append({"field": "venueId", "value": venue_id})

        if ssid:
            match_fields.append({"field": "ssid", "value": ssid})

        if ap_mac:
            match_fields.append({"field": "apMac", "value": ap_mac})

        # Filter for active/connected clients only
        # NOTE: Commenting out status filter to see if it's causing issues
        # Different APIs might use different status values
        # filters["status"] = ["CONNECTED", "ONLINE"]

        fields = [
            "clientMac", "hostname", "ipAddress", "apMac", "apName",
            "ssid", "venueName", "manufacturer", "osType", "deviceType",
            "connectionType", "radio", "channel", "rssi", "snr",
            "txRate", "rxRate", "status"
        ]

        payload = {
            "fields": fields,
            "sortField": "lastSeenTime",
            "sortOrder": "DESC",
            "page": 1,
            "pageSize": limit
        }

        if match_fields:
            payload["matchFields"] = match_fields

        if filters:
            payload["filters"] = filters

        try:
            # Use override_tenant_id for MSP accounts
            if self.client.ec_type == "MSP" and tenant_id:
                response = self.client.post("/venues/aps/clients/query", payload=payload, override_tenant_id=tenant_id)
            else:
                response = self.client.post("/venues/aps/clients/query", payload=payload)

            data = response.json()
            return data.get('data', [])
        except Exception as e:
            logger.error(f"Error fetching active clients: {str(e)}")
            return []


async def query_ap_clients(
    r1_client,
    fields: Optional[List[str]] = None,
    search_string: Optional[str] = None,
    search_target_fields: Optional[List[str]] = None,
    match_fields: Optional[List[Dict[str, str]]] = None,
    filters: Optional[Dict[str, List[str]]] = None,
    sort_field: Optional[str] = None,
    sort_order: str = "ASC",
    page: int = 1,
    page_size: int = 100
) -> Dict[str, Any]:
    """
    Query AP clients from RuckusONE (PRIMARY METHOD)
    Uses POST /venues/aps/clients/query

    Args:
        r1_client: The authenticated R1 API client instance
        fields: List of fields to return
        search_string: Search string to filter clients
        search_target_fields: Fields to search within
        match_fields: List of match field objects [{"field": "fieldName", "value": "value"}]
        filters: Additional filters as key-value pairs with arrays
        sort_field: Field to sort by
        sort_order: ASC or DESC
        page: Page number
        page_size: Number of results per page

    Returns:
        Dictionary with 'data', 'totalCount', 'page', etc.
    """
    try:
        endpoint = "/venues/aps/clients/query"

        # Default fields if none provided - comprehensive list for diagnostics
        if fields is None:
            fields = [
                "clientMac",
                "hostname",
                "ipAddress",
                "apMac",
                "apName",
                "ssid",
                "venueName",
                "venueId",
                "manufacturer",
                "osType",
                "deviceType",
                "userName",
                "connectionType",  # wired/wireless
                "radio",  # 2.4GHz, 5GHz, 6GHz
                "channel",
                "channelWidth",
                "rssi",
                "snr",
                "txRate",
                "rxRate",
                "txBytes",
                "rxBytes",
                "txPackets",
                "rxPackets",
                "txRetries",
                "rxRetries",
                "status",
                "connectTime",
                "lastSeenTime",
                "sessionDuration",
                "vlan",
                "authMethod",
                "encryption"
            ]

        payload = {
            "fields": fields,
            "sortField": sort_field or "lastSeenTime",
            "sortOrder": sort_order,
            "page": page,
            "pageSize": page_size
        }

        # Add optional parameters
        if search_string:
            payload["searchString"] = search_string

        if search_target_fields:
            payload["searchTargetFields"] = search_target_fields

        if match_fields:
            payload["matchFields"] = match_fields

        if filters:
            payload["filters"] = filters

        response = await r1_client.post(endpoint, json=payload)

        if response.status_code == 200:
            data = response.json()
            logger.info(f"Retrieved {len(data.get('data', []))} AP clients")
            return data
        else:
            logger.error(f"Failed to query AP clients: {response.status_code}")
            logger.error(f"Response: {response.text}")
            return {'data': [], 'totalCount': 0, 'page': page}

    except Exception as e:
        logger.error(f"Error querying AP clients: {str(e)}")
        return {'data': [], 'totalCount': 0, 'page': page}


async def query_historical_clients(
    r1_client,
    fields: Optional[List[str]] = None,
    search_string: Optional[str] = None,
    search_target_fields: Optional[List[str]] = None,
    filters: Optional[Dict[str, Any]] = None,
    sort_field: Optional[str] = None,
    sort_order: str = "ASC",
    page: int = 1,
    page_size: int = 100,
    detail_level: Optional[str] = None
) -> Dict[str, Any]:
    """
    Query historical network clients from RuckusONE

    Args:
        r1_client: The authenticated R1 API client instance
        fields: List of fields to return
        search_string: Search string to filter clients
        search_target_fields: Fields to search within
        filters: Additional filters as key-value pairs
        sort_field: Field to sort by
        sort_order: ASC or DESC
        page: Page number
        page_size: Number of results per page
        detail_level: Level of detail in response

    Returns:
        Dictionary with 'data', 'totalCount', 'page', etc.
    """
    try:
        endpoint = "/historicalClients/query"

        # Default fields if none provided
        if fields is None:
            fields = [
                "clientMac",
                "hostname",
                "ipAddress",
                "apMac",
                "apName",
                "ssid",
                "venueName",
                "manufacturer",
                "osType",
                "connectionType",  # wired/wireless
                "radio",  # 2.4GHz, 5GHz, 6GHz
                "rssi",
                "snr",
                "channel",
                "channelWidth",
                "txRate",
                "rxRate",
                "status",
                "connectTime",
                "lastSeenTime"
            ]

        payload = {
            "fields": fields,
            "sortField": sort_field or "clientMac",
            "sortOrder": sort_order,
            "page": page,
            "pageSize": page_size
        }

        # Add optional parameters
        if search_string:
            payload["searchString"] = search_string

        if search_target_fields:
            payload["searchTargetFields"] = search_target_fields

        if filters:
            payload["filters"] = filters

        if detail_level:
            payload["detailLevel"] = detail_level

        response = await r1_client.post(endpoint, json=payload)

        if response.status_code == 200:
            data = response.json()
            logger.info(f"Retrieved {len(data.get('data', []))} historical clients")
            return data
        else:
            logger.error(f"Failed to query historical clients: {response.status_code}")
            logger.error(f"Response: {response.text}")
            return {'data': [], 'totalCount': 0, 'page': page}

    except Exception as e:
        logger.error(f"Error querying historical clients: {str(e)}")
        return {'data': [], 'totalCount': 0, 'page': page}


async def get_active_clients(
    r1_client,
    venue_id: Optional[str] = None,
    ssid: Optional[str] = None,
    ap_mac: Optional[str] = None,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """
    Get currently active/connected network clients
    Uses filters to narrow down to specific venue, SSID, or AP

    Args:
        r1_client: The authenticated R1 API client instance
        venue_id: Filter by venue ID
        ssid: Filter by SSID name
        ap_mac: Filter by AP MAC address
        limit: Max number of clients to return

    Returns:
        List of active client objects
    """
    match_fields = []
    filters = {}

    # Build match fields for specific criteria
    if venue_id:
        match_fields.append({"field": "venueId", "value": venue_id})

    if ssid:
        match_fields.append({"field": "ssid", "value": ssid})

    if ap_mac:
        match_fields.append({"field": "apMac", "value": ap_mac})

    # Filter for active/connected clients only
    # Note: Field name might be "status", "state", or "connectionState"
    # May need to adjust based on actual API response
    filters["status"] = ["CONNECTED", "ONLINE"]  # Try both common values

    result = await query_ap_clients(
        r1_client,
        match_fields=match_fields if match_fields else None,
        filters=filters,
        page_size=limit,
        sort_field="lastSeenTime",
        sort_order="DESC"
    )

    return result.get('data', [])


async def get_client_by_mac(
    r1_client,
    client_mac: str
) -> Optional[Dict[str, Any]]:
    """
    Get a specific network client by MAC address

    Args:
        r1_client: The authenticated R1 API client instance
        client_mac: MAC address of the client

    Returns:
        Client object or None if not found
    """
    result = await query_historical_clients(
        r1_client,
        search_string=client_mac,
        search_target_fields=["clientMac"],
        page_size=1
    )

    clients = result.get('data', [])
    return clients[0] if clients else None


async def get_clients_by_ssid(
    r1_client,
    ssid: str,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """
    Get all clients connected to a specific SSID

    Args:
        r1_client: The authenticated R1 API client instance
        ssid: SSID name
        limit: Max number of clients to return

    Returns:
        List of clients connected to the SSID
    """
    return await get_active_clients(
        r1_client,
        ssid=ssid,
        limit=limit
    )


async def get_clients_by_ap(
    r1_client,
    ap_mac: str,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """
    Get all clients connected to a specific AP

    Args:
        r1_client: The authenticated R1 API client instance
        ap_mac: AP MAC address
        limit: Max number of clients to return

    Returns:
        List of clients connected to the AP
    """
    return await get_active_clients(
        r1_client,
        ap_mac=ap_mac,
        limit=limit
    )
