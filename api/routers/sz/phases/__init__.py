"""
SmartZone Audit Phase Executors

Each phase handles a specific part of the audit workflow:
- initialize: Get system info and domains
- fetch_switches: Get all switches and switch groups
- audit_zones: Audit each zone (APs, WLANs, etc.)
- finalize: Aggregate stats and store results
"""

from . import initialize
from . import fetch_switches
from . import audit_zones
from . import finalize

__all__ = ['initialize', 'fetch_switches', 'audit_zones', 'finalize']
