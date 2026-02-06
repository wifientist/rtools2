"""
AP Port Configuration Router

Standalone tool for configuring AP LAN ports without the Per-Unit SSID workflow.
"""

from routers.ap_port_config.ap_port_config_router import router
from routers.ap_port_config.v2_endpoints import router as router_v2

__all__ = ['router', 'router_v2']
