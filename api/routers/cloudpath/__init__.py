"""Cloudpath DPSK Migration Workflow"""

from routers.cloudpath.cloudpath_router import router
from routers.cloudpath.v2_endpoints import router as router_v2

__all__ = ['router', 'router_v2']
