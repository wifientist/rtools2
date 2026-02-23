"""
Bulk WLAN Edit Router

Bulk tool for editing WiFi network advanced settings across a tenant.
Supports modifying Client Isolation, Application Visibility, BSS Min Rate,
OFDM Only, Join RSSI Threshold, DTIM Interval, and QoS Mirroring.
"""

from routers.bulk_wlan.bulk_wlan_router import router

__all__ = ['router']
