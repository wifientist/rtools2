"""
Speed Explainer Router
Handles context options, CSV uploads, and analysis for WiFi performance diagnostics
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from typing import List, Dict, Any, Optional
import logging
import csv
import io
from datetime import datetime

from clients.r1_client import get_dynamic_r1_client
from r1api.client import R1Client

router = APIRouter()
logger = logging.getLogger(__name__)


# Supported dataset types
DATASET_TYPES = {
    'client_stats': 'Client Info and Statistics',
    'ap_airtime': 'AP Airtime and Hardware',
    'ap_afc': 'AP AFC (6GHz)',
    'ap_stats': 'AP Info and Statistics'
}


@router.post("/analyze/upload-csv")
async def upload_analytics_csv(
    dataset_type: str = Form(...),
    scope_type: str = Form(...),  # "client", "ap", "ssid"
    scope_id: str = Form(...),
    file: UploadFile = File(...),
    r1_client: R1Client = Depends(get_dynamic_r1_client)
):
    """
    Upload CSV analytics data from RuckusONE Data Studio

    Args:
        dataset_type: Type of CSV data (client_stats, ap_airtime, ap_afc, ap_stats)
        scope_type: Analysis scope (client, ap, ssid)
        scope_id: ID of the client/AP/SSID being analyzed
        file: CSV file upload

    Returns:
        Parsed and structured data ready for visualization
    """
    try:
        logger.info(f"CSV upload - dataset_type: {dataset_type}, scope: {scope_type}/{scope_id}")

        # Validate dataset type
        if dataset_type not in DATASET_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid dataset_type. Must be one of: {', '.join(DATASET_TYPES.keys())}"
            )

        # Validate file type
        if not file.filename.endswith('.csv'):
            raise HTTPException(
                status_code=400,
                detail="File must be a CSV file"
            )

        # Read CSV content
        content = await file.read()
        csv_text = content.decode('utf-8')

        # Parse CSV based on dataset type
        parsed_data = parse_csv_by_type(csv_text, dataset_type, scope_type, scope_id)

        logger.info(f"Successfully parsed {dataset_type} CSV with {len(parsed_data.get('timeSeries', {}).get('timestamps', []))} data points")

        return {
            'success': True,
            'datasetType': dataset_type,
            'datasetName': DATASET_TYPES[dataset_type],
            'scopeType': scope_type,
            'scopeId': scope_id,
            'data': parsed_data
        }

    except Exception as e:
        logger.error(f"Error processing CSV upload: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process CSV: {str(e)}"
        )


def parse_csv_by_type(
    csv_text: str,
    dataset_type: str,
    scope_type: str,
    scope_id: str
) -> Dict[str, Any]:
    """
    Parse CSV based on dataset type and return structured data
    """
    csv_reader = csv.DictReader(io.StringIO(csv_text))
    rows = list(csv_reader)

    if not rows:
        raise ValueError("CSV file is empty")

    # Route to appropriate parser
    if dataset_type == 'client_stats':
        return parse_client_stats_csv(rows, scope_id)
    elif dataset_type == 'ap_airtime':
        return parse_ap_airtime_csv(rows, scope_id)
    elif dataset_type == 'ap_afc':
        return parse_ap_afc_csv(rows, scope_id)
    elif dataset_type == 'ap_stats':
        return parse_ap_stats_csv(rows, scope_id)
    else:
        raise ValueError(f"Unknown dataset type: {dataset_type}")


def parse_client_stats_csv(rows: List[Dict[str, str]], client_mac: str) -> Dict[str, Any]:
    """
    Parse Client Info and Statistics CSV
    Expected columns: timestamp, clientMac, rssi, snr, noise, mcs, txRate, rxRate, etc.
    """
    # Filter rows for specific client if MAC is provided
    if client_mac and client_mac != 'all':
        rows = [r for r in rows if r.get('clientMac') == client_mac or r.get('Client MAC') == client_mac]

    timestamps = []
    rssi_values = []
    snr_values = []
    noise_values = []
    mcs_values = []
    tx_rates = []
    rx_rates = []

    # Track MCS distribution
    mcs_distribution = {}

    for row in rows:
        # Try different possible column names (Data Studio might vary)
        timestamp = row.get('timestamp') or row.get('Timestamp') or row.get('Time')
        rssi = row.get('rssi') or row.get('RSSI') or row.get('Signal Strength')
        snr = row.get('snr') or row.get('SNR')
        noise = row.get('noise') or row.get('Noise') or row.get('Noise Floor')
        mcs = row.get('mcs') or row.get('MCS')
        tx_rate = row.get('txRate') or row.get('TX Rate') or row.get('Transmit Rate')
        rx_rate = row.get('rxRate') or row.get('RX Rate') or row.get('Receive Rate')

        if timestamp:
            timestamps.append(timestamp)

        if rssi:
            try:
                rssi_values.append(float(rssi))
            except (ValueError, TypeError):
                rssi_values.append(None)

        if snr:
            try:
                snr_values.append(float(snr))
            except (ValueError, TypeError):
                snr_values.append(None)

        if noise:
            try:
                noise_values.append(float(noise))
            except (ValueError, TypeError):
                noise_values.append(None)

        if mcs:
            try:
                mcs_val = int(float(mcs))
                mcs_values.append(mcs_val)
                mcs_distribution[str(mcs_val)] = mcs_distribution.get(str(mcs_val), 0) + 1
            except (ValueError, TypeError):
                mcs_values.append(None)

        if tx_rate:
            try:
                tx_rates.append(float(tx_rate))
            except (ValueError, TypeError):
                tx_rates.append(None)

        if rx_rate:
            try:
                rx_rates.append(float(rx_rate))
            except (ValueError, TypeError):
                rx_rates.append(None)

    # Calculate aggregates
    valid_rssi = [v for v in rssi_values if v is not None]
    valid_snr = [v for v in snr_values if v is not None]
    valid_tx = [v for v in tx_rates if v is not None]
    valid_rx = [v for v in rx_rates if v is not None]

    return {
        'timeSeries': {
            'timestamps': timestamps,
            'rssi': rssi_values,
            'snr': snr_values,
            'noise': noise_values,
            'mcs': mcs_values,
            'txRate': tx_rates,
            'rxRate': rx_rates
        },
        'distributions': {
            'mcs': mcs_distribution
        },
        'aggregates': {
            'avgRssi': sum(valid_rssi) / len(valid_rssi) if valid_rssi else None,
            'minRssi': min(valid_rssi) if valid_rssi else None,
            'maxRssi': max(valid_rssi) if valid_rssi else None,
            'avgSnr': sum(valid_snr) / len(valid_snr) if valid_snr else None,
            'avgTxRate': sum(valid_tx) / len(valid_tx) if valid_tx else None,
            'avgRxRate': sum(valid_rx) / len(valid_rx) if valid_rx else None,
            'dataPointCount': len(rows)
        }
    }


def parse_ap_airtime_csv(rows: List[Dict[str, str]], ap_mac: str) -> Dict[str, Any]:
    """
    Parse AP Airtime and Hardware CSV
    Expected columns: timestamp, apMac, airtimeBusy, airtimeIdle, airtimeRx, airtimeTx,
                     traffic, mgmtTraffic, avgTxRate, etc.
    """
    # Filter rows for specific AP if MAC is provided
    if ap_mac and ap_mac != 'all':
        rows = [r for r in rows if r.get('apMac') == ap_mac or r.get('AP MAC') == ap_mac]

    timestamps = []
    airtime_busy = []
    airtime_idle = []
    airtime_rx = []
    airtime_tx = []
    traffic = []
    mgmt_traffic = []
    avg_tx_rates = []

    for row in rows:
        timestamp = row.get('timestamp') or row.get('Timestamp') or row.get('Time')

        if timestamp:
            timestamps.append(timestamp)

        # Parse airtime metrics
        for key, arr in [
            ('airtimeBusy', airtime_busy),
            ('Airtime Busy', airtime_busy),
            ('airtimeIdle', airtime_idle),
            ('Airtime Idle', airtime_idle),
            ('airtimeRx', airtime_rx),
            ('Airtime RX', airtime_rx),
            ('airtimeTx', airtime_tx),
            ('Airtime TX', airtime_tx),
            ('traffic', traffic),
            ('Traffic', traffic),
            ('mgmtTraffic', mgmt_traffic),
            ('Management Traffic', mgmt_traffic),
            ('avgTxRate', avg_tx_rates),
            ('Average TX Rate', avg_tx_rates)
        ]:
            if key in row:
                try:
                    arr.append(float(row[key]))
                except (ValueError, TypeError):
                    arr.append(None)
                break

    return {
        'timeSeries': {
            'timestamps': timestamps,
            'airtimeBusy': airtime_busy,
            'airtimeIdle': airtime_idle,
            'airtimeRx': airtime_rx,
            'airtimeTx': airtime_tx,
            'traffic': traffic,
            'mgmtTraffic': mgmt_traffic,
            'avgTxRate': avg_tx_rates
        },
        'aggregates': {
            'avgAirtimeBusy': sum([v for v in airtime_busy if v is not None]) / len([v for v in airtime_busy if v is not None]) if airtime_busy else None,
            'dataPointCount': len(rows)
        }
    }


def parse_ap_afc_csv(rows: List[Dict[str, str]], ap_mac: str) -> Dict[str, Any]:
    """
    Parse AP AFC (6GHz) CSV
    Expected columns specific to AFC and 6GHz operations
    """
    # Filter rows for specific AP if MAC is provided
    if ap_mac and ap_mac != 'all':
        rows = [r for r in rows if r.get('apMac') == ap_mac or r.get('AP MAC') == ap_mac]

    # TODO: Add specific AFC parsing when we see the actual CSV format
    timestamps = []

    for row in rows:
        timestamp = row.get('timestamp') or row.get('Timestamp') or row.get('Time')
        if timestamp:
            timestamps.append(timestamp)

    return {
        'timeSeries': {
            'timestamps': timestamps,
            'rawData': rows  # Include raw data until we know the exact format
        },
        'aggregates': {
            'dataPointCount': len(rows)
        }
    }


def parse_ap_stats_csv(rows: List[Dict[str, str]], ap_mac: str) -> Dict[str, Any]:
    """
    Parse AP Info and Statistics CSV
    Similar to client stats but from AP perspective
    """
    # Filter rows for specific AP if MAC is provided
    if ap_mac and ap_mac != 'all':
        rows = [r for r in rows if r.get('apMac') == ap_mac or r.get('AP MAC') == ap_mac]

    timestamps = []
    client_counts = []
    channel_util = []

    for row in rows:
        timestamp = row.get('timestamp') or row.get('Timestamp') or row.get('Time')
        if timestamp:
            timestamps.append(timestamp)

        client_count = row.get('clientCount') or row.get('Client Count') or row.get('Clients')
        if client_count:
            try:
                client_counts.append(int(float(client_count)))
            except (ValueError, TypeError):
                client_counts.append(None)

        util = row.get('channelUtilization') or row.get('Channel Utilization') or row.get('Utilization')
        if util:
            try:
                channel_util.append(float(util))
            except (ValueError, TypeError):
                channel_util.append(None)

    return {
        'timeSeries': {
            'timestamps': timestamps,
            'clientCount': client_counts,
            'channelUtilization': channel_util
        },
        'aggregates': {
            'avgClientCount': sum([v for v in client_counts if v is not None]) / len([v for v in client_counts if v is not None]) if client_counts else None,
            'avgChannelUtil': sum([v for v in channel_util if v is not None]) / len([v for v in channel_util if v is not None]) if channel_util else None,
            'dataPointCount': len(rows)
        }
    }


@router.get("/analyze/dataset-types")
async def get_dataset_types():
    """
    Get list of supported CSV dataset types
    """
    return {
        'datasetTypes': [
            {'id': key, 'name': name}
            for key, name in DATASET_TYPES.items()
        ]
    }


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
