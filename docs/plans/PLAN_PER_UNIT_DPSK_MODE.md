# Implementation Plan: Per-Unit SSID DPSK Mode Extension

## Overview

Extend the existing Per-Unit SSID workflow to support DPSK (Dynamic Pre-Shared Key) mode. When enabled, the workflow will create Identity Groups, DPSK Pools, and Passphrases alongside the standard AP Groups, SSIDs, and network activations.

**Key Architectural Change**: This implementation introduces a **shared phases library** that any workflow can use, establishing the foundation for standardized, reusable workflow components.

---

## Critical: DPSK Creation Order

RuckusONE requires resources to be created in this specific order:

```
1. Identity Group     → Required first (standalone)
2. DPSK Pool          → Requires identity_group_id
3. WiFi Network       → Created as DPSK type, tied to dpsk_service_id
4. Passphrases        → Added to DPSK pool (can happen after network)
```

**Important**: The WiFi Network is created WITH a reference to the DPSK service - it's not a separate "activation" step. The network type is `dpsk` and must reference the DPSK pool during creation.

---

## Key Design Decisions

### 1. Trigger Mechanism
- **Primary**: `security_type = "DPSK"` in CSV triggers DPSK mode automatically
- **Secondary**: Explicit UI toggle "Enable DPSK Mode" for clarity
- When DPSK mode is active, additional phases execute and DPSK-specific options appear

### 2. CSV Format for DPSK Mode

**Reuses existing `ssid_password` column as the DPSK passphrase** - no new required columns!

**New Optional Columns (DPSK-specific)**:
| Column | Required | Description |
|--------|----------|-------------|
| `username` | No | Identity/passphrase username |
| `email` | No | User email for passphrase |
| `description` | No | Description for passphrase |

**Multiple Passphrases Per Unit**: Multiple CSV rows with the same `unit_number` aggregate passphrases into the same DPSK Pool + Identity Group.

**Multiple VLANs Per Unit**: Passphrases within the same unit can have DIFFERENT VLANs. Each `default_vlan` value in the CSV row applies to THAT specific passphrase. Use cases:
- **Same VLAN**: Family members sharing the same network segment
- **Different VLANs**: Resident vs Guest isolation within the same unit's SSID
- DPSK handles the per-passphrase VLAN assignment at the pool level

**VLAN Semantics**:
- **Passphrase with VLAN**: Traffic uses that VLAN (overrides network default)
- **Passphrase without VLAN**: Falls back to the network's default VLAN
- **Network default_vlan**: Acts as a "catchall" for passphrases without explicit VLANs

**Example DPSK CSV**:
```csv
unit_number,ssid_name,ssid_password,security_type,default_vlan,username,email,description,ap_serial_or_name
101,Unit-101-WiFi,SecurePass101!,DPSK,10,john.doe,john@example.com,Primary resident,AP-101-Living
101,Unit-101-WiFi,GuestPass101!,DPSK,99,guest-101,,Guest access (isolated VLAN),
102,Unit-102-WiFi,SecurePass102!,DPSK,20,jane.smith,jane@example.com,Primary resident,AP-102-Living
102,Unit-102-WiFi,Family102Pass!,DPSK,20,family-102,,Family member (same VLAN),
102,Unit-102-WiFi,GuestPass102!,DPSK,99,guest-102,,Guest access (isolated VLAN),
```

**CSV Validation (Pre-flight)**:
- **Duplicate passphrase check**: Passphrases MUST be unique across the entire CSV. Warn and block if duplicates found.
- **VLAN collision check**: Audit existing venue SSIDs/networks for VLAN conflicts before creation.

### 3. Naming Conventions (Reuse Prefix/Postfix)
All resources use the same `{prefix}{unit_number}{postfix}` pattern:
- **AP Group**: `{prefix}{unit}{postfix}` (existing)
- **SSID/Network**: `{prefix}{unit}{postfix}-WiFi` or from CSV
- **Identity Group**: `{prefix}{unit}{postfix}-IDG`
- **DPSK Pool**: `{prefix}{unit}{postfix}-DPSK`

### 4. DPSK Pool Master Settings (UI Options)
```python
class DpskPoolSettings(BaseModel):
    passphrase_length: int = 12          # For auto-generated (not used when CSV provides passphrase)
    passphrase_format: str = "KEYBOARD_FRIENDLY"  # NUMBERS_ONLY, KEYBOARD_FRIENDLY, MOST_SECURED
    max_devices_per_passphrase: int = 0   # 0 = unlimited
    expiration_days: Optional[int] = None # None = no expiration
```

### 5. VLAN Tracking & Auto-Assignment

**Pre-flight VLAN Audit** (before any creation):
1. Fetch all existing WiFi networks on the venue
2. Fetch all SSID activations on AP Groups
3. Build a "used VLANs" set from existing configurations
4. Compare against CSV VLANs - warn on collisions

**Auto-Assignment** (when VLAN is omitted in CSV):
- Maintain a configurable VLAN range (e.g., 10-999)
- Track used VLANs from: (a) existing venue networks, (b) earlier CSV rows
- Assign next available VLAN from range
- Display assigned VLANs in summary before execution

**VLAN Collision Warnings**:
- Warn if CSV VLAN matches an existing venue SSID's VLAN
- Warn if same VLAN used across different units (may be intentional for shared services)
- Allow user to proceed with acknowledgment

> **Note**: VLAN tracking is a more complex step - stub out interface first, flag for detailed implementation later if needed.

---

## Architecture: Shared Phases Library

### Design Philosophy

Instead of duplicating phase logic across workflows (Per-Unit SSID, Cloudpath DPSK Migration, future workflows), we extract common operations into a **shared phases library**. Each workflow composes its pipeline from these reusable building blocks.

### New Directory Structure

```
api/workflow/
├── __init__.py
├── models.py                    # Workflow/Phase/Task models (existing)
├── engine.py                    # Workflow orchestration (existing)
├── executor.py                  # Task execution (existing)
├── state_manager.py             # Redis state (existing)
├── events.py                    # Event publishing (existing)
├── idempotent.py                # Find-or-create helpers (existing)
├── parallel_orchestrator.py     # Parallel job orchestration (existing)
│
├── phases/                      # ⭐ NEW: Shared phase library
│   ├── __init__.py              # Phase registry and exports
│   ├── base.py                  # Base phase executor class
│   ├── identity_groups.py       # Create/find identity groups
│   ├── dpsk_pools.py            # Create/find DPSK pools
│   ├── passphrases.py           # Create passphrases
│   ├── ap_groups.py             # Create/find AP groups
│   ├── wifi_networks.py         # Create WiFi networks (PSK, DPSK, etc.)
│   ├── ssid_activation.py       # Activate SSIDs on venues/AP groups
│   ├── ap_assignment.py         # Assign APs to AP groups
│   └── lan_ports.py             # Configure LAN ports
│
└── utils/
    ├── vlan_audit.py            # VLAN tracking and collision detection
    └── csv_validation.py        # CSV parsing and validation utilities
```

### Shared Phase Base Class

```python
# api/workflow/phases/base.py
from abc import ABC, abstractmethod
from typing import Dict, Any, List
from workflow.models import Task, TaskStatus

class BasePhaseExecutor(ABC):
    """Base class for all phase executors"""

    def __init__(self, context: Dict[str, Any]):
        self.context = context
        self.r1_client = context.get('r1_client')
        self.tenant_id = context.get('tenant_id')
        self.venue_id = context.get('venue_id')
        self.job_id = context.get('job_id')
        self.event_publisher = context.get('event_publisher')
        self.state_manager = context.get('state_manager')
        self.options = context.get('options', {})
        self.previous_results = context.get('previous_phase_results', {})

    @abstractmethod
    async def execute(self) -> List[Task]:
        """Execute the phase and return tasks"""
        pass

    def get_previous_phase_data(self, phase_id: str, key: str, default=None):
        """Safely retrieve data from a previous phase"""
        phase_data = self.previous_results.get(phase_id, {})
        aggregated = phase_data.get('aggregated', {})
        data_list = aggregated.get(key, [default])
        return data_list[0] if data_list else default

    async def emit(self, message: str, level: str = "info"):
        """Emit a status message"""
        if self.event_publisher and self.job_id:
            await self.event_publisher.message(self.job_id, message, level)

    async def track_resource(self, resource_type: str, resource: dict):
        """Track a created resource for potential cleanup"""
        if self.state_manager and self.job_id:
            await self.state_manager.add_created_resource(
                self.job_id, resource_type, resource
            )

    def create_task(self, name: str, output_data: dict, status: TaskStatus = TaskStatus.COMPLETED) -> Task:
        """Helper to create a completed task"""
        return Task(
            id=self.__class__.__name__.lower(),
            name=name,
            task_type="phase_execution",
            status=status,
            input_data={},
            output_data=output_data
        )
```

### Phase Implementations

#### Identity Groups Phase (Shared)

```python
# api/workflow/phases/identity_groups.py
from typing import Dict, Any, List
from workflow.phases.base import BasePhaseExecutor
from workflow.models import Task
from workflow.idempotent import IdempotentHelper

class IdentityGroupsPhase(BasePhaseExecutor):
    """
    Create identity groups - reusable across workflows.

    Input (from context.options or context.input_data):
        - items: List of dicts with 'name' and optional 'description'
        - OR units: List of unit configs (will derive names from prefix/postfix)

    Output:
        - idg_map: {item_key: identity_group_id}
    """

    async def execute(self) -> List[Task]:
        await self.emit("Creating identity groups...")

        helper = IdempotentHelper(self.r1_client)
        prefix = self.options.get('ap_group_prefix', '')
        postfix = self.options.get('identity_group_postfix', '-IDG')

        # Support both explicit items and unit-based derivation
        items = self.options.get('identity_group_items') or self._derive_from_units()

        idg_map = {}
        created_count = 0
        existed_count = 0

        for item in items:
            name = item.get('name') or f"{prefix}{item.get('key')}{postfix}"
            key = item.get('key') or name
            description = item.get('description', f"Identity group: {name}")

            result = await helper.find_or_create_identity_group(
                tenant_id=self.tenant_id,
                name=name,
                description=description
            )

            idg_map[key] = result.get('id')

            if result.get('existed'):
                existed_count += 1
                await self.emit(f"  Found existing: {name}")
            else:
                created_count += 1
                await self.emit(f"  Created: {name}", "success")
                await self.track_resource('identity_groups', {
                    'id': result.get('id'),
                    'name': name,
                    'key': key
                })

        await self.emit(
            f"Identity groups: {created_count} created, {existed_count} existed",
            "success"
        )

        return [self.create_task(
            f"Created {created_count} identity groups",
            {'idg_map': idg_map}
        )]

    def _derive_from_units(self) -> List[dict]:
        """Derive identity group items from units"""
        units = self.context.get('input_data', {}).get('units', [])
        prefix = self.options.get('ap_group_prefix', '')
        postfix = self.options.get('identity_group_postfix', '-IDG')

        seen = set()
        items = []
        for unit in units:
            unit_number = unit.get('unit_number')
            if unit_number and unit_number not in seen:
                seen.add(unit_number)
                items.append({
                    'key': unit_number,
                    'name': f"{prefix}{unit_number}{postfix}",
                    'description': f"Identity group for unit {unit_number}"
                })
        return items


# Standalone execute function for workflow engine compatibility
async def execute(context: Dict[str, Any]) -> List[Task]:
    return await IdentityGroupsPhase(context).execute()
```

#### DPSK Pools Phase (Shared)

```python
# api/workflow/phases/dpsk_pools.py
from typing import Dict, Any, List
from workflow.phases.base import BasePhaseExecutor
from workflow.models import Task
from workflow.idempotent import IdempotentHelper

class DpskPoolsPhase(BasePhaseExecutor):
    """
    Create DPSK pools linked to identity groups.

    Dependencies: create_identity_groups (for idg_map)

    Input:
        - idg_map from previous phase
        - dpsk_pool_settings from options

    Output:
        - pool_map: {key: {pool_id, identity_group_id}}
    """

    async def execute(self) -> List[Task]:
        await self.emit("Creating DPSK pools...")

        # Get identity group map from previous phase
        idg_map = self.get_previous_phase_data('create_identity_groups', 'idg_map', {})

        if not idg_map:
            await self.emit("No identity groups found - skipping DPSK pools", "warning")
            return [self.create_task("No DPSK pools to create", {'pool_map': {}})]

        helper = IdempotentHelper(self.r1_client)
        prefix = self.options.get('ap_group_prefix', '')
        postfix = self.options.get('dpsk_pool_postfix', '-DPSK')
        dpsk_settings = self.options.get('dpsk_pool_settings', {})

        pool_map = {}

        for key, identity_group_id in idg_map.items():
            pool_name = f"{prefix}{key}{postfix}"

            await self.emit(f"  Creating DPSK pool: {pool_name}")

            result = await helper.find_or_create_dpsk_pool(
                tenant_id=self.tenant_id,
                name=pool_name,
                identity_group_id=identity_group_id,
                description=f"DPSK pool for {key}",
                passphrase_length=dpsk_settings.get('passphrase_length', 12),
                passphrase_format=dpsk_settings.get('passphrase_format', 'KEYBOARD_FRIENDLY'),
                max_devices_per_passphrase=dpsk_settings.get('max_devices_per_passphrase', 0),
                expiration_days=dpsk_settings.get('expiration_days')
            )

            pool_map[key] = {
                'pool_id': result.get('id'),
                'identity_group_id': identity_group_id,
                'name': pool_name
            }

            status = "Found existing" if result.get('existed') else "Created"
            await self.emit(f"    {status}: {pool_name} (ID: {result.get('id')})", "success")

            if not result.get('existed'):
                await self.track_resource('dpsk_pools', {
                    'id': result.get('id'),
                    'name': pool_name,
                    'key': key,
                    'identity_group_id': identity_group_id
                })

        await self.emit(f"DPSK pools ready: {len(pool_map)} pools", "success")

        return [self.create_task(
            f"Created {len(pool_map)} DPSK pools",
            {'pool_map': pool_map, 'idg_map': idg_map}  # Forward idg_map
        )]


async def execute(context: Dict[str, Any]) -> List[Task]:
    return await DpskPoolsPhase(context).execute()
```

#### WiFi Networks Phase (Shared, with DPSK support)

```python
# api/workflow/phases/wifi_networks.py
from typing import Dict, Any, List
from workflow.phases.base import BasePhaseExecutor
from workflow.models import Task

class WifiNetworksPhase(BasePhaseExecutor):
    """
    Create WiFi networks - supports PSK, WPA2, WPA3, and DPSK types.

    For DPSK mode:
        - Dependencies: create_dpsk_pools (for pool_map)
        - Creates network WITH dpsk_service_id reference

    For PSK mode:
        - Dependencies: create_ap_groups (optional)
        - Creates standard PSK network

    Output:
        - ssid_map: {key: network_id}
    """

    async def execute(self) -> List[Task]:
        dpsk_mode = self.options.get('dpsk_mode', False)

        if dpsk_mode:
            return await self._create_dpsk_networks()
        else:
            return await self._create_psk_networks()

    async def _create_dpsk_networks(self) -> List[Task]:
        """Create DPSK-type WiFi networks tied to DPSK services"""
        await self.emit("Creating DPSK WiFi networks...")

        # Get pool map from previous phase
        pool_map = self.get_previous_phase_data('create_dpsk_pools', 'pool_map', {})

        units = self.context.get('input_data', {}).get('units', [])
        prefix = self.options.get('ap_group_prefix', '')
        postfix = self.options.get('ap_group_postfix', '')

        ssid_map = {}

        for unit in units:
            unit_number = unit.get('unit_number')
            ssid_name = unit.get('ssid_name')
            network_name = unit.get('network_name') or f"{prefix}{unit_number}{postfix}-WiFi"
            default_vlan = int(unit.get('default_vlan', 1))

            pool_info = pool_map.get(unit_number, {})
            dpsk_service_id = pool_info.get('pool_id')

            if not dpsk_service_id:
                await self.emit(f"  Skipping {unit_number} - no DPSK pool", "warning")
                continue

            await self.emit(f"  Creating DPSK network: {network_name}")

            # Create DPSK-type network with dpsk_service_id
            result = await self.r1_client.networks.create_dpsk_wifi_network(
                tenant_id=self.tenant_id,
                venue_id=self.venue_id,
                name=network_name,
                ssid=ssid_name,
                dpsk_service_id=dpsk_service_id,
                vlan_id=default_vlan,
                description=f"Per-unit DPSK SSID for {unit_number}",
                wait_for_completion=True
            )

            ssid_map[unit_number] = result.get('id')
            await self.emit(f"    Created: {network_name} (ID: {result.get('id')})", "success")

            await self.track_resource('wifi_networks', {
                'id': result.get('id'),
                'name': network_name,
                'ssid': ssid_name,
                'unit_number': unit_number,
                'dpsk_service_id': dpsk_service_id
            })

        await self.emit(f"DPSK networks ready: {len(ssid_map)} networks", "success")

        # Forward pool_map and idg_map for downstream phases
        return [self.create_task(
            f"Created {len(ssid_map)} DPSK networks",
            {
                'ssid_map': ssid_map,
                'pool_map': pool_map,
                'idg_map': self.get_previous_phase_data('create_dpsk_pools', 'idg_map', {})
            }
        )]

    async def _create_psk_networks(self) -> List[Task]:
        """Create standard PSK WiFi networks"""
        await self.emit("Creating PSK WiFi networks...")

        units = self.context.get('input_data', {}).get('units', [])
        prefix = self.options.get('ap_group_prefix', '')
        postfix = self.options.get('ap_group_postfix', '')

        ssid_map = {}

        for unit in units:
            unit_number = unit.get('unit_number')
            ssid_name = unit.get('ssid_name')
            network_name = unit.get('network_name') or f"{prefix}{unit_number}{postfix}-WiFi"
            ssid_password = unit.get('ssid_password')
            security_type = unit.get('security_type', 'WPA3')
            default_vlan = int(unit.get('default_vlan', 1))

            await self.emit(f"  Creating network: {network_name}")

            result = await self.r1_client.networks.create_wifi_network(
                tenant_id=self.tenant_id,
                venue_id=self.venue_id,
                name=network_name,
                ssid=ssid_name,
                passphrase=ssid_password,
                security_type=security_type,
                vlan_id=default_vlan,
                description=f"Per-unit SSID for {unit_number}",
                wait_for_completion=True
            )

            ssid_map[unit_number] = result.get('id')
            await self.emit(f"    Created: {network_name}", "success")

        # Forward ap_group_map if available
        ap_group_map = self.get_previous_phase_data('create_ap_groups', 'ap_group_map', {})

        return [self.create_task(
            f"Created {len(ssid_map)} PSK networks",
            {'ssid_map': ssid_map, 'ap_group_map': ap_group_map}
        )]


async def execute(context: Dict[str, Any]) -> List[Task]:
    return await WifiNetworksPhase(context).execute()
```

#### Passphrases Phase (Shared)

```python
# api/workflow/phases/passphrases.py
from typing import Dict, Any, List
from workflow.phases.base import BasePhaseExecutor
from workflow.models import Task

class PassphrasesPhase(BasePhaseExecutor):
    """
    Create passphrases in DPSK pools.

    Dependencies: create_dpsk_pools (for pool_map)

    Input:
        - units with passphrases list
        - pool_map from previous phase

    Output:
        - passphrase_map: {unit_key: [{passphrase_id, identity_id, username}]}
    """

    async def execute(self) -> List[Task]:
        await self.emit("Creating passphrases...")

        # Get pool map - check both create_wifi_networks and create_dpsk_pools
        pool_map = (
            self.get_previous_phase_data('create_wifi_networks', 'pool_map', {}) or
            self.get_previous_phase_data('create_dpsk_pools', 'pool_map', {})
        )

        if not pool_map:
            await self.emit("No DPSK pools found - skipping passphrases", "warning")
            return [self.create_task("No passphrases to create", {'passphrase_map': {}})]

        units = self.context.get('input_data', {}).get('units', [])
        passphrase_map = {}
        total_created = 0

        for unit in units:
            unit_number = unit.get('unit_number')
            passphrases = unit.get('passphrases', [])

            if not passphrases:
                continue

            pool_info = pool_map.get(unit_number, {})
            pool_id = pool_info.get('pool_id')

            if not pool_id:
                await self.emit(f"  Skipping {unit_number} - no DPSK pool", "warning")
                continue

            await self.emit(f"  Creating {len(passphrases)} passphrases for unit {unit_number}")

            created = []
            for pp in passphrases:
                # vlan_id can be None - in that case, network default VLAN applies
                vlan_id = pp.get('vlan_id')

                result = await self.r1_client.dpsk.create_passphrase(
                    pool_id=pool_id,
                    tenant_id=self.tenant_id,
                    passphrase=pp.get('passphrase'),
                    user_name=pp.get('username'),
                    user_email=pp.get('email'),
                    description=pp.get('description'),
                    vlan_id=str(vlan_id) if vlan_id else None
                )

                created.append({
                    'passphrase_id': result.get('id'),
                    'identity_id': result.get('identityId'),
                    'username': pp.get('username'),
                    'vlan_id': vlan_id
                })
                total_created += 1

            passphrase_map[unit_number] = created
            await self.emit(f"    Created {len(created)} passphrases", "success")

        await self.emit(f"Passphrases ready: {total_created} total", "success")

        # Forward maps for downstream phases
        ssid_map = self.get_previous_phase_data('create_wifi_networks', 'ssid_map', {})
        idg_map = self.get_previous_phase_data('create_wifi_networks', 'idg_map', {})

        return [self.create_task(
            f"Created {total_created} passphrases",
            {
                'passphrase_map': passphrase_map,
                'ssid_map': ssid_map,
                'pool_map': pool_map,
                'idg_map': idg_map
            }
        )]


async def execute(context: Dict[str, Any]) -> List[Task]:
    return await PassphrasesPhase(context).execute()
```

### Phase Registry

```python
# api/workflow/phases/__init__.py
"""
Shared Phase Library

Reusable phase executors for workflow composition.
"""

from .base import BasePhaseExecutor
from .identity_groups import IdentityGroupsPhase, execute as identity_groups_execute
from .dpsk_pools import DpskPoolsPhase, execute as dpsk_pools_execute
from .wifi_networks import WifiNetworksPhase, execute as wifi_networks_execute
from .passphrases import PassphrasesPhase, execute as passphrases_execute
from .ap_groups import ApGroupsPhase, execute as ap_groups_execute
from .ssid_activation import SsidActivationPhase, execute as ssid_activation_execute
from .ap_assignment import ApAssignmentPhase, execute as ap_assignment_execute
from .lan_ports import LanPortsPhase, execute as lan_ports_execute

# Phase registry for dynamic lookup
PHASE_REGISTRY = {
    'identity_groups': identity_groups_execute,
    'dpsk_pools': dpsk_pools_execute,
    'wifi_networks': wifi_networks_execute,
    'passphrases': passphrases_execute,
    'ap_groups': ap_groups_execute,
    'ssid_activation': ssid_activation_execute,
    'ap_assignment': ap_assignment_execute,
    'lan_ports': lan_ports_execute,
}

def get_phase_executor(phase_id: str):
    """Get executor function for a phase ID"""
    return PHASE_REGISTRY.get(phase_id)
```

---

## Updated Workflow Phases

### DPSK Mode OFF (Current - unchanged):
```
┌─────────────────────────────────────────────────────────────┐
│ Phase 1: Create AP Groups                                    │
│ Phase 2: Create SSIDs (WPA2/WPA3)                           │
│ Phase 3: Activate SSIDs on Venue                            │
│ Phase 4: Process Units (AP assignment + SSID activation)    │
│ Phase 5: Configure LAN Ports (optional)                     │
└─────────────────────────────────────────────────────────────┘
```

### DPSK Mode ON (New):
```
┌─────────────────────────────────────────────────────────────┐
│ Phase 1: Create AP Groups                                    │
│ Phase 2: Create Identity Groups         ← NEW (shared)      │
│ Phase 3: Create DPSK Pools              ← NEW (shared)      │
│ Phase 4: Create DPSK WiFi Networks      ← MODIFIED          │
│          (created WITH dpsk_service_id reference)           │
│ Phase 5: Create Passphrases             ← NEW (shared)      │
│ Phase 6: Activate SSIDs on Venue                            │
│ Phase 7: Process Units (AP + SSID activation on groups)     │
│ Phase 8: Configure LAN Ports (optional)                     │
└─────────────────────────────────────────────────────────────┘
```

**Note**: No separate "Activate DPSK on Networks" phase needed - the WiFi network is created with the DPSK service reference.

### Data Flow Between Phases

```
Phase 1 (AP Groups)        → ap_group_map: {unit_number: ap_group_id}
Phase 2 (Identity Groups)  → idg_map: {unit_number: identity_group_id}
Phase 3 (DPSK Pools)       → pool_map: {unit_number: {pool_id, identity_group_id}}
Phase 4 (WiFi Networks)    → ssid_map: {unit_number: network_id}
                             (also forwards pool_map, idg_map)
Phase 5 (Passphrases)      → passphrase_map: {unit_number: [{passphrase_id, ...}]}
Phase 6+ (Existing)        → Uses maps from above
```

---

## File Changes Summary

### New Files (Shared Phase Library)

| File | Purpose |
|------|---------|
| `api/workflow/phases/__init__.py` | Phase registry and exports |
| `api/workflow/phases/base.py` | Base phase executor class |
| `api/workflow/phases/identity_groups.py` | Create identity groups |
| `api/workflow/phases/dpsk_pools.py` | Create DPSK pools |
| `api/workflow/phases/wifi_networks.py` | Create WiFi networks (PSK/DPSK) |
| `api/workflow/phases/passphrases.py` | Create passphrases |
| `api/workflow/utils/vlan_audit.py` | VLAN tracking utilities |

### Modified Files

| File | Changes |
|------|---------|
| `api/r1api/services/networks.py` | Add `create_dpsk_wifi_network()` method |
| `api/routers/per_unit_ssid/workflow_definition.py` | Add DPSK phase definitions |
| `api/routers/per_unit_ssid/per_unit_ssid_router.py` | Add DPSK models and options |
| `src/pages/PerUnitSSID.tsx` | DPSK UI, CSV parsing, state management |

### Migrate Existing Phases

| From | To |
|------|-----|
| `api/routers/per_unit_ssid/phases/create_ap_groups.py` | Extract core to `api/workflow/phases/ap_groups.py` |
| `api/routers/per_unit_ssid/phases/activate_ssids.py` | Extract core to `api/workflow/phases/ssid_activation.py` |
| `api/routers/cloudpath/phases/identity_groups.py` | Merge into shared `api/workflow/phases/identity_groups.py` |
| `api/routers/cloudpath/phases/dpsk_pools.py` | Merge into shared `api/workflow/phases/dpsk_pools.py` |
| `api/routers/cloudpath/phases/passphrases.py` | Merge into shared `api/workflow/phases/passphrases.py` |

---

## NetworksService Extension

```python
# api/r1api/services/networks.py

async def create_dpsk_wifi_network(
    self,
    tenant_id: str,
    venue_id: str,
    name: str,
    ssid: str,
    dpsk_service_id: str,  # Required - the DPSK pool ID
    vlan_id: int = 1,
    description: str = None,
    wait_for_completion: bool = True
):
    """
    Create a DPSK WiFi network tied to a DPSK service (pool).

    The network is created as type 'dpsk' and the DPSK service handles authentication.

    Args:
        tenant_id: Tenant ID
        venue_id: Venue ID
        name: Internal network name
        ssid: Broadcast SSID
        dpsk_service_id: DPSK pool/service ID (must exist first!)
        vlan_id: Default VLAN (fallback when passphrase has no VLAN)
        description: Optional description
        wait_for_completion: Wait for async task to complete

    Returns:
        Created network data with 'id' field
    """
    body = {
        "name": name,
        "description": description or f"DPSK Network {name}",
        "type": WifiNetworkType.DPSK,  # "dpsk" - discriminator field
        "wlanSettings": {
            "ssid": ssid,
            "vlan": vlan_id,
            "enabled": True
        },
        "dpskServiceId": dpsk_service_id,  # Link to DPSK pool
        "venues": [{"id": venue_id}]
    }

    response = self.client.post("/wifiNetworks", payload=body, override_tenant_id=tenant_id)

    if not response.ok:
        error_data = response.json()
        raise Exception(f"Failed to create DPSK network: {error_data}")

    result = response.json()

    # Handle async operation
    if wait_for_completion and response.status_code == 202:
        request_id = result.get('requestId')
        if request_id:
            await self.client.await_task_completion(
                request_id=request_id,
                override_tenant_id=tenant_id
            )
            # Fetch the created network
            network_id = result.get('id')
            if network_id:
                return await self.get_wifi_network_by_id(network_id, tenant_id)

    return result
```

---

## Workflow Definition Update

```python
# api/routers/per_unit_ssid/workflow_definition.py

from workflow.models import WorkflowDefinition, PhaseDefinition

def get_workflow_definition(
    dpsk_mode: bool = False,
    configure_lan_ports: bool = False
) -> WorkflowDefinition:
    """
    Build workflow definition with conditional DPSK phases.

    All phases use shared executors from api/workflow/phases/
    """

    if dpsk_mode:
        phases = [
            PhaseDefinition(
                id="create_ap_groups",
                name="Create AP Groups",
                dependencies=[],
                parallelizable=False,
                critical=True,
                executor="workflow.phases.ap_groups.execute"
            ),
            PhaseDefinition(
                id="create_identity_groups",
                name="Create Identity Groups",
                dependencies=["create_ap_groups"],
                parallelizable=False,
                critical=True,
                executor="workflow.phases.identity_groups.execute"
            ),
            PhaseDefinition(
                id="create_dpsk_pools",
                name="Create DPSK Pools",
                dependencies=["create_identity_groups"],
                parallelizable=False,
                critical=True,
                executor="workflow.phases.dpsk_pools.execute"
            ),
            PhaseDefinition(
                id="create_wifi_networks",
                name="Create DPSK WiFi Networks",
                dependencies=["create_dpsk_pools"],
                parallelizable=False,
                critical=True,
                executor="workflow.phases.wifi_networks.execute"
            ),
            PhaseDefinition(
                id="create_passphrases",
                name="Create Passphrases",
                dependencies=["create_wifi_networks"],
                parallelizable=False,
                critical=True,
                executor="workflow.phases.passphrases.execute"
            ),
            PhaseDefinition(
                id="activate_ssids",
                name="Activate SSIDs on Venue",
                dependencies=["create_passphrases"],
                parallelizable=False,
                critical=True,
                executor="workflow.phases.ssid_activation.execute"
            ),
            PhaseDefinition(
                id="process_units",
                name="Process Units",
                dependencies=["activate_ssids"],
                parallelizable=False,
                critical=True,
                executor="workflow.phases.ap_assignment.execute"
            ),
        ]
    else:
        # Original PSK workflow
        phases = [
            PhaseDefinition(
                id="create_ap_groups",
                name="Create AP Groups",
                dependencies=[],
                parallelizable=False,
                critical=True,
                executor="workflow.phases.ap_groups.execute"
            ),
            PhaseDefinition(
                id="create_wifi_networks",
                name="Create SSIDs",
                dependencies=["create_ap_groups"],
                parallelizable=False,
                critical=True,
                executor="workflow.phases.wifi_networks.execute"
            ),
            PhaseDefinition(
                id="activate_ssids",
                name="Activate SSIDs on Venue",
                dependencies=["create_wifi_networks"],
                parallelizable=False,
                critical=True,
                executor="workflow.phases.ssid_activation.execute"
            ),
            PhaseDefinition(
                id="process_units",
                name="Process Units",
                dependencies=["activate_ssids"],
                parallelizable=False,
                critical=True,
                executor="workflow.phases.ap_assignment.execute"
            ),
        ]

    # Optional LAN ports phase
    if configure_lan_ports:
        phases.append(PhaseDefinition(
            id="configure_lan_ports",
            name="Configure LAN Ports",
            dependencies=["process_units"],
            parallelizable=False,
            critical=False,  # Non-critical - workflow succeeds even if this fails
            executor="workflow.phases.lan_ports.execute"
        ))

    return WorkflowDefinition(
        name="per_unit_ssid_configuration",
        description="Configure per-unit SSIDs with AP groups and optional DPSK",
        phases=phases
    )
```

---

## Implementation Order

### Step 0: Architecture Setup (Do First)
1. [ ] Create `api/workflow/phases/` directory structure
2. [ ] Create `base.py` with `BasePhaseExecutor` class
3. [ ] Create `__init__.py` with phase registry

### Step 1: Extract & Migrate Existing Phases
4. [ ] Extract AP Groups phase to shared location
5. [ ] Extract SSID Activation phase to shared location
6. [ ] Extract AP Assignment phase to shared location
7. [ ] Extract LAN Ports phase to shared location
8. [ ] Update Per-Unit SSID workflow to use shared phases
9. [ ] Verify existing PSK workflow still works

### Step 2: DPSK Infrastructure
10. [ ] Merge Cloudpath identity_groups.py into shared phase
11. [ ] Merge Cloudpath dpsk_pools.py into shared phase
12. [ ] Merge Cloudpath passphrases.py into shared phase
13. [ ] Add `create_dpsk_wifi_network()` to NetworksService
14. [ ] Create WiFi Networks phase with DPSK support

### Step 3: Backend Integration
15. [ ] Add DPSK Pydantic models to router
16. [ ] Update workflow_definition.py for DPSK mode
17. [ ] Add pre-flight validation endpoint
18. [ ] Create VLAN audit utilities

### Step 4: Frontend
19. [ ] Add DPSK state and settings to PerUnitSSID.tsx
20. [ ] Update CSV parsing for DPSK + passphrase aggregation
21. [ ] Add duplicate passphrase validation
22. [ ] Add DPSK Mode UI toggle and settings
23. [ ] Add VLAN audit display
24. [ ] Update request payload construction

### Step 5: Testing
25. [ ] Create test data generation script
26. [ ] Test with 200 units at scale
27. [ ] Test idempotency (re-run existing resources)
28. [ ] Verify Cloudpath workflow still works with shared phases

---

## Design Decisions (Confirmed)

1. **Shared Phases Architecture**: ✅ YES - Extract common phase logic into `api/workflow/phases/` for reuse across workflows.

2. **DPSK Network Creation**: ✅ CLARIFIED - Network is created as type `dpsk` WITH `dpskServiceId` reference. No separate "activation" step needed.

3. **VLAN Semantics**: ✅ CLARIFIED - Passphrase VLAN overrides network default when specified; otherwise falls back to network VLAN.

4. **Idempotency**: ✅ YES - Use `IdempotentHelper` patterns for all resource creation.

5. **Cleanup on Failure**: ❌ NO - User will implement separate "destroy" function later.

6. **CSV Validation**: ✅ YES - Block on duplicate passphrases (must be unique).

7. **VLAN Auto-Assignment**: ✅ YES - Implement basic version, enhance later.

---

## Future Enhancements (Out of Scope)

- Destroy/cleanup function for created resources
- Bulk passphrase import from external sources
- Policy set attachment automation
- DPSK Orchestrator integration (separate project)
