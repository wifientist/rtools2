"""
Per-Unit SSID Configuration Router and Workflow
"""

from routers.per_unit_ssid.per_unit_ssid_router import router
from routers.per_unit_ssid.v2_endpoints import router as router_v2

__all__ = ['router', 'router_v2']
