"""
Speed Explainer Context Options Router
Provides lists of clients, APs, and SSIDs for the context selector
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
import logging

from clients.r1_client import get_dynamic_r1_client
from r1api.client import R1Client

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/speed-context")
async def get_speed_explainer_context(
    tenant_id: str = None,
    venue_id: str = None,
    r1_client: R1Client = Depends(get_dynamic_r1_client)
):
    """
    Get all context options for Speed Explainer:
    - List of clients
    - List of APs
    - List of SSIDs/WLANs

    Query params:
    - tenant_id: Optional tenant ID to filter by (for MSP accounts)
    - venue_id: Optional venue ID to filter by
    """
    try:
        # Use provided tenant_id or fall back to client's tenant_id
        effective_tenant_id = tenant_id or r1_client.tenant_id

        logger.info(f"Speed context request - tenant_id: {tenant_id}, venue_id: {venue_id}, effective: {effective_tenant_id}")

        # Fetch APs using the tenant service (returns full response with 'data' key)
        aps_response = await r1_client.tenant.get_tenant_aps(effective_tenant_id)
        aps_list = aps_response.get('data', []) if isinstance(aps_response, dict) else []
        logger.info(f"Fetched {len(aps_list)} total APs")

        # Filter APs by venue if venue_id is provided
        if venue_id:
            aps_list = [ap for ap in aps_list if ap.get('venueId') == venue_id]
            logger.info(f"Filtered to {len(aps_list)} APs for venue {venue_id}")

        # Fetch WLANs/SSIDs (returns full response with 'data' key)
        networks_response = await r1_client.networks.get_wifi_networks(effective_tenant_id)
        networks_list = networks_response.get('data', []) if isinstance(networks_response, dict) else []
        logger.info(f"Fetched {len(networks_list)} networks/SSIDs")

        # Fetch active clients using the clients service (already returns list)
        try:
            clients_list = await r1_client.clients.get_active_clients(
                tenant_id=effective_tenant_id,
                venue_id=venue_id,
                limit=200
            )
            logger.info(f"Fetched {len(clients_list)} active clients")
        except Exception as e:
            logger.warning(f"Failed to fetch clients: {str(e)}")
            clients_list = []

        # Format APs for dropdown
        aps = [
            {
                'id': ap.get('id', ap.get('macAddress', ap.get('mac', ''))),
                'name': ap.get('name', 'Unknown AP'),
                'mac': ap.get('macAddress', ap.get('mac', '')),
                'model': ap.get('model', ''),
                'serialNumber': ap.get('serialNumber', ''),
                'status': ap.get('status', 'unknown'),
                'venueName': ap.get('venueName', '')
            }
            for ap in aps_list
            if ap.get('macAddress') or ap.get('mac')  # Only include APs with MAC addresses
        ]

        # Format SSIDs for dropdown
        ssids = [
            {
                'id': network.get('id', network.get('ssid', '')),
                'name': network.get('name', network.get('ssid', 'Unknown Network')),
                'ssid': network.get('ssid', ''),
                'vlan': network.get('vlan'),
                'nwSubType': network.get('nwSubType', 'unknown'),
                'description': network.get('description', '')
            }
            for network in networks_list
            if network.get('ssid')  # Only include networks with SSIDs
        ]

        # Format clients for dropdown
        clients = [
            {
                # Use clientMac as primary ID, or create unique ID from hostname+IP+index
                'id': client.get('clientMac') or f"{client.get('hostname', 'unknown')}_{client.get('ipAddress', '')}_{idx}",
                'name': client.get('hostname') or client.get('clientMac', 'Unknown Client'),
                'mac': client.get('clientMac', ''),
                'ipAddress': client.get('ipAddress', ''),
                'apName': client.get('apName', ''),
                'ssid': client.get('ssid', ''),
                'manufacturer': client.get('manufacturer', ''),
                'osType': client.get('osType', ''),
                'connectionType': client.get('connectionType', 'wireless'),
                'status': client.get('status', 'unknown')
            }
            for idx, client in enumerate(clients_list)
            # Include clients that have either clientMac OR hostname (some might use hostname as ID)
            if client.get('clientMac') or client.get('hostname')
        ]

        return {
            'clients': clients,
            'aps': aps,
            'ssids': ssids,
            'meta': {
                'clientsCount': len(clients),
                'apsCount': len(aps),
                'ssidsCount': len(ssids)
            }
        }

    except Exception as e:
        logger.error(f"Error fetching speed context: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch speed explainer context: {str(e)}"
        )
