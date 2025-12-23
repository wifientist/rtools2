"""
Data Mapping Utilities

Maps Cloudpath data structures to RuckusONE API format
"""

from typing import Dict, Any


def map_identity_group(cloudpath_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map Cloudpath identity group to R1 format

    Args:
        cloudpath_data: Cloudpath identity group data

    Returns:
        R1 API format dict
    """
    return {
        'name': cloudpath_data.get('name'),
        'description': cloudpath_data.get('description', 'Migrated from Cloudpath')
    }


def map_dpsk_pool(cloudpath_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map Cloudpath DPSK pool to R1 format

    Args:
        cloudpath_data: Cloudpath DPSK pool data

    Returns:
        R1 API format dict
    """
    return {
        'name': cloudpath_data.get('name'),
        'description': cloudpath_data.get('description', 'Migrated from Cloudpath'),
        'passphraseFormat': cloudpath_data.get('passphraseFormat', 'RANDOM'),
        'passphraseLength': cloudpath_data.get('passphraseLength', 12),
        'expirationInDays': cloudpath_data.get('expirationInDays', 0),
        'maxDevicesPerPassphrase': cloudpath_data.get('maxDevicesPerPassphrase', 1)
    }


def map_passphrase(cloudpath_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map Cloudpath passphrase to R1 format

    Args:
        cloudpath_data: Cloudpath passphrase data

    Returns:
        R1 API format dict
    """
    return {
        'userName': cloudpath_data.get('userName') or cloudpath_data.get('username'),
        'passphrase': cloudpath_data.get('passphrase') or cloudpath_data.get('password'),
        'expirationDate': cloudpath_data.get('expirationDate'),
        'maxDevices': cloudpath_data.get('maxDevices', 1)
    }
