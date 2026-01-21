# Implementation Plan: Per-Unit SSID DPSK Mode Extension

## Overview

Extend the existing Per-Unit SSID workflow to support DPSK (Dynamic Pre-Shared Key) mode. When enabled, the workflow will create Identity Groups, DPSK Pools, and Passphrases alongside the standard AP Groups, SSIDs, and network activations.

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

## Architecture Changes

### Updated Workflow Phases

```
PRE-FLIGHT VALIDATION (Before Workflow Starts):
┌─────────────────────────────────────────────────────────────┐
│ ✓ CSV Parsing & Passphrase Aggregation                      │
│ ✓ Duplicate Passphrase Check (BLOCK if found!)              │
│ ✓ VLAN Audit: Fetch existing venue networks/AP Group SSIDs  │
│ ✓ VLAN Auto-Assignment for omitted values                   │
│ ✓ VLAN Collision Warnings (allow proceed with ack)          │
└─────────────────────────────────────────────────────────────┘

DPSK Mode OFF (Current):
┌─────────────────────────────────────────────────────────────┐
│ Phase 1: Create AP Groups                                    │
│ Phase 2: Create SSIDs (WPA2/WPA3)                           │
│ Phase 3: Activate SSIDs on Venue                            │
│ Phase 4: Process Units (AP assignment + SSID activation)    │
│ Phase 5: Configure LAN Ports (optional)                     │
└─────────────────────────────────────────────────────────────┘

DPSK Mode ON (New):
┌─────────────────────────────────────────────────────────────┐
│ Phase 1: Create AP Groups                                    │
│ Phase 2: Create Identity Groups         ← NEW               │
│ Phase 3: Create DPSK Pools              ← NEW               │
│ Phase 4: Create Passphrases             ← NEW               │
│ Phase 5: Create SSIDs (DPSK type)       ← MODIFIED          │
│ Phase 6: Activate DPSK on Networks      ← NEW               │
│ Phase 7: Activate SSIDs on Venue                            │
│ Phase 8: Process Units (AP + SSID activation)               │
│ Phase 9: Configure LAN Ports (optional)                     │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow Between Phases

```
Phase 1 (AP Groups)     → ap_group_map: {unit_number: ap_group_id}
Phase 2 (Identity Groups) → idg_map: {unit_number: identity_group_id}
Phase 3 (DPSK Pools)    → pool_map: {unit_number: {pool_id, identity_group_id}}
Phase 4 (Passphrases)   → passphrase_map: {unit_number: [{passphrase_id, identity_id}]}
Phase 5 (SSIDs)         → ssid_map: {unit_number: ssid_id}
Phase 6 (DPSK Activate) → dpsk_activation_map: {unit_number: {network_id, pool_id}}
Phase 7+ (Existing)     → Uses maps from above
```

---

## File Changes

### Backend (API)

#### 1. New Pydantic Models
**File**: `api/routers/per_unit_ssid/per_unit_ssid_router.py`

```python
class DpskPoolSettings(BaseModel):
    """Master DPSK pool settings applied to all pools"""
    passphrase_length: int = Field(default=12, ge=8, le=64)
    passphrase_format: str = Field(default="KEYBOARD_FRIENDLY")  # NUMBERS_ONLY, KEYBOARD_FRIENDLY, MOST_SECURED
    max_devices_per_passphrase: int = Field(default=0, ge=0)  # 0 = unlimited
    expiration_days: Optional[int] = Field(default=None, ge=1)

class PassphraseConfig(BaseModel):
    """Configuration for a single passphrase within a unit (aggregated from CSV rows)"""
    passphrase: str = Field(..., min_length=8, max_length=64)  # From ssid_password column
    vlan_id: int  # Each passphrase can have its own VLAN (from default_vlan column)
    username: Optional[str] = None
    email: Optional[str] = None
    description: Optional[str] = None

class UnitConfig(BaseModel):  # MODIFIED
    """Configuration for a single unit"""
    unit_number: str
    ap_identifiers: List[str] = []
    ssid_name: str
    network_name: Optional[str] = None
    ssid_password: Optional[str] = None  # First passphrase (for non-DPSK) or ignored in DPSK
    security_type: str = "WPA3"  # WPA2, WPA3, WPA2/WPA3, DPSK
    default_vlan: str = "1"  # Default VLAN for the SSID itself
    # DPSK-specific (aggregated from multiple CSV rows with same unit_number)
    passphrases: List[PassphraseConfig] = []  # Each with its own passphrase + vlan

class PerUnitSSIDRequest(BaseModel):  # MODIFIED
    # ... existing fields ...
    # New DPSK fields
    dpsk_mode: bool = Field(default=False, description="Enable DPSK mode")
    dpsk_pool_settings: DpskPoolSettings = Field(default_factory=DpskPoolSettings)
    identity_group_postfix: str = Field(default="-IDG")
    dpsk_pool_postfix: str = Field(default="-DPSK")
```

#### 2. Workflow Definition Update
**File**: `api/routers/per_unit_ssid/workflow_definition.py`

```python
# New DPSK phases
DPSK_PHASES: List[PhaseDefinition] = [
    PhaseDefinition(
        id="create_identity_groups",
        name="Create Identity Groups",
        dependencies=["create_ap_groups"],
        parallelizable=False,
        critical=True,
        executor="routers.per_unit_ssid.phases.create_identity_groups.execute"
    ),
    PhaseDefinition(
        id="create_dpsk_pools",
        name="Create DPSK Pools",
        dependencies=["create_identity_groups"],
        parallelizable=False,
        critical=True,
        executor="routers.per_unit_ssid.phases.create_dpsk_pools.execute"
    ),
    PhaseDefinition(
        id="create_passphrases",
        name="Create Passphrases",
        dependencies=["create_dpsk_pools"],
        parallelizable=False,
        critical=True,
        executor="routers.per_unit_ssid.phases.create_passphrases.execute"
    ),
    PhaseDefinition(
        id="activate_dpsk_on_networks",
        name="Activate DPSK on Networks",
        dependencies=["create_ssids"],
        parallelizable=False,
        critical=True,
        executor="routers.per_unit_ssid.phases.activate_dpsk.execute"
    ),
]

def get_workflow_definition(dpsk_mode: bool = False, configure_lan_ports: bool = False):
    """Build workflow with conditional DPSK phases"""
    if dpsk_mode:
        phases = [
            BASE_PHASES[0],  # create_ap_groups
            *DPSK_PHASES[:3],  # identity_groups, dpsk_pools, passphrases
            # Modified create_ssids (DPSK type)
            PhaseDefinition(
                id="create_ssids",
                name="Create DPSK SSIDs",
                dependencies=["create_passphrases"],
                ...
            ),
            DPSK_PHASES[3],  # activate_dpsk_on_networks
            # ... rest of phases
        ]
    else:
        phases = BASE_PHASES.copy()
    # ...
```

#### 3. New Phase Executors
**Files** (new):
- `api/routers/per_unit_ssid/phases/create_identity_groups.py`
- `api/routers/per_unit_ssid/phases/create_dpsk_pools.py`
- `api/routers/per_unit_ssid/phases/create_passphrases.py`
- `api/routers/per_unit_ssid/phases/activate_dpsk.py`

**Phase: Create Identity Groups**
```python
async def execute(context: Dict[str, Any]) -> List[Task]:
    """Create identity groups for each unit"""
    units = context.get('units', [])
    options = context.get('options', {})
    prefix = options.get('ap_group_prefix', '')
    postfix = options.get('identity_group_postfix', '-IDG')

    idg_map = {}
    for unit in units:
        unit_number = unit['unit_number']
        idg_name = f"{prefix}{unit_number}{postfix}"

        # Use IdempotentHelper to find or create
        result = await helper.find_or_create_identity_group(
            tenant_id=tenant_id,
            name=idg_name,
            description=f"Identity group for unit {unit_number}"
        )
        idg_map[unit_number] = result.get('id')

    return [Task(..., output_data={'idg_map': idg_map})]
```

**Phase: Create DPSK Pools**
```python
async def execute(context: Dict[str, Any]) -> List[Task]:
    """Create DPSK pools for each unit, linked to identity groups"""
    idg_map = context['previous_phase_results']['create_identity_groups']['aggregated']['idg_map'][0]
    dpsk_settings = context.get('options', {}).get('dpsk_pool_settings', {})

    pool_map = {}
    for unit in units:
        unit_number = unit['unit_number']
        identity_group_id = idg_map[unit_number]
        pool_name = f"{prefix}{unit_number}{postfix}"

        pool = await r1_client.dpsk.create_dpsk_pool(
            identity_group_id=identity_group_id,
            name=pool_name,
            tenant_id=tenant_id,
            passphrase_length=dpsk_settings.get('passphrase_length', 12),
            passphrase_format=dpsk_settings.get('passphrase_format'),
            max_devices_per_passphrase=dpsk_settings.get('max_devices_per_passphrase', 0),
            expiration_days=dpsk_settings.get('expiration_days')
        )
        pool_map[unit_number] = {
            'pool_id': pool['id'],
            'identity_group_id': identity_group_id
        }

    return [Task(..., output_data={'pool_map': pool_map, 'idg_map': idg_map})]
```

**Phase: Create Passphrases**
```python
async def execute(context: Dict[str, Any]) -> List[Task]:
    """Create passphrases for each unit"""
    pool_map = context['previous_phase_results']['create_dpsk_pools']['aggregated']['pool_map'][0]

    passphrase_map = {}
    for unit in units:
        unit_number = unit['unit_number']
        pool_id = pool_map[unit_number]['pool_id']
        passphrases = unit.get('passphrases', [])

        created = []
        for pp in passphrases:
            result = await r1_client.dpsk.create_passphrase(
                pool_id=pool_id,
                tenant_id=tenant_id,
                passphrase=pp.get('passphrase'),
                user_name=pp.get('username'),
                user_email=pp.get('email'),
                description=pp.get('description'),
                vlan_id=pp.get('vlan_id') or unit.get('default_vlan')
            )
            created.append({
                'passphrase_id': result.get('id'),
                'identity_id': result.get('identityId'),
                'username': pp.get('username')
            })
        passphrase_map[unit_number] = created

    return [Task(..., output_data={'passphrase_map': passphrase_map})]
```

**Phase: Activate DPSK on Networks**
```python
async def execute(context: Dict[str, Any]) -> List[Task]:
    """Activate DPSK pools on their corresponding WiFi networks"""
    ssid_map = context['previous_phase_results']['create_ssids']['aggregated']['ssid_map'][0]
    pool_map = context['previous_phase_results']['create_dpsk_pools']['aggregated']['pool_map'][0]

    for unit in units:
        unit_number = unit['unit_number']
        network_id = ssid_map[unit_number]
        pool_id = pool_map[unit_number]['pool_id']

        await r1_client.dpsk.activate_dpsk_on_wifi_network(
            wifi_network_id=network_id,
            dpsk_service_id=pool_id,
            tenant_id=tenant_id
        )

    return [Task(...)]
```

#### 4. Modified Create SSIDs Phase
**File**: `api/routers/per_unit_ssid/phases/create_ssids.py`

```python
async def execute(context: Dict[str, Any]) -> List[Task]:
    dpsk_mode = context.get('options', {}).get('dpsk_mode', False)

    for unit in units:
        security_type = unit.get('security_type', 'WPA3')

        if dpsk_mode or security_type == 'DPSK':
            # Create DPSK-type network (no passphrase - DPSK provides it)
            ssid_result = await r1_client.networks.create_dpsk_wifi_network(
                tenant_id=tenant_id,
                venue_id=venue_id,
                name=network_name,
                ssid=ssid_name,
                vlan_id=int(default_vlan),
                description=f"Per-unit DPSK SSID for unit {unit_number}"
            )
        else:
            # Existing WPA2/WPA3 logic
            ssid_result = await r1_client.networks.create_wifi_network(...)
```

#### 5. Pre-flight VLAN Audit Service
**File**: `api/routers/per_unit_ssid/vlan_audit.py` (new)

```python
async def audit_venue_vlans(
    r1_client,
    tenant_id: str,
    venue_id: str
) -> dict:
    """
    Audit existing VLANs in use at a venue.

    Returns:
        {
            'used_vlans': Set[int],  # All VLANs already in use
            'vlan_details': [        # Details for each VLAN
                {'vlan': 10, 'source': 'ssid', 'name': 'Unit-101-WiFi'},
                {'vlan': 20, 'source': 'ssid', 'name': 'Unit-102-WiFi'},
                ...
            ]
        }
    """
    used_vlans = set()
    vlan_details = []

    # 1. Fetch all WiFi networks
    networks_response = await r1_client.networks.get_wifi_networks(tenant_id)
    networks = networks_response.get('data', [])

    for network in networks:
        # Check if activated on this venue
        venue_ap_groups = network.get('venueApGroups', [])
        for vag in venue_ap_groups:
            if vag.get('venueId') == venue_id:
                base_vlan = network.get('vlan')
                override_vlan = vag.get('vlanOverride')
                effective_vlan = override_vlan or base_vlan

                if effective_vlan:
                    used_vlans.add(int(effective_vlan))
                    vlan_details.append({
                        'vlan': int(effective_vlan),
                        'source': 'ssid',
                        'name': network.get('name'),
                        'ssid': network.get('ssid')
                    })
                break

    return {
        'used_vlans': used_vlans,
        'vlan_details': vlan_details
    }


def assign_missing_vlans(
    units: List[dict],
    used_vlans: Set[int],
    vlan_range: tuple = (10, 999)
) -> List[dict]:
    """
    Assign VLANs to units/passphrases that have omitted VLANs.

    Returns updated units with VLANs assigned.
    """
    min_vlan, max_vlan = vlan_range
    available = set(range(min_vlan, max_vlan + 1)) - used_vlans
    available_iter = iter(sorted(available))

    for unit in units:
        for pp in unit.get('passphrases', []):
            if not pp.get('vlan_id'):
                try:
                    pp['vlan_id'] = next(available_iter)
                    used_vlans.add(pp['vlan_id'])  # Track newly assigned
                except StopIteration:
                    raise ValueError(f"No available VLANs in range {vlan_range}")

    return units


def check_vlan_collisions(
    units: List[dict],
    existing_vlans: Set[int]
) -> List[dict]:
    """
    Check for VLAN collisions between CSV and existing venue VLANs.

    Returns list of warnings.
    """
    warnings = []
    csv_vlans = {}

    for unit in units:
        for pp in unit.get('passphrases', []):
            vlan = pp.get('vlan_id')
            if vlan:
                # Track which units use which VLANs
                if vlan not in csv_vlans:
                    csv_vlans[vlan] = []
                csv_vlans[vlan].append(unit['unit_number'])

                # Check collision with existing
                if vlan in existing_vlans:
                    warnings.append({
                        'type': 'existing_collision',
                        'vlan': vlan,
                        'unit': unit['unit_number'],
                        'message': f"VLAN {vlan} already in use by existing venue SSID"
                    })

    # Check same VLAN across different units (may be intentional)
    for vlan, unit_list in csv_vlans.items():
        if len(set(unit_list)) > 1:
            warnings.append({
                'type': 'cross_unit',
                'vlan': vlan,
                'units': list(set(unit_list)),
                'message': f"VLAN {vlan} used by multiple units: {', '.join(set(unit_list))}"
            })

    return warnings
```

#### 6. Network Service Extension
**File**: `api/r1api/services/networks.py`

```python
async def create_dpsk_wifi_network(
    self,
    tenant_id: str,
    venue_id: str,
    name: str,
    ssid: str,
    vlan_id: int = 1,
    description: str = None,
    wait_for_completion: bool = True
):
    """
    Create a DPSK WiFi network (no passphrase - DPSK pool provides authentication)
    """
    body = {
        "name": name,
        "description": description or f"DPSK Network {name}",
        "nwSubType": WifiNetworkType.DPSK,  # "dpsk"
        "wlanSettings": {
            "ssid": ssid,
            "wlanSecurity": "open",  # DPSK handles auth
            "vlan": vlan_id,
            "enabled": True
        },
        "venues": [{"id": venue_id}]
    }
    # ... rest of creation logic
```

---

### Frontend (React)

#### 1. Extended State Management
**File**: `src/pages/PerUnitSSID.tsx`

```typescript
// New state for DPSK mode
const [dpskMode, setDpskMode] = useState(false);
const [dpskPoolSettings, setDpskPoolSettings] = useState<DpskPoolSettings>({
  passphrase_length: 12,
  passphrase_format: 'KEYBOARD_FRIENDLY',
  max_devices_per_passphrase: 0,
  expiration_days: null
});
const [identityGroupPostfix, setIdentityGroupPostfix] = useState('-IDG');
const [dpskPoolPostfix, setDpskPoolPostfix] = useState('-DPSK');
```

#### 2. CSV Parsing Update
**File**: `src/pages/PerUnitSSID.tsx`

```typescript
// Detect DPSK mode from CSV
const detectDpskMode = (units: UnitConfig[]): boolean => {
  return units.some(u => u.security_type?.toUpperCase() === 'DPSK');
};

// Parse CSV with DPSK support
const parseCsvWithDpsk = (csvText: string) => {
  const rows = parseCSV(csvText);
  const unitMap = new Map<string, UnitConfig>();
  const allPassphrases = new Set<string>();  // For duplicate detection
  const duplicates: string[] = [];

  for (const row of rows) {
    const unitNumber = row.unit_number;
    const passphrase = row.ssid_password;  // Reuse existing column!

    // Check for duplicate passphrases
    if (passphrase) {
      if (allPassphrases.has(passphrase)) {
        duplicates.push(passphrase);
      }
      allPassphrases.add(passphrase);
    }

    if (!unitMap.has(unitNumber)) {
      unitMap.set(unitNumber, {
        unit_number: unitNumber,
        ssid_name: row.ssid_name,
        security_type: row.security_type,
        default_vlan: row.default_vlan,  // SSID's base VLAN
        ap_identifiers: [],
        passphrases: []
      });
    }

    const unit = unitMap.get(unitNumber)!;

    // Aggregate AP identifiers
    if (row.ap_serial_or_name) {
      unit.ap_identifiers.push(row.ap_serial_or_name);
    }

    // Aggregate passphrases (DPSK mode) - use ssid_password as the passphrase
    if (passphrase && row.security_type?.toUpperCase() === 'DPSK') {
      unit.passphrases.push({
        passphrase: passphrase,
        vlan_id: parseInt(row.default_vlan) || 1,  // Each passphrase has its own VLAN
        username: row.username || null,
        email: row.email || null,
        description: row.description || null
      });
    }
  }

  // Block if duplicates found
  if (duplicates.length > 0) {
    throw new Error(`Duplicate passphrases found (must be unique): ${duplicates.slice(0, 5).map(p => p.slice(0, 4) + '****').join(', ')}${duplicates.length > 5 ? '...' : ''}`);
  }

  return Array.from(unitMap.values());
};
```

#### 3. UI Components Update

**DPSK Mode Toggle & Settings Panel**:
```tsx
{/* DPSK Mode Section */}
<div className="mb-4 p-4 bg-indigo-50 border border-indigo-200 rounded-lg">
  <div className="flex items-start gap-3">
    <input
      type="checkbox"
      id="dpskMode"
      checked={dpskMode}
      onChange={(e) => setDpskMode(e.target.checked)}
      className="mt-1 h-4 w-4 text-indigo-600"
    />
    <div className="flex-1">
      <label htmlFor="dpskMode" className="block text-sm font-medium text-gray-700 cursor-pointer">
        Enable DPSK Mode
        <span className="ml-2 text-xs font-normal text-indigo-600 bg-indigo-100 px-2 py-0.5 rounded">
          Auto-detected from CSV
        </span>
      </label>
      <p className="text-xs text-gray-500 mt-1">
        Creates Identity Groups, DPSK Pools, and Passphrases for each unit.
        Each passphrase provides unique WiFi credentials per user/device.
      </p>

      {dpskMode && (
        <div className="mt-4 space-y-4">
          {/* Resource Naming */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-600">Identity Group Postfix</label>
              <input
                type="text"
                value={identityGroupPostfix}
                onChange={(e) => setIdentityGroupPostfix(e.target.value)}
                className="w-full px-2 py-1 text-sm border rounded"
                placeholder="-IDG"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600">DPSK Pool Postfix</label>
              <input
                type="text"
                value={dpskPoolPostfix}
                onChange={(e) => setDpskPoolPostfix(e.target.value)}
                className="w-full px-2 py-1 text-sm border rounded"
                placeholder="-DPSK"
              />
            </div>
          </div>

          {/* Pool Settings */}
          <div className="bg-white p-3 rounded border">
            <h5 className="text-sm font-medium mb-2">DPSK Pool Settings (Applied to All)</h5>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-gray-600">Max Devices per Passphrase</label>
                <input
                  type="number"
                  min="0"
                  value={dpskPoolSettings.max_devices_per_passphrase}
                  onChange={(e) => setDpskPoolSettings({
                    ...dpskPoolSettings,
                    max_devices_per_passphrase: parseInt(e.target.value) || 0
                  })}
                  className="w-full px-2 py-1 text-sm border rounded"
                />
                <span className="text-xs text-gray-400">0 = unlimited</span>
              </div>
              <div>
                <label className="block text-xs text-gray-600">Expiration (days)</label>
                <input
                  type="number"
                  min="0"
                  value={dpskPoolSettings.expiration_days || ''}
                  onChange={(e) => setDpskPoolSettings({
                    ...dpskPoolSettings,
                    expiration_days: e.target.value ? parseInt(e.target.value) : null
                  })}
                  placeholder="No expiration"
                  className="w-full px-2 py-1 text-sm border rounded"
                />
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  </div>
</div>
```

**Updated CSV Template Buttons**:
```tsx
<button
  onClick={() => {
    const template = `unit_number,ssid_name,ssid_password,security_type,default_vlan,username,email,description,ap_serial_or_name
101,Unit-101-WiFi,SecurePass101!,DPSK,10,john.doe,john@example.com,Primary resident,AP-101-Living
101,Unit-101-WiFi,GuestPass101!,DPSK,99,guest-101,,Guest access (isolated VLAN),
102,Unit-102-WiFi,SecurePass102!,DPSK,20,jane.smith,jane@example.com,Primary resident,AP-102-Living
102,Unit-102-WiFi,Family102Pass!,DPSK,20,family-102,,Family member (same VLAN),
102,Unit-102-WiFi,GuestPass102!,DPSK,99,guest-102,,Guest access (isolated VLAN),`;
    downloadCSV(template, 'per-unit-dpsk.csv');
  }}
  className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 text-sm font-medium"
>
  📥 DPSK Template
</button>
```

#### 4. VLAN Tracking Display

```tsx
{/* VLAN Usage Summary */}
{units.length > 0 && (
  <div className="mt-4 p-3 bg-gray-50 rounded border">
    <h5 className="text-sm font-medium mb-2">VLAN Usage Summary</h5>
    <div className="flex flex-wrap gap-2">
      {getVlanUsageSummary(units).map(({vlan, count, units: unitList}) => (
        <span
          key={vlan}
          className={`px-2 py-1 rounded text-xs font-medium ${
            count > 1 ? 'bg-amber-100 text-amber-800' : 'bg-green-100 text-green-800'
          }`}
          title={`Units: ${unitList.join(', ')}`}
        >
          VLAN {vlan}: {count} unit{count > 1 ? 's' : ''}
          {count > 1 && ' ⚠️'}
        </span>
      ))}
    </div>
  </div>
)}
```

---

## Testing Strategy

### 1. Test Data Generation Script
Create a Python script to generate test CSVs with the passphrase distribution:

```python
# api/scripts/generate_dpsk_test_data.py
import random
import string
import csv
from io import StringIO

def generate_passphrase(length=12):
    """Generate a unique passphrase"""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def generate_dpsk_test_csv(num_units=200, include_guest_vlans=True):
    """
    Generate test CSV with passphrase distribution:
    - 75% (150): 1 passphrase
    - 17.5% (35): 2 passphrases
    - 5% (10): 3 passphrases
    - 1.5% (3): 4 passphrases
    - 1% (2): 5 passphrases

    Multi-VLAN scenario:
    - Primary users: Unit-specific VLANs (10-209)
    - Guest passphrases: Shared guest VLAN (999)
    """
    distribution = [1]*150 + [2]*35 + [3]*10 + [4]*3 + [5]*2
    random.shuffle(distribution)

    used_passphrases = set()
    rows = []

    for i, pp_count in enumerate(distribution[:num_units], start=1):
        unit_number = f"{i:03d}"
        unit_vlan = 10 + i  # Unique VLAN per unit (10-209)
        guest_vlan = 999    # Shared guest VLAN

        for pp_idx in range(pp_count):
            # Generate unique passphrase
            passphrase = generate_passphrase()
            while passphrase in used_passphrases:
                passphrase = generate_passphrase()
            used_passphrases.add(passphrase)

            # Determine VLAN: Primary users get unit VLAN, guests get shared VLAN
            is_guest = include_guest_vlans and pp_idx == pp_count - 1 and pp_count > 1
            vlan = guest_vlan if is_guest else unit_vlan

            rows.append({
                'unit_number': unit_number,
                'ssid_name': f'Unit-{unit_number}-WiFi',
                'ssid_password': passphrase,  # Uses existing column!
                'security_type': 'DPSK',
                'default_vlan': vlan,
                'username': f'guest-{unit_number}' if is_guest else f'user-{unit_number}-{pp_idx+1}',
                'email': '' if is_guest else f'user{unit_number}_{pp_idx+1}@test.com',
                'description': 'Guest access' if is_guest else ('Primary' if pp_idx == 0 else f'Family {pp_idx+1}'),
                'ap_serial_or_name': f'AP-{unit_number}' if pp_idx == 0 else ''
            })

    return rows

def write_csv(rows, filename='dpsk_test_200_units.csv'):
    """Write rows to CSV file"""
    fieldnames = ['unit_number', 'ssid_name', 'ssid_password', 'security_type',
                  'default_vlan', 'username', 'email', 'description', 'ap_serial_or_name']

    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

    with open(filename, 'w') as f:
        f.write(output.getvalue())

    print(f"Generated {filename} with {len(rows)} rows across {len(set(r['unit_number'] for r in rows))} units")

if __name__ == '__main__':
    rows = generate_dpsk_test_csv(num_units=200, include_guest_vlans=True)
    write_csv(rows)
```

### 2. Unit Tests
- CSV parsing with DPSK columns
- Passphrase aggregation by unit
- VLAN tracking and collision detection
- Phase data flow validation

### 3. Integration Tests
- Full workflow execution (mock R1 API)
- Parallel execution with DPSK mode
- Error handling (pool creation failures, passphrase limits)

---

## Implementation Order

### Phase 1: Pre-flight Validation (Critical Path)
1. [ ] Create `vlan_audit.py` - VLAN audit service
2. [ ] Add duplicate passphrase detection to CSV parser
3. [ ] Implement VLAN auto-assignment logic
4. [ ] Add VLAN collision detection and warnings
5. [ ] Create pre-flight validation endpoint

### Phase 2: Backend DPSK Phases
6. [ ] Add new Pydantic models to router (DpskPoolSettings, PassphraseConfig, etc.)
7. [ ] Create `create_identity_groups.py` phase executor (with idempotency)
8. [ ] Create `create_dpsk_pools.py` phase executor
9. [ ] Create `create_passphrases.py` phase executor
10. [ ] Create `activate_dpsk.py` phase executor
11. [ ] Add `create_dpsk_wifi_network` to NetworksService
12. [ ] Update workflow_definition.py for conditional DPSK phases
13. [ ] Modify create_ssids.py for DPSK network type

### Phase 3: Frontend Integration
14. [ ] Add DPSK state and settings to PerUnitSSID.tsx
15. [ ] Update CSV parsing for DPSK columns + passphrase aggregation
16. [ ] Add duplicate passphrase validation (block with error)
17. [ ] Add DPSK Mode toggle and settings UI
18. [ ] Add DPSK CSV template download button
19. [ ] Add VLAN audit display (existing + CSV VLANs)
20. [ ] Add VLAN collision warnings UI
21. [ ] Update request payload construction

### Phase 4: Testing & Polish
22. [ ] Create test data generation script (200 units, weighted passphrase distribution)
23. [ ] Test with 200 units at scale
24. [ ] Test VLAN auto-assignment
25. [ ] Test idempotency (re-run with existing resources)
26. [ ] Add error handling and edge cases
27. [ ] Update educational documentation in UI

---

## Design Decisions (Confirmed)

1. **Idempotency**: ✅ YES - Check for existing identity groups/pools with matching names before creating. Use IdempotentHelper pattern from Cloudpath workflow.

2. **Cleanup on Failure**: ❌ NO - User will implement a separate "destroy" function later. No automatic cleanup needed.

3. **CSV Validation - Duplicate Passphrases**: ✅ YES - Validate passphrase uniqueness upfront during CSV parsing. **Block execution** if duplicates found (passphrases must be unique!).

4. **VLAN Auto-Assignment**: ✅ YES (Required Now) - Implement automatic VLAN assignment from configurable range when omitted in CSV. Track used VLANs from existing venue networks + earlier CSV rows.

5. **Orchestrator Integration**: ❌ NO - Completely out of scope. DPSK Orchestrator is a separate project. User will manually configure orchestration independently.

## Future Enhancements (Out of Scope)

- Destroy/cleanup function for created resources
- Bulk passphrase import from external sources
- Policy set attachment automation
