# SZ Audit Tool Implementation Plan

## Overview
Create a new informational tool called "SZ Audit" that allows users to select one or more SmartZone controllers and view a comprehensive audit summary.

**API Version Focus**: SmartZone API v11_1 only

---

## Audit Data Points

### Per Controller
- Controller name, host, cluster IP
- Controller firmware version
- Total counts (APs, WLANs, switches, etc.)

### Domains (Hierarchical)
- All administrative domains (nested structure supported)
- Domain hierarchy visualization
- Per-domain statistics

### Per Zone
- Zone name and ID
- **APs**: Total count, status breakdown (online/offline/flagged)
- **AP Models**: Count per model (e.g., R750: 45, R650: 30, T750: 12)
- **AP Groups**: Count and list
- **WLANs**: Count
- **WLAN Groups**: Count and list
- **WLAN Types**: Breakdown by auth type (Open, WPA2-PSK, WPA2-Enterprise, DPSK, etc.)
- **External IP**: Zone's external/public IP

### Switching
- **Switch Groups**: Count and list per domain
- **Switches**: Count, status, firmware per switch group
- Switch firmware versions

### Firmware Summary
- AP firmware distribution (version -> count)
- Switch firmware distribution
- Controller version

---

## Phase 1: Backend - New SZ API Services

### 1.1 Create WLAN Service (`api/szapi/services/wlans.py`)

**SmartZone v11 API Endpoints:**
- `GET /v11_1/rkszones/{zoneId}/wlans` - Get WLANs in a zone
- `GET /v11_1/rkszones/{zoneId}/wlangroups` - Get WLAN Groups in a zone

**Service methods:**
```python
class WlanService:
    async def get_wlans_by_zone(zone_id: str) -> List[Dict]
    async def get_wlan_groups_by_zone(zone_id: str) -> List[Dict]
```

### 1.2 Create AP Groups Service (`api/szapi/services/apgroups.py`)

**SmartZone v11 API Endpoints:**
- `GET /v11_1/rkszones/{zoneId}/apgroups` - Get AP Groups in a zone

**Service methods:**
```python
class ApGroupService:
    async def get_ap_groups_by_zone(zone_id: str) -> List[Dict]
```

### 1.3 Create System Info Service (`api/szapi/services/system.py`)

**SmartZone v11 API Endpoints:**
- `GET /v11_1/cluster` - Cluster info (management IP, version)
- `GET /v11_1/controller` - Controller info and firmware

**Service methods:**
```python
class SystemService:
    async def get_cluster_info() -> Dict
    async def get_controller_info() -> Dict
```

### 1.4 Enhance Zones Service (`api/szapi/services/zones.py`)

**Additional endpoints needed:**
- Zone external IP is typically in zone details

**Add method:**
```python
async def get_zone_details(zone_id: str) -> Dict  # Includes external IP config
```

### 1.5 Enhance Existing Services

**APs Service** - already has `get_aps_by_zone()`, need to extract:
- Status (connectionStatus: "Connect", "Disconnect", etc.)
- Firmware version (firmwareVersion field)
- Model (model field - e.g., "R750", "R650", "T750")

**Switches Service** - already exists, need:
- Status and firmware per switch

### 1.6 Update SZ Client (`api/szapi/client.py`)
```python
self.wlans = WlanService(self)
self.apgroups = ApGroupService(self)
self.system = SystemService(self)
```

---

## Phase 2: Backend - Audit Router

### 2.1 Audit Response Schemas (`api/schemas/sz_audit.py`)

```python
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime

class WlanSummary(BaseModel):
    id: str
    name: str
    ssid: str
    auth_type: str  # "Open", "WPA2-PSK", "WPA2-Enterprise", "DPSK", etc.
    encryption: str
    vlan: Optional[int]

class ApGroupSummary(BaseModel):
    id: str
    name: str
    ap_count: int

class WlanGroupSummary(BaseModel):
    id: str
    name: str
    wlan_count: int

class ApStatusBreakdown(BaseModel):
    online: int
    offline: int
    flagged: int
    total: int

class FirmwareDistribution(BaseModel):
    version: str
    count: int

class ModelDistribution(BaseModel):
    model: str
    count: int

class ZoneAudit(BaseModel):
    zone_id: str
    zone_name: str
    domain_id: str
    domain_name: str
    external_ip: Optional[str]

    # AP Info
    ap_status: ApStatusBreakdown
    ap_model_distribution: List[ModelDistribution]  # R750: 45, R650: 30, etc.
    ap_groups: List[ApGroupSummary]
    ap_firmware_distribution: List[FirmwareDistribution]

    # WLAN Info
    wlan_count: int
    wlan_groups: List[WlanGroupSummary]
    wlans: List[WlanSummary]
    wlan_type_breakdown: Dict[str, int]  # {"WPA2-PSK": 5, "DPSK": 3, ...}

class SwitchGroupSummary(BaseModel):
    id: str
    name: str
    switch_count: int
    switches_online: int
    switches_offline: int

class SwitchSummary(BaseModel):
    id: str
    name: str
    model: str
    status: str
    firmware: str
    ip_address: Optional[str]

class DomainAudit(BaseModel):
    domain_id: str
    domain_name: str
    parent_domain_id: Optional[str]  # For nested domains
    parent_domain_name: Optional[str]

    # Aggregated stats for this domain
    zone_count: int
    total_aps: int
    total_wlans: int

    # Switching
    switch_groups: List[SwitchGroupSummary]
    total_switches: int
    switch_firmware_distribution: List[FirmwareDistribution]

    # Child domains (for hierarchy)
    children: List["DomainAudit"] = []

class SZAuditResult(BaseModel):
    controller_id: int
    controller_name: str
    host: str
    timestamp: datetime

    # Controller Info
    cluster_ip: Optional[str]
    controller_firmware: Optional[str]

    # Domain Hierarchy
    domains: List[DomainAudit]

    # Zones (flat list with domain references)
    zones: List[ZoneAudit]

    # Global Summaries
    total_domains: int
    total_zones: int
    total_aps: int
    total_wlans: int
    total_switches: int

    # Global Breakdowns
    ap_model_summary: List[ModelDistribution]  # Aggregate AP models across all zones
    ap_firmware_summary: List[FirmwareDistribution]
    switch_firmware_summary: List[FirmwareDistribution]
    wlan_type_summary: Dict[str, int]

    # Error handling
    error: Optional[str] = None
    partial_errors: List[str] = []  # Non-fatal errors during audit
```

### 2.2 Create Audit Router (`api/routers/sz/audit_router.py`)

```python
@router.post("/{controller_id}/audit")
async def audit_single_controller(
    controller_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> SZAuditResult:
    """
    Comprehensive audit of a single SmartZone controller.
    Returns domains, zones, APs, WLANs, switches, and firmware info.
    """

@router.post("/audit/batch")
async def audit_multiple_controllers(
    request: BatchAuditRequest,  # List of controller IDs
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> List[SZAuditResult]:
    """
    Audit multiple SmartZone controllers in parallel.
    Each controller audit is independent - failures don't affect others.
    """
```

### 2.3 Audit Logic Flow

```
1. Get controller credentials, create SZ client with v11_1
2. Fetch cluster/controller info (firmware, cluster IP)
3. Fetch all domains (recursive for nested structure)
4. For each domain:
   a. Fetch zones in domain
   b. Fetch switch groups and switches in domain
5. For each zone:
   a. Fetch APs → extract status, firmware, and model
   b. Fetch AP Groups
   c. Fetch WLANs → extract auth types
   d. Fetch WLAN Groups
   e. Get zone details → extract external IP
6. Aggregate statistics
7. Build hierarchical domain tree
8. Return comprehensive result
```

---

## Phase 3: Frontend

### 3.1 Create SZ Audit Page (`src/pages/SZAudit.tsx`)

**Layout Sections:**

1. **Controller Selection** (top)
   - Multi-select for SmartZone controllers
   - "Select All" / "Clear" buttons
   - "Run Audit" button

2. **Global Summary** (after audit)
   - Total controllers audited
   - Aggregate: Domains | Zones | APs | WLANs | Switches
   - WLAN type breakdown chart/badges
   - Firmware distribution summary

3. **Per-Controller Results** (expandable accordion)
   - Controller header with key stats
   - **Domains Tab**: Hierarchical tree view
   - **Zones Tab**: Table/cards with zone details
   - **Switching Tab**: Switch groups and switches
   - **Firmware Tab**: Distribution charts

### 3.2 UI Mockup

```
┌─────────────────────────────────────────────────────────────────────────┐
│ SZ Audit                                                                 │
│ Comprehensive SmartZone controller audit                                │
├─────────────────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────────────────┐ │
│ │ Select Controllers                              [Select All] [Clear] │ │
│ │ ☑ SZ-Production (sz-prod.company.com)                               │ │
│ │ ☑ SZ-DR (sz-dr.company.com)                                         │ │
│ │ ☐ SZ-Lab (192.168.1.100)                                            │ │
│ │                                           [Run Audit] (2 selected)   │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│ ┌─────────────────────────────────────────────────────────────────────┐ │
│ │ GLOBAL SUMMARY                                                       │ │
│ │ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │ │
│ │ │ Domains  │ │  Zones   │ │   APs    │ │  WLANs   │ │ Switches │   │ │
│ │ │    12    │ │    45    │ │   1,247  │ │   156    │ │    89    │   │ │
│ │ └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘   │ │
│ │                                                                      │ │
│ │ WLAN Types: WPA2-Enterprise (45) | DPSK (38) | WPA2-PSK (52) | ...  │ │
│ │                                                                      │ │
│ │ AP Models: R750 (520) | R650 (380) | T750 (200) | R350 (147)        │ │
│ │ AP Firmware: 7.0.0.123 (890) | 6.1.2.456 (312) | 5.2.2.100 (45)    │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│ ┌─────────────────────────────────────────────────────────────────────┐ │
│ │ ▼ SZ-Production (sz-prod.company.com) - v7.0.0.100                  │ │
│ │   Cluster IP: 203.0.113.50                                          │ │
│ │   8 Domains | 32 Zones | 890 APs | 98 WLANs | 56 Switches          │ │
│ │                                                                      │ │
│ │   [Domains] [Zones] [Switching] [Firmware]                          │ │
│ │   ┌───────────────────────────────────────────────────────────────┐ │ │
│ │   │ DOMAINS (Hierarchical)                                        │ │ │
│ │   │ ├─ System (root)                                              │ │ │
│ │   │ │  ├─ North Region                                            │ │ │
│ │   │ │  │  ├─ Building-A (3 zones, 120 APs)                       │ │ │
│ │   │ │  │  └─ Building-B (2 zones, 85 APs)                        │ │ │
│ │   │ │  └─ South Region                                            │ │ │
│ │   │ │     └─ Campus-Main (5 zones, 250 APs)                       │ │ │
│ │   └───────────────────────────────────────────────────────────────┘ │ │
│ │                                                                      │ │
│ │   ┌───────────────────────────────────────────────────────────────┐ │ │
│ │   │ ZONES                                                         │ │ │
│ │   │ ┌─────────────────────────────────────────────────────────┐   │ │ │
│ │   │ │ Zone: Building-A-Floor1          Domain: Building-A     │   │ │ │
│ │   │ │ External IP: 203.0.113.51                                │   │ │ │
│ │   │ │ APs: 45 (42 online, 3 offline) | AP Groups: 3           │   │ │ │
│ │   │ │ Models: R750 (25) | R650 (15) | R350 (5)                 │   │ │ │
│ │   │ │ WLANs: 6 | WLAN Groups: 2                                │   │ │ │
│ │   │ │ WLANs:                                                   │   │ │ │
│ │   │ │  • Corp-WiFi (WPA2-Enterprise)                          │   │ │ │
│ │   │ │  • Guest (Open + Captive Portal)                        │   │ │ │
│ │   │ │  • Resident-DPSK (DPSK)                                 │   │ │ │
│ │   │ └─────────────────────────────────────────────────────────┘   │ │ │
│ │   └───────────────────────────────────────────────────────────────┘ │ │
│ │                                                                      │ │
│ │   ┌───────────────────────────────────────────────────────────────┐ │ │
│ │   │ SWITCHING                                                     │ │ │
│ │   │ Switch Groups: 4 | Total Switches: 56 (52 online, 4 offline) │ │ │
│ │   │ ┌─────────────────────────────────────────────────────────┐   │ │ │
│ │   │ │ Switch Group: IDF-Switches (24 switches)                │   │ │ │
│ │   │ │ Firmware: ICX7150-24P (v08.0.95)                        │   │ │ │
│ │   │ └─────────────────────────────────────────────────────────┘   │ │ │
│ │   └───────────────────────────────────────────────────────────────┘ │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│ ▶ SZ-DR (sz-dr.company.com) - Click to expand                           │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.3 Navigation Updates

**Sidebar.tsx** - Add to "Informational" category:
```tsx
{ to: "/sz-audit", icon: <ClipboardList size={20} />, label: "SZ Audit", requiresAuth: true, rolesAllowed: ["user","admin"] },
```

**App.tsx** - Add route:
```tsx
<Route path="/sz-audit" element={<ProtectedRoute element={<SZAudit />} />} />
```

---

## Implementation Order

### Backend (Phase 1 & 2)
1. [ ] `api/szapi/services/wlans.py` - WLAN + WLAN Groups service
2. [ ] `api/szapi/services/apgroups.py` - AP Groups service
3. [ ] `api/szapi/services/system.py` - Cluster/controller info
4. [ ] Update `api/szapi/services/zones.py` - Add zone details method
5. [ ] Update `api/szapi/client.py` - Attach new services
6. [ ] `api/schemas/sz_audit.py` - Response schemas
7. [ ] `api/routers/sz/audit_router.py` - Audit endpoints
8. [ ] Update `api/main.py` - Register router

### Frontend (Phase 3)
9. [ ] `src/pages/SZAudit.tsx` - Main page
10. [ ] Add route to `App.tsx`
11. [ ] Add nav item to `Sidebar.tsx`

---

## SmartZone v11 API Reference

Key endpoints we'll use:

| Resource | Endpoint | Method |
|----------|----------|--------|
| Domains | `/v11_1/domains` | GET |
| Zones | `/v11_1/rkszones` | GET |
| Zone Details | `/v11_1/rkszones/{id}` | GET |
| APs by Zone | `/v11_1/rkszones/{zoneId}/aps` | GET |
| AP Groups | `/v11_1/rkszones/{zoneId}/apgroups` | GET |
| WLANs | `/v11_1/rkszones/{zoneId}/wlans` | GET |
| WLAN Groups | `/v11_1/rkszones/{zoneId}/wlangroups` | GET |
| Switch Groups | `/v11_1/switchm/groups` | GET |
| Switches | `/v11_1/switchm/switches` | POST (with filters) |
| Cluster Info | `/v11_1/cluster` | GET |
| Controller | `/v11_1/controller` | GET |

---

## Notes

- **API Version**: Force `v11_1` for all calls (pass to SZClient constructor)
- **Domain Hierarchy**: SmartZone supports nested admin domains - need recursive fetch
- **Error Handling**: Audit each component independently; collect partial errors
- **Performance**: Use `asyncio.gather()` for parallel zone/domain fetches
- **AP Status Mapping**: SmartZone uses "Connect"/"Disconnect" - map to online/offline
