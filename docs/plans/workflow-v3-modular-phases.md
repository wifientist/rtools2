# Workflow V3: Modular Phase Execution

## Problem Statement

Current V2 workflow limitations:
1. Workflows run as monolithic sequences - can't run just one phase
2. Validation outputs are tied to a specific job execution
3. No way to say "I have existing resources, just run the AP assignment phase"
4. Re-running requires full workflow re-execution

**User Need**: "I don't want to run the whole workflow, I need to just map the unit number to the AP groups."

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Storage | Redis only | Simple, 7-day TTL sufficient for migrations |
| Plan lifespan | 7 days | One-time migration, then move on |
| Staleness handling | Refresh on demand | Re-audit R1 state, revalidate as needed |
| Partial execution | Yes | Single unit, subset, or all units |
| Manual overrides | No | Focus on comprehensive R1 entity tracking instead |

## Proposed Solution: Persistent Discovery + Composable Phases

### Core Concept: Venue Import Plan

Separate **Discovery** (what exists) from **Execution** (what to do):

```
┌─────────────────────────────────────────────────────────────────┐
│                     VENUE IMPORT PLAN                           │
│  (Persistent snapshot of venue state + import configuration)    │
├─────────────────────────────────────────────────────────────────┤
│  Discovery Data (R1 State):                                     │
│  ├─ all_venue_aps: [{serial, name, mac, ap_group_id}, ...]     │
│  ├─ existing_ap_groups: {name: id, ...}                        │
│  ├─ existing_networks: [{id, name, ssid, dpsk_service_id}, ...]│
│  ├─ existing_identity_groups: {name: id, ...}                  │
│  ├─ existing_dpsk_pools: {name: {id, passphrase_count}, ...}   │
│  └─ existing_passphrases: {passphrase_value: id, ...}          │
├─────────────────────────────────────────────────────────────────┤
│  Import Configuration:                                          │
│  ├─ cloudpath_data: {...}  (original import JSON)              │
│  ├─ options: {ssid_mode, ap_group_prefix, ...}                 │
│  └─ parsed_passphrases: [...]                                  │
├─────────────────────────────────────────────────────────────────┤
│  Unit Mappings (Planned State):                                 │
│  └─ units: {                                                    │
│       "unit_101": {                                             │
│         plan: {ap_group_name, ssid_name, network_name, ...}    │
│         resolved: {ap_group_id, network_id, dpsk_pool_id, ...} │
│         ap_assignments: ["serial1", "serial2"]                 │
│         passphrases: [...]                                     │
│       }                                                         │
│     }                                                           │
├─────────────────────────────────────────────────────────────────┤
│  Execution State:                                               │
│  ├─ phases_completed: ["create_identity_group", ...]           │
│  ├─ phases_failed: []                                          │
│  └─ last_execution: {job_id, timestamp, phases_run}            │
└─────────────────────────────────────────────────────────────────┘
```

### Key Changes from V2

| Aspect | V2 (Current) | V3 (Proposed) |
|--------|--------------|---------------|
| Discovery | Per-job, ephemeral | Persistent, reusable |
| Phase execution | Full workflow only | Any subset of phases |
| State storage | Job-scoped Redis keys | Plan-scoped Redis/DB |
| Lifespan | 7 days (job TTL) | User-controlled |
| Re-execution | New job, re-discover | Same plan, pick phases |

### Comprehensive R1 Entity Inventory

The discovery phase must capture ALL R1 entities that could be involved in a migration:

```
┌─────────────────────────────────────────────────────────────────┐
│                    R1 ENTITY INVENTORY                          │
├─────────────────────────────────────────────────────────────────┤
│  VENUE CONTEXT                                                  │
│  ├─ venue: {id, name, address}                                 │
│  └─ tenant: {id, name, ec_type}                                │
├─────────────────────────────────────────────────────────────────┤
│  ACCESS POINTS                                                  │
│  ├─ aps: [{                                                    │
│  │     serial, name, mac, model, status,                       │
│  │     ap_group_id, ap_group_name,                             │
│  │     venue_id, firmware_version                              │
│  │   }, ...]                                                   │
│  └─ ap_groups: [{                                              │
│       id, name, venue_id, ap_count,                            │
│       is_default, description                                  │
│     }, ...]                                                    │
├─────────────────────────────────────────────────────────────────┤
│  WIFI NETWORKS                                                  │
│  └─ networks: [{                                               │
│       id, name, ssid, security_type,                           │
│       dpsk_service_id, dpsk_service_name,                      │
│       venue_ap_groups: [{venue_id, ap_group_ids, is_all}],     │
│       is_activated, vlan_id                                    │
│     }, ...]                                                    │
├─────────────────────────────────────────────────────────────────┤
│  DPSK SERVICES (Pools)                                          │
│  └─ dpsk_pools: [{                                             │
│       id, name, description,                                   │
│       identity_group_id, identity_group_name,                  │
│       passphrase_length, passphrase_format,                    │
│       max_devices, expiration_days,                            │
│       passphrase_count                                         │
│     }, ...]                                                    │
├─────────────────────────────────────────────────────────────────┤
│  DPSK PASSPHRASES                                               │
│  └─ passphrases: [{                                            │
│       id, pool_id, passphrase_value,                           │
│       username, mac, vlan_id,                                  │
│       device_count, max_devices,                               │
│       expiration, status                                       │
│     }, ...]                                                    │
│  └─ passphrase_index: {passphrase_value: id, ...}  (for dedup) │
├─────────────────────────────────────────────────────────────────┤
│  IDENTITY GROUPS                                                │
│  └─ identity_groups: [{                                        │
│       id, name, description,                                   │
│       identity_count                                           │
│     }, ...]                                                    │
├─────────────────────────────────────────────────────────────────┤
│  IDENTITIES                                                     │
│  └─ identities: [{                                             │
│       id, name, identity_group_id,                             │
│       mac, vlan, description,                                  │
│       created_at                                               │
│     }, ...]                                                    │
├─────────────────────────────────────────────────────────────────┤
│  ADAPTIVE POLICIES                                              │
│  ├─ policy_sets: [{                                            │
│  │     id, name, description,                                  │
│  │     policy_count                                            │
│  │   }, ...]                                                   │
│  └─ policies: [{                                               │
│       id, name, policy_set_id,                                 │
│       conditions: [...],                                       │
│       actions: [...],                                          │
│       priority                                                 │
│     }, ...]                                                    │
├─────────────────────────────────────────────────────────────────┤
│  RADIUS ATTRIBUTE GROUPS                                        │
│  └─ radius_groups: [{                                          │
│       id, name, attributes: [...]                              │
│     }, ...]                                                    │
└─────────────────────────────────────────────────────────────────┘
```

### New Data Model

```python
class VenueImportPlan(BaseModel):
    """Persistent import plan for a venue."""
    id: str  # UUID
    venue_id: str
    tenant_id: str
    name: str  # User-friendly name
    created_at: datetime
    updated_at: datetime

    # Comprehensive R1 state snapshot
    inventory: R1Inventory

    # Import configuration (Cloudpath data + options)
    import_config: ImportConfig

    # Unit mappings (planned + resolved state)
    units: Dict[str, UnitMapping]

    # Execution tracking
    execution_state: ExecutionState


class R1Inventory(BaseModel):
    """Complete snapshot of R1 venue state."""
    discovered_at: datetime
    discovery_duration_ms: int

    # Context
    venue: VenueInfo
    tenant: TenantInfo

    # Access Points
    aps: List[APInfo]
    ap_groups: List[APGroupInfo]

    # Networks
    networks: List[NetworkInfo]

    # DPSK
    dpsk_pools: List[DPSKPoolInfo]
    passphrases: List[PassphraseInfo]
    passphrase_index: Dict[str, str]  # passphrase_value -> id

    # Identity
    identity_groups: List[IdentityGroupInfo]
    identities: List[IdentityInfo]

    # Policies
    policy_sets: List[PolicySetInfo]
    policies: List[PolicyInfo]
    radius_groups: List[RadiusGroupInfo]

    # Quick lookup helpers (built from lists)
    @property
    def ap_by_serial(self) -> Dict[str, APInfo]: ...
    @property
    def ap_by_name(self) -> Dict[str, APInfo]: ...
    @property
    def ap_group_by_name(self) -> Dict[str, str]: ...  # name -> id
    @property
    def network_by_ssid(self) -> Dict[str, NetworkInfo]: ...
    @property
    def dpsk_pool_by_name(self) -> Dict[str, str]: ...  # name -> id
    @property
    def identity_group_by_name(self) -> Dict[str, str]: ...  # name -> id


class ExecutionState(BaseModel):
    """Track what's been executed on this plan."""
    # Global phase tracking
    phases_completed: Dict[str, PhaseExecutionRecord]  # phase_id -> record
    phases_failed: Dict[str, str]  # phase_id -> error message

    # Per-unit phase tracking (for selective execution)
    unit_phases: Dict[str, UnitPhaseState]  # unit_id -> state

    # Execution history
    executions: List[ExecutionRecord]  # All execution runs


class UnitPhaseState(BaseModel):
    """Track phase completion for a specific unit."""
    unit_id: str
    completed_phases: List[str]
    failed_phases: Dict[str, str]  # phase_id -> error
    current_phase: Optional[str]


class ExecutionRecord(BaseModel):
    """Record of a single execution run."""
    id: str
    started_at: datetime
    completed_at: Optional[datetime]
    phases_requested: List[str]
    units_requested: Optional[List[str]]  # None = all units
    status: str  # running, completed, failed, partial
    results: Dict[str, Any]
```

### Phase Invocation Modes

#### Mode 1: Full Workflow (unchanged from V2)
```python
# Create plan + run all phases
job = await brain.create_and_run_workflow(
    workflow_name="cloudpath_import",
    input_data=cloudpath_json,
    options={...}
)
```

#### Mode 2: Create/Refresh Plan Only
```python
# Just discover and plan - no execution
plan = await brain.create_import_plan(
    venue_id=venue_id,
    input_data=cloudpath_json,
    options={...}
)
# Returns plan_id, stores in Redis/DB
```

#### Mode 3: Execute Specific Phases
```python
# Run selected phases using existing plan
result = await brain.execute_phases(
    plan_id=plan_id,
    phases=["assign_aps_to_groups"],  # Just this one phase
    units=["unit_101", "unit_102"],   # Optional: specific units only
)
```

#### Mode 4: Re-discover (Refresh Plan)
```python
# Refresh R1 state without changing config
plan = await brain.refresh_discovery(plan_id=plan_id)
```

### API Endpoints

```python
# Plan management
POST   /api/workflows/cloudpath/plans
       → Create new import plan (discover + validate)

GET    /api/workflows/cloudpath/plans
       → List plans for venue

GET    /api/workflows/cloudpath/plans/{plan_id}
       → Get plan details

POST   /api/workflows/cloudpath/plans/{plan_id}/refresh
       → Re-discover R1 state

DELETE /api/workflows/cloudpath/plans/{plan_id}
       → Delete plan

# Phase execution
POST   /api/workflows/cloudpath/plans/{plan_id}/execute
       Body: {
         "phases": ["create_ap_groups", "assign_aps"],
         "units": ["unit_101"],  # optional
         "options": {}  # runtime overrides
       }
       → Execute specific phases

GET    /api/workflows/cloudpath/plans/{plan_id}/phases
       → Get phase status (what's been run, what's pending)

# Legacy (backwards compatible)
POST   /api/workflows/cloudpath/run
       → Full workflow (creates ephemeral plan internally)
```

### Phase Dependency Validation

When executing specific phases, validate that dependencies are satisfied:

```python
async def execute_phases(self, plan_id: str, phases: List[str], units: List[str] = None):
    plan = await self.get_plan(plan_id)

    # Build dependency graph
    graph = DependencyGraph(self.workflow.phases)

    # Check each requested phase
    for phase_id in phases:
        phase = self.workflow.get_phase(phase_id)

        for dep_id in phase.depends_on:
            # Check if dependency was already executed
            if dep_id not in plan.execution_state.phases_completed:
                # Check if dependency outputs exist in resolved state
                if not self._has_required_outputs(plan, dep_id):
                    raise DependencyNotSatisfied(
                        f"Phase '{phase_id}' requires '{dep_id}' to be executed first, "
                        f"or its outputs to be manually provided"
                    )

    # Execute phases in dependency order
    ...
```

### Storage: Redis Only

**Decision**: Redis with 7-day TTL (consistent with V2 job storage)

```
Redis Keys:
  workflow:v3:plans:{plan_id}              → Full VenueImportPlan JSON
  workflow:v3:plans:{plan_id}:inventory    → R1Inventory (can be large)
  workflow:v3:plans:{plan_id}:units:{id}   → Per-unit UnitMapping (atomic updates)
  workflow:v3:venue:{venue_id}:plans       → Set of plan_ids for this venue
  workflow:v3:plans:active                 → Set of currently executing plan_ids

TTL: 7 days (sufficient for one-time migrations)
```

**Rationale**: Migrations are one-time events. 7 days is enough to complete the work. If needed longer, user can refresh/recreate the plan.

### Architecture: Engine vs. Workflows

**CRITICAL**: The workflow engine must be completely decoupled from specific workflows.

```
┌─────────────────────────────────────────────────────────────────┐
│                     WORKFLOW ENGINE (Generic)                    │
│                     api/workflow/v3/engine/                      │
├─────────────────────────────────────────────────────────────────┤
│  PlanStateManager        - Redis storage for any plan           │
│  DependencyGraph         - Build/traverse phase dependencies    │
│  DependencyResolver      - Check if phase can run (generic)     │
│  ExecutionEngine         - Run phases with concurrency control  │
│  PhaseExecutor (base)    - Abstract base class for all phases   │
│  PhaseRegistry           - Dynamic phase loading                │
│  EventPublisher          - SSE events for any workflow          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ uses
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                 WORKFLOW DEFINITION (Per-Workflow)               │
│                 api/workflow/workflows/cloudpath.py              │
├─────────────────────────────────────────────────────────────────┤
│  Workflow(                                                       │
│    name="cloudpath_import",                                     │
│    inventory_service=R1InventoryService,  # Pluggable!          │
│    phases=[...],                                                │
│    field_mappings={...},  # How to resolve inputs from inventory│
│  )                                                              │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ defines
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              PHASE IMPLEMENTATIONS (Per-Workflow)                │
│              api/workflow/phases/cloudpath/                      │
├─────────────────────────────────────────────────────────────────┤
│  ValidateCloudpathPhase   - Parse Cloudpath JSON                │
│  CreateIdentityGroupPhase - R1-specific API calls               │
│  CreateDPSKPoolPhase      - R1-specific API calls               │
│  CreateAPGroupsPhase      - R1-specific API calls               │
│  ...                                                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ uses
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              INVENTORY SERVICE (Per-Platform)                    │
│              api/services/r1_inventory.py                        │
├─────────────────────────────────────────────────────────────────┤
│  R1InventoryService                                             │
│  ├─ discover_venue() → R1Inventory                              │
│  ├─ refresh_entity()                                            │
│  └─ lookup_resource(type, name) → id                            │
│                                                                 │
│  # Could have others: ArubaCentralInventoryService, etc.        │
└─────────────────────────────────────────────────────────────────┘
```

**Engine Components (Generic)**:

```python
# api/workflow/v3/engine/base.py

class WorkflowDefinition(BaseModel):
    """Abstract workflow definition."""
    name: str
    phases: List[PhaseDefinition]

    # Pluggable inventory service
    inventory_service: Type[InventoryService]

    # How to map phase inputs to inventory lookups
    # e.g., {"identity_group_id": ("identity_groups", "identity_group_name")}
    field_mappings: Dict[str, Tuple[str, str]] = {}


class InventoryService(ABC):
    """Abstract inventory service - implemented per platform."""

    @abstractmethod
    async def discover(self, context: Dict[str, Any]) -> Inventory:
        """Fetch all resources from the platform."""
        pass

    @abstractmethod
    async def lookup(self, resource_type: str, name: str) -> Optional[str]:
        """Look up a resource ID by type and name."""
        pass

    @abstractmethod
    def get_resource_types(self) -> List[str]:
        """Return list of resource types this service handles."""
        pass


class Inventory(BaseModel):
    """Generic inventory - workflow-specific subclasses add fields."""
    discovered_at: datetime
    resources: Dict[str, List[Dict[str, Any]]]  # type -> list of resources

    def lookup(self, resource_type: str, name_field: str, name: str) -> Optional[str]:
        """Generic lookup by resource type and name."""
        for resource in self.resources.get(resource_type, []):
            if resource.get(name_field) == name:
                return resource.get('id')
        return None
```

**Workflow-Specific Implementation**:

```python
# api/workflow/workflows/cloudpath.py

class R1Inventory(Inventory):
    """R1-specific inventory with typed fields."""
    aps: List[APInfo] = []
    ap_groups: List[APGroupInfo] = []
    networks: List[NetworkInfo] = []
    dpsk_pools: List[DPSKPoolInfo] = []
    identity_groups: List[IdentityGroupInfo] = []
    # ... etc


class R1InventoryService(InventoryService):
    """R1-specific inventory fetching."""

    async def discover(self, context: Dict[str, Any]) -> R1Inventory:
        venue_id = context['venue_id']
        tenant_id = context['tenant_id']
        # Fetch all R1 resources...

    async def lookup(self, resource_type: str, name: str) -> Optional[str]:
        # Type-specific lookups...


CloudpathImportWorkflow = WorkflowDefinition(
    name="cloudpath_import",
    inventory_service=R1InventoryService,
    phases=[
        PhaseDefinition(id="validate_and_plan", ...),
        PhaseDefinition(id="create_identity_group", ...),
        # ...
    ],
    field_mappings={
        # input_field -> (inventory_resource_type, name_field_in_plan)
        "identity_group_id": ("identity_groups", "identity_group_name"),
        "dpsk_pool_id": ("dpsk_pools", "dpsk_pool_name"),
        "ap_group_id": ("ap_groups", "ap_group_name"),
        "network_id": ("networks", "ssid_name"),
    }
)
```

**Generic Dependency Resolver**:

```python
# api/workflow/v3/engine/resolver.py

class DependencyResolver:
    """Generic resolver - uses workflow's field_mappings."""

    def __init__(self, workflow: WorkflowDefinition):
        self.workflow = workflow
        self.field_mappings = workflow.field_mappings

    def _lookup_in_inventory(
        self,
        field_name: str,
        inventory: Inventory,
        unit_plan: UnitPlan
    ) -> Optional[str]:
        """Generic inventory lookup using workflow's field_mappings."""

        if field_name not in self.field_mappings:
            return None

        resource_type, plan_field = self.field_mappings[field_name]
        name_value = getattr(unit_plan, plan_field, None)

        if not name_value:
            return None

        # Use generic inventory lookup
        return inventory.lookup(resource_type, 'name', name_value)
```

**Benefits of This Separation**:

1. **New workflows don't touch engine code** - just define phases + inventory service
2. **Different platforms** - could support Aruba Central, Meraki, etc. with different inventory services
3. **Testable** - mock inventory service for unit tests
4. **Reusable patterns** - cleanup workflow, audit workflow, etc. all use same engine

### Inventory-Driven Reuse (Zero Redundant API Calls)

**Problem with V2**: Each phase checks R1 for existing resources at execution time:
```python
# Current approach - makes API call during execution
existing = await self._find_existing_pool(pool_name)
if existing:
    return self.Outputs(dpsk_pool_id=existing['id'], reused=True)
```

**V3 Approach**: Inventory is fetched ONCE upfront, phases use cached data:
```python
# V3 - no API call, just lookup in pre-fetched inventory
existing_id = self.context.inventory.lookup('dpsk_pools', pool_name)
if existing_id:
    return self.Outputs(dpsk_pool_id=existing_id, reused=True)
```

**Phase Execution Logic** (generic in engine):

```python
# api/workflow/v3/engine/executor.py

async def execute_phase(self, phase_id: str, unit: UnitMapping) -> PhaseOutputs:
    """Execute a phase with inventory-aware reuse."""

    # Get the planned resource name from unit.plan
    phase = self.workflow.get_phase(phase_id)
    executor = self.get_executor(phase_id)

    # Check if this phase's output already exists in inventory
    for output_field, mapping in self.workflow.field_mappings.items():
        resource_type, name_field = mapping

        # Get the planned name (e.g., "Cloudpath-IDG")
        planned_name = getattr(unit.plan, name_field, None)
        if not planned_name:
            continue

        # Check if it exists in inventory
        existing_id = self.inventory.lookup(resource_type, 'name', planned_name)
        if existing_id:
            # Resource exists! Skip creation, just populate resolved
            unit.resolved.set(output_field, existing_id)
            await self.emit(f"Reusing existing {resource_type}: {planned_name}")
            # Mark phase as SATISFIED (not COMPLETE - we didn't execute it)
            return PhaseResult(status='satisfied', reused=True, resource_id=existing_id)

    # Resource doesn't exist - actually execute the phase
    inputs = self._build_inputs(phase_id, unit)
    outputs = await executor.execute(inputs)
    return PhaseResult(status='complete', outputs=outputs)
```

### Visualization: Create vs Reuse Summary

**Per-Phase Summary** (shown on each phase card):

```
┌─────────────────────────────────────────┐
│  create_ap_groups                       │
│  ⏳ READY                               │
│                                         │
│  Summary: 12 new / 42 existing          │
│  ══════════════════════════             │ (progress bar: 42/54 = 78% reuse)
│                                         │
│  New:      Unit-101, Unit-102, ...      │
│  Existing: Unit-103, Unit-104, ...      │
└─────────────────────────────────────────┘
```

**Workflow-Level Summary** (header/sidebar):

```
┌─────────────────────────────────────────────────────────────────┐
│  WORKFLOW SUMMARY                                               │
│                                                                 │
│  Phase                      New    Existing   Total   API Calls │
│  ─────────────────────────────────────────────────────────────  │
│  create_identity_group       0         1        1         0     │
│  create_dpsk_pool            0         1        1         0     │
│  create_ap_groups           12        42       54        12     │
│  assign_aps_to_groups       12         0       12        12     │
│  create_networks            12        42       54        12     │
│  create_passphrases        150       850     1000       150     │
│  ─────────────────────────────────────────────────────────────  │
│  TOTAL                     186       936     1122       186     │
│                                                                 │
│  Estimated time: ~3 min (186 API calls @ ~1s each)              │
│  Reuse rate: 83%                                                │
└─────────────────────────────────────────────────────────────────┘
```

**Data Model for Summary**:

```python
class PhaseResourceSummary(BaseModel):
    """Summary of resources for a phase."""
    phase_id: str
    resource_type: str

    # Counts
    total: int
    new: int        # Will be created
    existing: int   # Already in R1, will reuse

    # Details (for drill-down)
    new_items: List[str]       # Names of resources to create
    existing_items: List[str]  # Names of resources to reuse

    # Estimated API calls
    estimated_api_calls: int

    @property
    def reuse_rate(self) -> float:
        return self.existing / self.total if self.total > 0 else 0


class WorkflowSummary(BaseModel):
    """Overall workflow resource summary."""
    phases: List[PhaseResourceSummary]

    @property
    def total_new(self) -> int:
        return sum(p.new for p in self.phases)

    @property
    def total_existing(self) -> int:
        return sum(p.existing for p in self.phases)

    @property
    def total_api_calls(self) -> int:
        return sum(p.estimated_api_calls for p in self.phases)

    @property
    def overall_reuse_rate(self) -> float:
        total = self.total_new + self.total_existing
        return self.total_existing / total if total > 0 else 0
```

**API Endpoint**:

```python
GET /api/v3/workflows/plans/{id}/summary

Response:
{
  "phases": [
    {
      "phase_id": "create_ap_groups",
      "resource_type": "ap_groups",
      "total": 54,
      "new": 12,
      "existing": 42,
      "new_items": ["Unit-101", "Unit-102", ...],
      "existing_items": ["Unit-103", "Unit-104", ...],
      "estimated_api_calls": 12
    },
    ...
  ],
  "total_new": 186,
  "total_existing": 936,
  "total_api_calls": 186,
  "overall_reuse_rate": 0.83,
  "estimated_duration_seconds": 186
}
```

**Key Insight**: With V3, we know BEFORE execution exactly what will be created vs reused. No surprises, no wasted API calls checking existence during execution.

### Migration Path

1. **V2 Compatibility**: Keep V2 brain working unchanged
2. **V3 Brain**: New `WorkflowBrainV3` with plan-based execution
3. **Gradual Migration**: Workflows can opt-in to V3 model
4. **API Versioning**: `/api/v2/workflows/...` vs `/api/v3/workflows/...`

### Phase Dependency Visualization

**Vertical Flow Layout** (replacing horizontal swimlane):

Current V2 horizontal layout works for "watch it run" but doesn't help with:
- Understanding what's blocking a specific phase
- Seeing which dependencies are already satisfied in R1
- Deciding which phases to run next

Vertical layout advantages:
- Natural top-to-bottom reading flow
- More space for showing inputs/outputs per phase
- Can show branching (parallel phases) as columns
- Easier to highlight "blocked" vs "ready" states
- Mobile-friendly

**Visual Design** showing phases with dependency status:

```
┌─────────────────────────────────────────────────────────────┐
│  validate_and_plan                              ✅ COMPLETE │
│  └─ Outputs: unit_mappings, inventory, pool_config         │
└─────────────────────────┬───────────────────────────────────┘
                          │
          ┌───────────────┴───────────────┐
          ▼                               ▼
┌─────────────────────────┐   ┌─────────────────────────────┐
│  create_identity_group  │   │  create_ap_groups           │
│  ✅ SATISFIED           │   │  ⏳ READY                   │
│                         │   │                             │
│  Needs:                 │   │  Needs:                     │
│  └─ identity_group_name │   │  └─ ap_group_name (planned) │
│     ✅ "Cloudpath-IDG"  │   │     ✅ "Unit-101"           │
│                         │   │  └─ venue_id                │
│  Provides:              │   │     ✅ "venue-abc"          │
│  └─ identity_group_id   │   │                             │
│     ✅ "abc-123" EXISTS │   │  Provides:                  │
│        (from R1)        │   │  └─ ap_group_id             │
└───────────┬─────────────┘   └──────────────┬──────────────┘
            │                                 │
            ▼                                 ▼
┌─────────────────────────┐   ┌─────────────────────────────┐
│  create_dpsk_pool       │   │  assign_aps_to_groups       │
│  ⏳ READY               │   │  🔒 BLOCKED                 │
│                         │   │                             │
│  Needs:                 │   │  Needs:                     │
│  └─ identity_group_id   │   │  └─ ap_group_id             │
│     ✅ "abc-123"        │   │     ❌ MISSING              │
│  └─ pool_name           │   │  └─ ap_serial_numbers       │
│     ✅ "Cloudpath-DPSK" │   │     ✅ ["AP1", "AP2"]       │
│                         │   │                             │
│  Provides:              │   │  Provides:                  │
│  └─ dpsk_pool_id        │   │  └─ assigned: true          │
└─────────────────────────┘   └─────────────────────────────┘
```

**Status Legend**:
| Status | Meaning |
|--------|---------|
| ✅ COMPLETE | Phase has been executed |
| ✅ SATISFIED | All inputs exist (from R1 inventory or completed phases) |
| ⏳ READY | Dependencies satisfied, can be executed |
| 🔒 BLOCKED | Missing dependencies, cannot run yet |
| ❌ MISSING | Required input not available |

**Dependency Resolution Logic**:

```python
class DependencyResolver:
    """Resolve phase dependencies against plan state."""

    def get_phase_status(
        self,
        phase_id: str,
        plan: VenueImportPlan,
        unit_id: str = None
    ) -> PhaseStatus:
        """
        Determine if a phase can run.

        Checks:
        1. Was phase already executed? → COMPLETE
        2. Are all depends_on phases complete? → Check inputs
        3. For each required input:
           - Does it exist in unit.resolved? → SATISFIED
           - Does it exist in R1 inventory? → SATISFIED
           - Will upstream phase provide it? → READY (if upstream ready)
           - Otherwise → BLOCKED
        """
        phase = self.workflow.get_phase(phase_id)

        # Already executed?
        if phase_id in plan.execution_state.phases_completed:
            return PhaseStatus.COMPLETE

        # Check depends_on phases
        for dep_id in phase.depends_on:
            dep_status = self.get_phase_status(dep_id, plan, unit_id)
            if dep_status not in (PhaseStatus.COMPLETE, PhaseStatus.SATISFIED):
                return PhaseStatus.BLOCKED

        # Check each required input
        executor_class = self.get_executor(phase_id)
        for field_name, field_info in executor_class.Inputs.model_fields.items():
            if not field_info.is_required():
                continue

            # Check sources for this input
            value = self._resolve_input(field_name, plan, unit_id)
            if value is None:
                return PhaseStatus.BLOCKED

        return PhaseStatus.READY

    def _resolve_input(
        self,
        field_name: str,
        plan: VenueImportPlan,
        unit_id: str
    ) -> Optional[Any]:
        """
        Try to resolve an input from available sources.

        Priority:
        1. unit.resolved (from completed phases)
        2. R1 inventory (already exists)
        3. unit.plan (will be created)
        4. global options
        """
        unit = plan.units.get(unit_id)
        if not unit:
            return None

        # From completed phase outputs
        if hasattr(unit.resolved, field_name):
            val = getattr(unit.resolved, field_name)
            if val is not None:
                return val

        # From R1 inventory (e.g., identity_group_id if group exists)
        val = self._lookup_in_inventory(field_name, plan, unit)
        if val is not None:
            return val

        # From plan (names, not IDs)
        if hasattr(unit.plan, field_name):
            val = getattr(unit.plan, field_name)
            if val is not None:
                return val

        return None

    def _lookup_in_inventory(
        self,
        field_name: str,
        plan: VenueImportPlan,
        unit: UnitMapping
    ) -> Optional[str]:
        """
        Check if a resource already exists in R1.

        Maps field names to inventory lookups:
        - identity_group_id → inventory.identity_group_by_name[unit.plan.identity_group_name]
        - dpsk_pool_id → inventory.dpsk_pool_by_name[unit.plan.dpsk_pool_name]
        - ap_group_id → inventory.ap_group_by_name[unit.plan.ap_group_name]
        - network_id → inventory.network_by_ssid[unit.plan.ssid_name]
        """
        inv = plan.inventory

        if field_name == 'identity_group_id' and unit.plan.identity_group_name:
            return inv.identity_group_by_name.get(unit.plan.identity_group_name)

        if field_name == 'dpsk_pool_id' and unit.plan.dpsk_pool_name:
            pool_info = inv.dpsk_pool_by_name.get(unit.plan.dpsk_pool_name)
            return pool_info.get('id') if pool_info else None

        if field_name == 'ap_group_id' and unit.plan.ap_group_name:
            return inv.ap_group_by_name.get(unit.plan.ap_group_name)

        if field_name == 'network_id' and unit.plan.ssid_name:
            network = inv.network_by_ssid.get(unit.plan.ssid_name)
            return network.get('id') if network else None

        return None
```

**UI Component: Phase Dependency Card**

```typescript
interface PhaseDependencyCard {
  phase_id: string;
  phase_name: string;
  status: 'complete' | 'satisfied' | 'ready' | 'blocked';

  // Inputs with resolution status
  inputs: {
    name: string;
    required: boolean;
    status: 'satisfied' | 'planned' | 'missing';
    value?: string;        // Actual value if satisfied
    source?: 'inventory' | 'completed_phase' | 'plan';
    planned_value?: string; // Name if planned but not yet created
  }[];

  // Outputs this phase will provide
  outputs: {
    name: string;
    will_provide: boolean;
  }[];

  // Upstream dependencies
  depends_on: {
    phase_id: string;
    status: 'complete' | 'ready' | 'blocked';
  }[];
}
```

### Unified Experience: Plan → Execute → Review

**The Plan is the single source of truth** - whether you're running the full workflow, individual phases, or specific units.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         UNIFIED WORKFLOW EXPERIENCE                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                   │
│  │   CREATE     │───▶│   EXECUTE    │───▶│   REVIEW     │                   │
│  │    PLAN      │    │  (flexible)  │    │   HISTORY    │                   │
│  └──────────────┘    └──────────────┘    └──────────────┘                   │
│         │                   │                   │                            │
│         ▼                   ▼                   ▼                            │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                   │
│  │ • Inventory  │    │ Run options: │    │ • Execution  │                   │
│  │ • Summary    │    │ • All phases │    │   history    │                   │
│  │ • New vs     │    │ • Some phases│    │ • Per-unit   │                   │
│  │   Existing   │    │ • All units  │    │   results    │                   │
│  │ • Dependency │    │ • Some units │    │ • Errors     │                   │
│  │   graph      │    │              │    │ • Duration   │                   │
│  └──────────────┘    └──────────────┘    └──────────────┘                   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Plan Summary (replaces Validation Summary)

The plan summary is **always available** - before, during, and after execution:

```python
GET /api/v3/workflows/plans/{plan_id}/summary

{
  # INVENTORY SUMMARY
  "inventory": {
    "discovered_at": "2024-01-15T10:30:00Z",
    "aps": 54,
    "ap_groups": 42,
    "networks": 38,
    "dpsk_pools": 1,
    "identity_groups": 1,
    "passphrases": 850
  },

  # RESOURCE SUMMARY (what will be created vs reused)
  "resources": {
    "phases": [
      {"phase_id": "create_ap_groups", "new": 12, "existing": 42, "total": 54},
      {"phase_id": "create_networks", "new": 12, "existing": 42, "total": 54},
      {"phase_id": "create_passphrases", "new": 150, "existing": 850, "total": 1000},
      ...
    ],
    "totals": {
      "new": 186,
      "existing": 936,
      "reuse_rate": 0.83,
      "estimated_api_calls": 186,
      "estimated_duration_seconds": 186
    }
  },

  # UNIT SUMMARY
  "units": {
    "total": 54,
    "by_status": {
      "pending": 12,
      "ready": 42,
      "completed": 0,
      "failed": 0
    }
  },

  # EXECUTION STATE (if any executions have happened)
  "execution": {
    "last_run": "2024-01-15T11:00:00Z",
    "total_runs": 2,
    "phases_completed": ["validate_and_plan", "create_identity_group", "create_dpsk_pool"],
    "phases_remaining": ["create_ap_groups", "assign_aps", "create_networks", ...]
  }
}
```

### Execution Modes (All Use Same SSE)

**Mode 1: Full Workflow**
```python
POST /api/v3/workflows/plans/{plan_id}/execute
{
  "mode": "full"  # Run all phases, all units
}
```

**Mode 2: Selected Phases**
```python
POST /api/v3/workflows/plans/{plan_id}/execute
{
  "mode": "phases",
  "phases": ["create_ap_groups", "assign_aps_to_groups"]
}
```

**Mode 3: Selected Units**
```python
POST /api/v3/workflows/plans/{plan_id}/execute
{
  "mode": "units",
  "units": ["unit_101", "unit_102", "unit_103"]
}
```

**Mode 4: Specific Phases + Units**
```python
POST /api/v3/workflows/plans/{plan_id}/execute
{
  "mode": "targeted",
  "phases": ["assign_aps_to_groups"],
  "units": ["unit_101", "unit_102"]
}
```

### SSE Events (Unified Across All Modes)

Same SSE channel, same events - whether full workflow or single phase:

```
SSE Channel: /api/v3/workflows/plans/{plan_id}/events

Events (same as V2, but plan-scoped):
─────────────────────────────────────────────────────────────────────

execution_started
  {"execution_id": "exec-123", "mode": "phases", "phases": ["create_ap_groups"], "units": null}

plan_updated
  {"summary": {...}}  # Updated resource counts as inventory changes

phase_started
  {"phase_id": "create_ap_groups", "unit_id": "unit_101"}

phase_progress
  {"phase_id": "create_ap_groups", "current": 5, "total": 12, "failed": 0, "item_name": "ap_group"}

phase_completed
  {"phase_id": "create_ap_groups", "unit_id": "unit_101", "duration_ms": 1200, "reused": false}

unit_started
  {"unit_id": "unit_101", "unit_number": "101", "phases_total": 8}

unit_completed
  {"unit_id": "unit_101", "unit_number": "101", "phases_completed": 8, "phases_failed": 0}

resource_created
  {"type": "ap_group", "name": "Unit-101", "id": "abc-123"}

resource_reused
  {"type": "ap_group", "name": "Unit-103", "id": "def-456"}

execution_completed
  {"execution_id": "exec-123", "status": "completed", "duration_seconds": 45, "summary": {...}}

execution_failed
  {"execution_id": "exec-123", "error": "...", "failed_units": [...]}

message
  {"message": "Creating AP group: Unit-101", "level": "info"}
```

### Execution History (Per-Plan)

Track all executions on a plan:

```python
GET /api/v3/workflows/plans/{plan_id}/executions

{
  "executions": [
    {
      "id": "exec-001",
      "started_at": "2024-01-15T10:00:00Z",
      "completed_at": "2024-01-15T10:02:30Z",
      "duration_seconds": 150,
      "mode": "full",
      "phases_requested": null,  # null = all
      "units_requested": null,   # null = all
      "status": "partial",
      "results": {
        "phases_completed": 5,
        "phases_failed": 1,
        "units_completed": 52,
        "units_failed": 2,
        "resources_created": 120,
        "resources_reused": 800,
        "api_calls": 125
      }
    },
    {
      "id": "exec-002",
      "started_at": "2024-01-15T11:00:00Z",
      "completed_at": "2024-01-15T11:00:45Z",
      "duration_seconds": 45,
      "mode": "targeted",
      "phases_requested": ["create_ap_groups"],
      "units_requested": ["unit_101", "unit_102"],
      "status": "completed",
      "results": {
        "phases_completed": 1,
        "phases_failed": 0,
        "units_completed": 2,
        "units_failed": 0,
        "resources_created": 2,
        "resources_reused": 0,
        "api_calls": 2
      }
    }
  ]
}
```

### UI: Unified Dashboard

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  CLOUDPATH IMPORT: Sunset Apartments                                        │
│  Plan ID: plan-abc123 | Created: Jan 15, 2024 | Last run: 2 hours ago       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─ SUMMARY ────────────────────────────────────────────────────────────┐   │
│  │                                                                       │   │
│  │   Resources      New    Existing   Total    Status                   │   │
│  │   ─────────────────────────────────────────────────────────────────  │   │
│  │   Identity Group   0         1        1     ✅ Complete               │   │
│  │   DPSK Pool        0         1        1     ✅ Complete               │   │
│  │   AP Groups       12        42       54     ⏳ 12 remaining           │   │
│  │   Networks        12        42       54     🔒 Blocked                │   │
│  │   Passphrases    150       850     1000     ✅ Complete               │   │
│  │   ─────────────────────────────────────────────────────────────────  │   │
│  │   TOTAL          186       936     1122     Progress: 83%            │   │
│  │                                                                       │   │
│  │   [▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░] 83% reuse rate          │   │
│  │                                                                       │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─ ACTIONS ─────────────────────────────────────────────────────────────┐  │
│  │                                                                        │  │
│  │  [Run All Remaining]  [Run Selected Phases ▼]  [Run Selected Units ▼] │  │
│  │                                                                        │  │
│  │  [ ] create_ap_groups (12 new)       Units: [ ] All  [x] 101-112      │  │
│  │  [ ] assign_aps_to_groups (12)                                        │  │
│  │  [ ] create_networks (12 new)                                         │  │
│  │  [ ] activate_networks (12)                                           │  │
│  │                                                                        │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌─ EXECUTION LOG ───────────────────────────────────────────────────────┐  │
│  │                                                                        │  │
│  │  [Live] Creating AP group: Unit-101...                                │  │
│  │  [10:02:15] ✅ Created AP group: Unit-101 (id: abc-123)               │  │
│  │  [10:02:14] ✅ Reused AP group: Unit-103 (id: def-456)                │  │
│  │  [10:02:13] ▶ Started phase: create_ap_groups                         │  │
│  │  [10:02:00] ▶ Execution started (mode: phases, 2 phases selected)     │  │
│  │                                                                        │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌─ HISTORY ─────────────────────────────────────────────────────────────┐  │
│  │                                                                        │  │
│  │  Jan 15, 11:00  Targeted: assign_aps (2 units)      ✅ 45s            │  │
│  │  Jan 15, 10:00  Full workflow                       ⚠️ Partial 2:30   │  │
│  │  Jan 15, 09:30  Phases: create_identity_group       ✅ 3s             │  │
│  │                                                                        │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Key Unification Points

| Aspect | V2 (Current) | V3 (Unified) |
|--------|--------------|--------------|
| **Validation** | Per-job, discarded after run | Persistent plan summary, always available |
| **Execution scope** | Full workflow only | Any combo of phases + units |
| **SSE channel** | Job-scoped (`/jobs/{id}/events`) | Plan-scoped (`/plans/{id}/events`) |
| **History** | Single job result | Multiple execution records per plan |
| **Resume** | Start new job | Continue from plan state |
| **Resource tracking** | Per-job `created_resources` | Per-plan inventory + execution results |
| **Summary** | Only at start/end | Always available, live updates |

### Example: User's Use Case

"I need to just map the unit number to the AP groups"

```python
# 1. Create/load plan (if not already done)
plan = await brain.create_import_plan(
    venue_id="venue-123",
    input_data=cloudpath_json,
    options={"ssid_mode": "per_unit", "ap_group_prefix": "Unit-"}
)

# 2. Review the plan
print(plan.units)  # See all unit mappings
print(plan.discovery.aps)  # See all venue APs
print(plan.discovery.ap_groups)  # See existing AP groups

# 3. Execute just the AP assignment phase
result = await brain.execute_phases(
    plan_id=plan.id,
    phases=["create_ap_groups", "assign_aps_to_groups"],
    # Optionally filter to specific units
    units=["unit_101", "unit_102"]
)

# 4. Later, run the network phases
result = await brain.execute_phases(
    plan_id=plan.id,
    phases=["create_networks", "activate_networks", "assign_networks_to_ap_groups"]
)
```

### Operation Types: Audit vs Mutate vs Verify

**Clear separation between read and write operations enables pre/post validation and targeted retries.**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         OPERATION LIFECYCLE                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌────────────────┐    ┌────────────────┐    ┌────────────────┐             │
│  │   PRE-AUDIT    │───▶│     MUTATE     │───▶│  POST-AUDIT    │             │
│  │   (GET/Query)  │    │  (POST/PUT)    │    │   (GET/Query)  │             │
│  └────────────────┘    └────────────────┘    └────────────────┘             │
│         │                                            │                       │
│         │              ┌────────────────┐            │                       │
│         └─────────────▶│      DIFF      │◀───────────┘                       │
│                        └────────────────┘                                    │
│                               │                                              │
│                        ┌──────▼───────┐                                      │
│                        │ RETRY FAILED │                                      │
│                        └──────────────┘                                      │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Operation Categories**:

| Category | HTTP Methods | Purpose | Side Effects | Idempotent |
|----------|--------------|---------|--------------|------------|
| **Audit** | GET, POST (query) | Read current state | None | Yes |
| **Mutate** | POST, PUT, DELETE | Create/modify/delete | Yes | Varies |
| **Verify** | GET | Confirm mutation succeeded | None | Yes |

**Phase Classification**:

```python
class PhaseType(Enum):
    AUDIT = "audit"       # Read-only, can run anytime, safe to retry
    MUTATE = "mutate"     # Creates/modifies resources, has side effects
    VERIFY = "verify"     # Confirms mutations, triggers retries

class PhaseDefinition(BaseModel):
    id: str
    name: str
    phase_type: PhaseType  # Explicit classification

    # For MUTATE phases: what entity type does this create?
    creates_entity: Optional[str] = None  # e.g., "ap_group", "network"

    # For VERIFY phases: what entity type does this verify?
    verifies_entity: Optional[str] = None

    # Can this phase be retried for specific entities?
    supports_partial_retry: bool = False
```

**Example Flow: AP Group Creation**

```python
# 1. PRE-AUDIT: What AP groups exist?
pre_audit = await inventory_service.get_ap_groups(venue_id)
# Result: {"Unit-101": "id-1", "Unit-102": "id-2"}

# 2. MUTATE: Create missing AP groups
to_create = ["Unit-103", "Unit-104", "Unit-105"]
results = await create_ap_groups_phase.execute(to_create)
# Result: {"Unit-103": "id-3", "Unit-104": "id-4", "Unit-105": "FAILED"}

# 3. POST-AUDIT: Verify all AP groups now exist
post_audit = await inventory_service.get_ap_groups(venue_id)
# Result: {"Unit-101": "id-1", ..., "Unit-104": "id-4"}
# Missing: "Unit-105" - needs retry

# 4. DIFF: Compare expected vs actual
expected = {"Unit-101", "Unit-102", "Unit-103", "Unit-104", "Unit-105"}
actual = set(post_audit.keys())
missing = expected - actual  # {"Unit-105"}

# 5. RETRY: Re-attempt failed entities
if missing:
    retry_results = await create_ap_groups_phase.execute(list(missing))
```

**Audit Operations (Read-Only)**:

```python
class AuditOperations:
    """All GET/Query operations - safe, no side effects."""

    # AP Groups
    async def get_ap_groups(self, venue_id: str) -> List[APGroupInfo]: ...
    async def get_ap_group_by_name(self, venue_id: str, name: str) -> Optional[APGroupInfo]: ...

    # Networks
    async def get_networks(self, venue_id: str) -> List[NetworkInfo]: ...
    async def get_network_by_ssid(self, venue_id: str, ssid: str) -> Optional[NetworkInfo]: ...

    # DPSK
    async def get_dpsk_pools(self, venue_id: str) -> List[DPSKPoolInfo]: ...
    async def get_passphrases(self, pool_id: str) -> List[PassphraseInfo]: ...

    # Identity
    async def get_identity_groups(self, venue_id: str) -> List[IdentityGroupInfo]: ...
    async def get_identities(self, group_id: str) -> List[IdentityInfo]: ...

    # Full inventory (parallel fetch of all above)
    async def get_full_inventory(self, venue_id: str) -> R1Inventory: ...
```

**Mutate Operations (Write)**:

```python
class MutateOperations:
    """All POST/PUT/DELETE operations - have side effects."""

    # Returns: (created_id, reused_existing)
    async def create_ap_group(self, venue_id: str, name: str) -> Tuple[str, bool]: ...
    async def create_network(self, venue_id: str, config: NetworkConfig) -> Tuple[str, bool]: ...
    async def create_dpsk_pool(self, venue_id: str, config: DPSKConfig) -> Tuple[str, bool]: ...
    async def create_passphrase(self, pool_id: str, data: PassphraseData) -> str: ...

    # Bulk operations
    async def create_passphrases_bulk(self, pool_id: str, data: List[PassphraseData]) -> BulkResult: ...
    async def assign_aps_to_group(self, group_id: str, serials: List[str]) -> BulkResult: ...
```

**Verification Pattern**:

```python
class VerificationResult(BaseModel):
    """Result of post-mutation verification."""
    entity_type: str
    expected: int
    found: int
    missing: List[str]       # Entity names/IDs not found
    extra: List[str]         # Unexpected entities found
    mismatched: List[str]    # Found but with wrong properties

    @property
    def success(self) -> bool:
        return len(self.missing) == 0 and len(self.mismatched) == 0


async def verify_phase_result(
    phase_id: str,
    expected_entities: List[str],
    inventory_service: InventoryService,
    entity_type: str,
) -> VerificationResult:
    """
    Verify that a mutate phase created all expected entities.

    Used for:
    1. Immediate post-phase verification
    2. Retry triggering for failed entities
    3. Final workflow validation
    """
    # Re-fetch current state
    current = await inventory_service.get_entities(entity_type)
    current_names = {e.name for e in current}

    expected_set = set(expected_entities)

    return VerificationResult(
        entity_type=entity_type,
        expected=len(expected_set),
        found=len(current_names & expected_set),
        missing=list(expected_set - current_names),
        extra=list(current_names - expected_set),  # Optional tracking
        mismatched=[],  # Would need property comparison
    )
```

**Retry Strategy**:

```python
class RetryConfig(BaseModel):
    """Configuration for phase retry behavior."""
    max_attempts: int = 3
    backoff_seconds: List[int] = [5, 15, 30]
    retry_on_partial: bool = True  # Retry just failed entities
    verify_after_retry: bool = True


async def execute_with_verification(
    phase: PhaseExecutor,
    inputs: PhaseInputs,
    verify_fn: Callable,
    retry_config: RetryConfig,
) -> PhaseResult:
    """Execute a mutate phase with post-verification and retry."""

    for attempt in range(retry_config.max_attempts):
        # Execute phase
        result = await phase.execute(inputs)

        # Verify result
        verification = await verify_fn(result)

        if verification.success:
            return PhaseResult(
                status="complete",
                outputs=result,
                verification=verification,
            )

        # Partial failure - retry just the missing entities
        if retry_config.retry_on_partial and verification.missing:
            await self.emit(
                f"Verification found {len(verification.missing)} missing entities, "
                f"retry {attempt + 1}/{retry_config.max_attempts}"
            )

            # Filter inputs to just missing entities
            inputs = self._filter_to_missing(inputs, verification.missing)

            if attempt < retry_config.max_attempts - 1:
                await asyncio.sleep(retry_config.backoff_seconds[attempt])

    # All retries exhausted
    return PhaseResult(
        status="partial",
        outputs=result,
        verification=verification,
        error=f"{len(verification.missing)} entities failed after {retry_config.max_attempts} attempts"
    )
```

**Pre/Post Audit in Plan Summary**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  PLAN AUDIT SUMMARY                                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Entity Type       Pre-Audit    Expected    Post-Audit    Status            │
│  ────────────────────────────────────────────────────────────────────────   │
│  AP Groups              42          54           53       ⚠️ 1 missing      │
│  Networks               38          54           54       ✅ Complete        │
│  DPSK Pools              1           1            1       ✅ Complete        │
│  Passphrases           850        1000          998       ⚠️ 2 missing      │
│  ────────────────────────────────────────────────────────────────────────   │
│                                                                              │
│  [Retry 3 Missing Entities]  [View Details]  [Re-audit]                     │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Remaining Design Details

1. **Concurrent Executions**: Should two phase executions run on same plan simultaneously?
   - Recommendation: Lock plan during execution to prevent conflicts
   - Alternative: Allow if phases don't overlap (more complex)

2. **Inventory Size**: Large venues could have thousands of passphrases
   - Solution: Store inventory separately from plan metadata
   - Use pagination/streaming for passphrase fetch
   - Consider: Only index passphrases from relevant pools

3. **Discovery Performance**: Full inventory fetch could be slow
   - Parallel API calls for each entity type
   - Show progress during discovery
   - Cache individual entity types with shorter TTLs

## Implementation Phases

### Phase 1: Generic Engine Core
Build the workflow engine with NO workflow-specific code:

```
api/workflow/v3/engine/
├── __init__.py
├── base.py           # WorkflowDefinition, InventoryService (ABC), Inventory
├── models.py         # Plan, ExecutionState, UnitMapping (generic)
├── state_manager.py  # Redis storage (works with any plan type)
├── dependency.py     # DependencyGraph, DependencyResolver (generic)
├── executor.py       # ExecutionEngine (runs any phases)
├── events.py         # SSE publisher (generic events)
└── registry.py       # Phase registry (already exists, move here)
```

Files to create:
- [ ] `api/workflow/v3/engine/base.py` - Abstract base classes
- [ ] `api/workflow/v3/engine/models.py` - Generic Plan, ExecutionState
- [ ] `api/workflow/v3/engine/state_manager.py` - Redis plan storage
- [ ] `api/workflow/v3/engine/dependency.py` - Generic resolver using field_mappings
- [ ] `api/workflow/v3/engine/executor.py` - Phase execution orchestration

### Phase 2: R1 Platform Support
Implement R1-specific inventory service (SEPARATE from engine):

```
api/services/
├── r1_inventory.py   # R1InventoryService implements InventoryService
└── r1_models.py      # R1Inventory, APInfo, NetworkInfo, etc.
```

Files to create:
- [ ] `api/services/r1_inventory.py` - R1InventoryService
- [ ] `api/services/r1_models.py` - R1-specific Pydantic models

### Phase 3: Cloudpath Workflow Definition
Define cloudpath_import as a workflow using the engine:

```
api/workflow/workflows/
├── cloudpath_import.py  # CloudpathImportWorkflow definition
└── cleanup.py           # (already exists, can migrate later)
```

- [ ] `api/workflow/workflows/cloudpath_import.py`:
  - Workflow definition with phases list
  - Field mappings for dependency resolution
  - Uses R1InventoryService

### Phase 4: Plan Management API
CRUD for plans (generic, works with any workflow):

- [ ] `api/routes/workflow_v3.py` - API endpoints:
  - `POST /api/v3/workflows/{workflow_name}/plans` - Create plan
  - `GET /api/v3/workflows/{workflow_name}/plans` - List plans
  - `GET /api/v3/workflows/plans/{id}` - Get plan
  - `POST /api/v3/workflows/plans/{id}/refresh` - Refresh inventory
  - `DELETE /api/v3/workflows/plans/{id}` - Delete plan

### Phase 5: Selective Execution API
Execute specific phases (generic engine feature):

- [ ] `POST /api/v3/workflows/plans/{id}/execute` - Run phases
- [ ] `GET /api/v3/workflows/plans/{id}/phases` - Phase status
- [ ] `GET /api/v3/workflows/plans/{id}/graph` - Dependency graph
- [ ] SSE endpoint for execution progress

### Phase 6: Frontend Visualization
UI components (framework-agnostic data):

- [ ] Vertical workflow graph component
- [ ] Phase card with inputs/outputs/status
- [ ] Unit selector dropdown
- [ ] "Run Selected Phases" button
- [ ] Real-time status updates via SSE

### Phase 7: Migration + Polish
- [ ] Migrate existing cloudpath phases to use new base
- [ ] V2 compatibility adapter
- [ ] Documentation

### File Structure Summary

```
api/
├── workflow/
│   ├── v3/
│   │   └── engine/           # GENERIC ENGINE (locked-in)
│   │       ├── base.py
│   │       ├── models.py
│   │       ├── state_manager.py
│   │       ├── dependency.py
│   │       ├── executor.py
│   │       └── events.py
│   │
│   ├── workflows/            # WORKFLOW DEFINITIONS
│   │   ├── cloudpath_import.py
│   │   └── cleanup.py
│   │
│   └── phases/               # PHASE IMPLEMENTATIONS
│       ├── cloudpath/
│       │   ├── validate.py
│       │   ├── identity_groups.py
│       │   └── ...
│       └── cleanup/
│           └── ...
│
├── services/                 # PLATFORM-SPECIFIC SERVICES
│   ├── r1_inventory.py
│   └── r1_models.py
│
└── routes/
    └── workflow_v3.py        # API ENDPOINTS
```

### Estimated Effort
| Phase | Effort | Dependencies |
|-------|--------|--------------|
| Phase 1: Generic Engine | 3-4 days | None |
| Phase 2: R1 Inventory | 2 days | None (parallel) |
| Phase 3: Cloudpath Workflow | 1 day | Phase 1, 2 |
| Phase 4: Plan API | 1-2 days | Phase 1 |
| Phase 5: Execution API | 2-3 days | Phase 1, 3, 4 |
| Phase 6: Frontend | 3-4 days | Phase 5 |
| Phase 7: Migration | 2 days | All |

**Total: ~2 weeks of focused work**

---

## Architecture Revisions (Staff Engineer Review)

The following revisions address critical architectural gaps identified during design review. These changes transform the plan from "Cloudpath-specific workflow engine" to "truly generic workflow orchestration framework."

### Revision 1: Generic Resource Model

**Problem**: The engine knows about R1-specific types (`ap_groups`, `networks`, `dpsk_pools`).

**Solution**: Abstract to generic `Resource` that platforms implement:

```python
# api/workflow/v3/engine/resources.py

class Resource(BaseModel):
    """Generic resource - platform-agnostic."""
    id: str
    type: str                    # "ap_group", "network", "dpsk_pool", etc.
    name: str                    # Human-readable identifier
    external_id: Optional[str]   # Platform-specific ID (R1 UUID, etc.)
    properties: Dict[str, Any]   # Type-specific properties
    state: ResourceState         # present, absent, unknown
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    def matches(self, other: 'Resource') -> bool:
        """Check if two resources represent the same entity."""
        if self.type != other.type:
            return False
        # Match by external_id if available, else by name
        if self.external_id and other.external_id:
            return self.external_id == other.external_id
        return self.name == other.name


class ResourceState(Enum):
    PRESENT = "present"    # Exists in platform
    ABSENT = "absent"      # Does not exist
    UNKNOWN = "unknown"    # Haven't checked yet
    STALE = "stale"        # Exists but properties don't match expected


class ResourceCollection(BaseModel):
    """Collection of resources of a single type."""
    resource_type: str
    resources: List[Resource]
    fetched_at: datetime
    ttl_seconds: int = 300  # 5 minute default

    def lookup_by_name(self, name: str) -> Optional[Resource]:
        for r in self.resources:
            if r.name == name:
                return r
        return None

    def lookup_by_id(self, external_id: str) -> Optional[Resource]:
        for r in self.resources:
            if r.external_id == external_id:
                return r
        return None

    @property
    def is_stale(self) -> bool:
        age = (datetime.utcnow() - self.fetched_at).total_seconds()
        return age > self.ttl_seconds


class Inventory(BaseModel):
    """Generic inventory - collection of resource collections."""
    scope_type: str              # "venue", "tenant", "global"
    scope_id: str                # venue_id, tenant_id, etc.
    collections: Dict[str, ResourceCollection]  # type -> collection
    discovered_at: datetime

    def get_collection(self, resource_type: str) -> Optional[ResourceCollection]:
        return self.collections.get(resource_type)

    def lookup(self, resource_type: str, name: str) -> Optional[Resource]:
        collection = self.get_collection(resource_type)
        if collection:
            return collection.lookup_by_name(name)
        return None
```

**Platform Implementation** (R1-specific, in `api/services/`):

```python
# api/services/r1/resources.py

class R1ResourceTypes:
    """R1-specific resource type constants."""
    AP = "ap"
    AP_GROUP = "ap_group"
    NETWORK = "wifi_network"
    DPSK_POOL = "dpsk_pool"
    PASSPHRASE = "passphrase"
    IDENTITY_GROUP = "identity_group"
    IDENTITY = "identity"


def ap_to_resource(ap_data: Dict) -> Resource:
    """Convert R1 AP response to generic Resource."""
    return Resource(
        id=f"ap:{ap_data['serial']}",
        type=R1ResourceTypes.AP,
        name=ap_data.get('name', ap_data['serial']),
        external_id=ap_data['serial'],
        properties={
            'mac': ap_data.get('mac'),
            'model': ap_data.get('model'),
            'ap_group_id': ap_data.get('apGroupId'),
            'status': ap_data.get('status'),
        },
        state=ResourceState.PRESENT,
    )
```

---

### Revision 2: Work Items (Replacing "Units")

**Problem**: "Unit" is Cloudpath-specific (apartments). Other workflows need different parallel dimensions.

**Solution**: Abstract to `WorkItem` - the generic unit of parallel work:

```python
# api/workflow/v3/engine/work_items.py

class WorkItem(BaseModel):
    """
    Generic unit of parallel work.

    Examples:
    - Cloudpath import: WorkItem per apartment unit
    - Cleanup: WorkItem per resource type
    - Audit: WorkItem per AP group
    - Bulk update: WorkItem per batch of 100 items
    """
    id: str                      # Unique within workflow
    label: str                   # Human-readable (e.g., "Unit 101", "AP Group: Lobby")
    scope: Dict[str, Any]        # Workflow-specific scope data

    # Execution state
    status: WorkItemStatus
    phases_completed: List[str]
    phases_failed: Dict[str, str]  # phase_id -> error
    current_phase: Optional[str]

    # Resources this work item will create/modify
    planned_resources: List[ResourceIntent]
    resolved_resources: Dict[str, str]  # resource_name -> external_id


class WorkItemStatus(Enum):
    PENDING = "pending"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkItemFactory(ABC):
    """
    Workflow-specific factory that creates work items from input data.
    Implemented per workflow, not in engine.
    """

    @abstractmethod
    def create_work_items(
        self,
        input_data: Dict[str, Any],
        inventory: Inventory,
        options: Dict[str, Any],
    ) -> List[WorkItem]:
        """Generate work items from workflow input."""
        pass

    @abstractmethod
    def get_parallel_dimension(self) -> str:
        """Return the name of the parallel dimension (e.g., 'unit', 'ap_group')."""
        pass


# Cloudpath implementation
class CloudpathWorkItemFactory(WorkItemFactory):
    """Creates work items for Cloudpath import - one per apartment unit."""

    def get_parallel_dimension(self) -> str:
        return "unit"

    def create_work_items(
        self,
        input_data: Dict[str, Any],
        inventory: Inventory,
        options: Dict[str, Any],
    ) -> List[WorkItem]:
        # Parse Cloudpath data, create one WorkItem per unit
        units = self._extract_units(input_data)
        return [
            WorkItem(
                id=f"unit:{unit_num}",
                label=f"Unit {unit_num}",
                scope={
                    'unit_number': unit_num,
                    'passphrases': unit_data['passphrases'],
                    'ssid': unit_data.get('ssid'),
                },
                status=WorkItemStatus.PENDING,
                phases_completed=[],
                phases_failed={},
                current_phase=None,
                planned_resources=[],
                resolved_resources={},
            )
            for unit_num, unit_data in units.items()
        ]
```

---

### Revision 3: Explicit State Machines

**Problem**: No defined state transitions or recovery procedures.

**Solution**: Explicit state machines for Plan, Execution, and Phase:

```python
# api/workflow/v3/engine/state_machine.py

class PlanState(Enum):
    """Plan lifecycle states."""
    DRAFT = "draft"           # Being created, not ready
    READY = "ready"           # Validated, ready for execution
    EXECUTING = "executing"   # Execution in progress
    PARTIAL = "partial"       # Some executions completed, some work remains
    COMPLETE = "complete"     # All work items completed successfully
    FAILED = "failed"         # Unrecoverable failure
    EXPIRED = "expired"       # TTL exceeded


PLAN_TRANSITIONS = {
    PlanState.DRAFT: [PlanState.READY, PlanState.FAILED],
    PlanState.READY: [PlanState.EXECUTING, PlanState.EXPIRED],
    PlanState.EXECUTING: [PlanState.READY, PlanState.PARTIAL, PlanState.COMPLETE, PlanState.FAILED],
    PlanState.PARTIAL: [PlanState.EXECUTING, PlanState.COMPLETE, PlanState.EXPIRED],
    PlanState.COMPLETE: [PlanState.EXPIRED],  # Can expire even if complete
    PlanState.FAILED: [PlanState.READY],  # Can retry after fixing
    PlanState.EXPIRED: [],  # Terminal
}


class ExecutionState(Enum):
    """Execution lifecycle states."""
    PENDING = "pending"       # Queued, not started
    RUNNING = "running"       # Currently executing
    PAUSED = "paused"         # Manually paused
    COMPLETED = "completed"   # All phases succeeded
    PARTIAL = "partial"       # Some phases failed
    FAILED = "failed"         # Critical failure, stopped
    CANCELLED = "cancelled"   # User cancelled
    STALE = "stale"          # Process died, needs recovery


EXECUTION_TRANSITIONS = {
    ExecutionState.PENDING: [ExecutionState.RUNNING, ExecutionState.CANCELLED],
    ExecutionState.RUNNING: [ExecutionState.PAUSED, ExecutionState.COMPLETED,
                             ExecutionState.PARTIAL, ExecutionState.FAILED,
                             ExecutionState.CANCELLED, ExecutionState.STALE],
    ExecutionState.PAUSED: [ExecutionState.RUNNING, ExecutionState.CANCELLED],
    ExecutionState.COMPLETED: [],  # Terminal
    ExecutionState.PARTIAL: [],    # Terminal (can start new execution)
    ExecutionState.FAILED: [],     # Terminal
    ExecutionState.CANCELLED: [],  # Terminal
    ExecutionState.STALE: [ExecutionState.RUNNING, ExecutionState.FAILED],  # Recovery
}


class PhaseState(Enum):
    """Phase execution states."""
    PENDING = "pending"       # Not started
    BLOCKED = "blocked"       # Dependencies not met
    READY = "ready"           # Dependencies met, can run
    RUNNING = "running"       # Currently executing
    COMPLETED = "completed"   # Succeeded
    SATISFIED = "satisfied"   # Skipped - resource already exists
    PARTIAL = "partial"       # Some work items failed
    FAILED = "failed"         # All work items failed
    SKIPPED = "skipped"       # Explicitly skipped


class StateMachine:
    """Generic state machine with transition validation."""

    def __init__(self, transitions: Dict[Enum, List[Enum]]):
        self.transitions = transitions

    def can_transition(self, from_state: Enum, to_state: Enum) -> bool:
        allowed = self.transitions.get(from_state, [])
        return to_state in allowed

    def transition(self, current: Enum, target: Enum) -> Enum:
        if not self.can_transition(current, target):
            raise InvalidStateTransition(
                f"Cannot transition from {current.value} to {target.value}"
            )
        return target


# Recovery procedures
class RecoveryProcedure(ABC):
    """Procedure to recover from stale/failed state."""

    @abstractmethod
    async def can_recover(self, execution: 'Execution') -> bool:
        """Check if recovery is possible."""
        pass

    @abstractmethod
    async def recover(self, execution: 'Execution') -> ExecutionState:
        """Attempt recovery, return new state."""
        pass


class StaleExecutionRecovery(RecoveryProcedure):
    """Recover from stale execution (process died)."""

    async def can_recover(self, execution: 'Execution') -> bool:
        # Check if lock is still held
        # Check last heartbeat
        return True

    async def recover(self, execution: 'Execution') -> ExecutionState:
        # Release stale lock
        # Check which phases completed
        # Resume from last checkpoint
        pass
```

---

### Revision 4: Concurrency Model

**Problem**: Lock semantics undefined, race conditions possible.

**Solution**: Explicit distributed locking with failure handling:

```python
# api/workflow/v3/engine/concurrency.py

class DistributedLock:
    """Redis-based distributed lock with heartbeat."""

    def __init__(
        self,
        redis: Redis,
        key: str,
        ttl_seconds: int = 60,
        heartbeat_interval: int = 15,
    ):
        self.redis = redis
        self.key = f"lock:{key}"
        self.ttl = ttl_seconds
        self.heartbeat_interval = heartbeat_interval
        self.lock_id = str(uuid4())
        self._heartbeat_task: Optional[asyncio.Task] = None

    async def acquire(self, timeout: int = 30) -> bool:
        """Attempt to acquire lock with timeout."""
        start = time.time()
        while time.time() - start < timeout:
            acquired = await self.redis.set(
                self.key,
                self.lock_id,
                nx=True,  # Only if not exists
                ex=self.ttl,
            )
            if acquired:
                self._start_heartbeat()
                return True
            await asyncio.sleep(0.5)
        return False

    async def release(self):
        """Release lock if we own it."""
        self._stop_heartbeat()
        # Lua script to atomically check and delete
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        await self.redis.eval(script, 1, self.key, self.lock_id)

    def _start_heartbeat(self):
        """Keep lock alive while we hold it."""
        async def heartbeat():
            while True:
                await asyncio.sleep(self.heartbeat_interval)
                # Extend TTL only if we still own the lock
                script = """
                if redis.call("get", KEYS[1]) == ARGV[1] then
                    return redis.call("expire", KEYS[1], ARGV[2])
                else
                    return 0
                end
                """
                result = await self.redis.eval(
                    script, 1, self.key, self.lock_id, self.ttl
                )
                if result == 0:
                    break  # Lost the lock

        self._heartbeat_task = asyncio.create_task(heartbeat())

    def _stop_heartbeat(self):
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

    async def __aenter__(self):
        if not await self.acquire():
            raise LockAcquisitionFailed(f"Could not acquire lock: {self.key}")
        return self

    async def __aexit__(self, *args):
        await self.release()


class ExecutionLock:
    """Lock for plan execution with concurrency control."""

    def __init__(self, redis: Redis, plan_id: str):
        self.redis = redis
        self.plan_id = plan_id
        self.lock = DistributedLock(redis, f"plan:{plan_id}:execution")

    async def acquire_exclusive(self) -> bool:
        """Acquire exclusive lock - only one execution at a time."""
        return await self.lock.acquire()

    async def check_conflicts(self, phases: List[str]) -> List[str]:
        """
        Check if requested phases conflict with running execution.
        Returns list of conflicting phases.
        """
        running_phases = await self.redis.smembers(
            f"plan:{self.plan_id}:running_phases"
        )
        return list(set(phases) & running_phases)
```

---

### Revision 5: Rollback & Compensation

**Problem**: No way to undo partial execution.

**Solution**: Track created resources with compensation actions:

```python
# api/workflow/v3/engine/compensation.py

class CompensationAction(BaseModel):
    """Action to undo a mutation."""
    id: str
    execution_id: str
    phase_id: str
    work_item_id: Optional[str]

    # What was created
    resource_type: str
    resource_id: str
    resource_name: str

    # How to undo
    action_type: CompensationType
    action_params: Dict[str, Any]

    # State
    status: CompensationStatus
    attempted_at: Optional[datetime]
    error: Optional[str]


class CompensationType(Enum):
    DELETE = "delete"           # Delete the resource
    RESTORE = "restore"         # Restore previous state
    DETACH = "detach"          # Remove association
    NOOP = "noop"              # Nothing to undo


class CompensationStatus(Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class CompensationRegistry:
    """Track compensations for an execution."""

    def __init__(self, redis: Redis):
        self.redis = redis

    async def register(
        self,
        execution_id: str,
        phase_id: str,
        resource_type: str,
        resource_id: str,
        resource_name: str,
        compensation_type: CompensationType,
        compensation_params: Dict[str, Any] = None,
        work_item_id: str = None,
    ):
        """Register a compensation action for a created resource."""
        action = CompensationAction(
            id=str(uuid4()),
            execution_id=execution_id,
            phase_id=phase_id,
            work_item_id=work_item_id,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_name=resource_name,
            action_type=compensation_type,
            action_params=compensation_params or {},
            status=CompensationStatus.PENDING,
        )
        await self.redis.rpush(
            f"execution:{execution_id}:compensations",
            action.model_dump_json()
        )

    async def get_compensations(
        self,
        execution_id: str,
    ) -> List[CompensationAction]:
        """Get all compensation actions for an execution."""
        data = await self.redis.lrange(
            f"execution:{execution_id}:compensations",
            0, -1
        )
        return [CompensationAction.model_validate_json(d) for d in data]

    async def execute_rollback(
        self,
        execution_id: str,
        executor: 'CompensationExecutor',
    ) -> RollbackResult:
        """
        Execute all compensations in reverse order.
        Returns summary of rollback.
        """
        compensations = await self.get_compensations(execution_id)

        # Reverse order - undo in opposite order of creation
        compensations.reverse()

        succeeded = []
        failed = []

        for action in compensations:
            try:
                await executor.execute(action)
                action.status = CompensationStatus.COMPLETED
                succeeded.append(action)
            except Exception as e:
                action.status = CompensationStatus.FAILED
                action.error = str(e)
                failed.append(action)

        return RollbackResult(
            execution_id=execution_id,
            total=len(compensations),
            succeeded=succeeded,
            failed=failed,
        )


class CompensationExecutor(ABC):
    """Platform-specific compensation execution."""

    @abstractmethod
    async def execute(self, action: CompensationAction):
        """Execute a single compensation action."""
        pass


# R1 implementation
class R1CompensationExecutor(CompensationExecutor):
    """Execute compensations against R1 API."""

    def __init__(self, r1_client):
        self.r1_client = r1_client

    async def execute(self, action: CompensationAction):
        if action.action_type == CompensationType.DELETE:
            await self._delete_resource(action)
        elif action.action_type == CompensationType.DETACH:
            await self._detach_resource(action)

    async def _delete_resource(self, action: CompensationAction):
        if action.resource_type == "ap_group":
            await self.r1_client.venues.delete_ap_group(action.resource_id)
        elif action.resource_type == "wifi_network":
            await self.r1_client.networks.delete(action.resource_id)
        # ... etc
```

---

### Revision 6: Inventory Cache

**Problem**: Per-plan inventory snapshots, no sharing or freshness control.

**Solution**: Venue-level inventory cache with TTL:

```python
# api/workflow/v3/engine/inventory_cache.py

class InventoryCache:
    """
    Venue-level inventory cache with TTL and selective refresh.
    Shared across plans for the same venue.
    """

    def __init__(self, redis: Redis, inventory_service: 'InventoryService'):
        self.redis = redis
        self.inventory_service = inventory_service

    async def get(
        self,
        scope_type: str,
        scope_id: str,
        resource_types: List[str] = None,
        max_age_seconds: int = 300,
        force_refresh: bool = False,
    ) -> Inventory:
        """
        Get inventory, using cache if fresh enough.

        Args:
            scope_type: "venue", "tenant", etc.
            scope_id: The scope identifier
            resource_types: Specific types to fetch (None = all)
            max_age_seconds: Maximum age before refresh
            force_refresh: Force fresh fetch from platform
        """
        cache_key = f"inventory:{scope_type}:{scope_id}"

        if not force_refresh:
            cached = await self._get_cached(cache_key, max_age_seconds)
            if cached:
                # Check if we have all requested types
                if resource_types is None or all(
                    t in cached.collections for t in resource_types
                ):
                    return cached

        # Fetch fresh inventory
        inventory = await self.inventory_service.discover(
            scope_type=scope_type,
            scope_id=scope_id,
            resource_types=resource_types,
        )

        # Cache it
        await self._cache(cache_key, inventory)

        return inventory

    async def refresh_type(
        self,
        scope_type: str,
        scope_id: str,
        resource_type: str,
    ) -> ResourceCollection:
        """Refresh a single resource type in the cache."""
        cache_key = f"inventory:{scope_type}:{scope_id}"

        # Fetch just this type
        collection = await self.inventory_service.discover_type(
            scope_type=scope_type,
            scope_id=scope_id,
            resource_type=resource_type,
        )

        # Update cache atomically
        await self._update_collection(cache_key, resource_type, collection)

        return collection

    async def invalidate(
        self,
        scope_type: str,
        scope_id: str,
        resource_type: str = None,
    ):
        """Invalidate cache (all or specific type)."""
        cache_key = f"inventory:{scope_type}:{scope_id}"

        if resource_type:
            # Remove just this collection
            await self.redis.hdel(cache_key, resource_type)
        else:
            # Remove entire inventory
            await self.redis.delete(cache_key)

    async def _get_cached(
        self,
        cache_key: str,
        max_age_seconds: int,
    ) -> Optional[Inventory]:
        """Get cached inventory if fresh enough."""
        data = await self.redis.hgetall(cache_key)
        if not data:
            return None

        # Check freshness
        meta = json.loads(data.get('_meta', '{}'))
        cached_at = datetime.fromisoformat(meta.get('cached_at', '1970-01-01'))
        age = (datetime.utcnow() - cached_at).total_seconds()

        if age > max_age_seconds:
            return None

        # Reconstruct inventory
        collections = {}
        for key, value in data.items():
            if key.startswith('_'):
                continue
            collections[key] = ResourceCollection.model_validate_json(value)

        return Inventory(
            scope_type=meta['scope_type'],
            scope_id=meta['scope_id'],
            collections=collections,
            discovered_at=cached_at,
        )
```

---

### Revision 7: Reuse Policies

**Problem**: What if existing resource has different properties?

**Solution**: Configurable reuse policies:

```python
# api/workflow/v3/engine/reuse.py

class ReusePolicy(Enum):
    """How to handle existing resources."""
    STRICT = "strict"       # Must match exactly, fail if different
    LENIENT = "lenient"     # Use as-is, ignore property differences
    UPDATE = "update"       # Update properties to match expected
    SKIP = "skip"           # Skip if exists, don't use


class ReuseDecision(BaseModel):
    """Result of checking reuse policy."""
    action: ReuseAction
    existing_resource: Optional[Resource]
    reason: str
    property_diff: Optional[Dict[str, Any]]  # Expected vs actual


class ReuseAction(Enum):
    CREATE = "create"       # Resource doesn't exist, create it
    REUSE = "reuse"         # Use existing resource as-is
    UPDATE = "update"       # Update existing resource
    FAIL = "fail"           # Conflict, cannot proceed


class ReuseChecker:
    """Check if a resource can be reused based on policy."""

    def __init__(self, policy: ReusePolicy):
        self.policy = policy

    def check(
        self,
        expected: ResourceIntent,
        existing: Optional[Resource],
        critical_properties: List[str] = None,
    ) -> ReuseDecision:
        """
        Check if existing resource can be reused.

        Args:
            expected: What we want to create
            existing: What exists (None if doesn't exist)
            critical_properties: Properties that MUST match regardless of policy
        """
        if existing is None:
            return ReuseDecision(
                action=ReuseAction.CREATE,
                existing_resource=None,
                reason="Resource does not exist",
            )

        # Check critical properties first
        if critical_properties:
            for prop in critical_properties:
                expected_val = expected.properties.get(prop)
                actual_val = existing.properties.get(prop)
                if expected_val != actual_val:
                    return ReuseDecision(
                        action=ReuseAction.FAIL,
                        existing_resource=existing,
                        reason=f"Critical property mismatch: {prop}",
                        property_diff={prop: {'expected': expected_val, 'actual': actual_val}},
                    )

        # Check all properties
        diff = self._compute_diff(expected.properties, existing.properties)

        if not diff:
            # Exact match
            return ReuseDecision(
                action=ReuseAction.REUSE,
                existing_resource=existing,
                reason="Existing resource matches expected",
            )

        # Properties differ - apply policy
        if self.policy == ReusePolicy.STRICT:
            return ReuseDecision(
                action=ReuseAction.FAIL,
                existing_resource=existing,
                reason="Property mismatch with STRICT policy",
                property_diff=diff,
            )

        if self.policy == ReusePolicy.LENIENT:
            return ReuseDecision(
                action=ReuseAction.REUSE,
                existing_resource=existing,
                reason="Using existing despite property differences (LENIENT)",
                property_diff=diff,
            )

        if self.policy == ReusePolicy.UPDATE:
            return ReuseDecision(
                action=ReuseAction.UPDATE,
                existing_resource=existing,
                reason="Will update existing resource to match",
                property_diff=diff,
            )

        if self.policy == ReusePolicy.SKIP:
            return ReuseDecision(
                action=ReuseAction.REUSE,
                existing_resource=existing,
                reason="Skipping - using existing as-is",
                property_diff=diff,
            )
```

---

### Revision 8: Enhanced Dependencies

**Problem**: Simple `depends_on` list doesn't handle conditional or dynamic dependencies.

**Solution**: Dependency expressions:

```python
# api/workflow/v3/engine/dependencies.py

class DependencyType(Enum):
    HARD = "hard"           # Must complete before this phase runs
    SOFT = "soft"           # Prefer to run after, but not required
    CONDITIONAL = "conditional"  # Depends on runtime condition


class Dependency(BaseModel):
    """A single dependency relationship."""
    phase_id: str
    type: DependencyType = DependencyType.HARD
    condition: Optional[str] = None  # Expression for conditional deps

    # For resource-level dependencies
    scope: DependencyScope = DependencyScope.GLOBAL


class DependencyScope(Enum):
    GLOBAL = "global"       # Phase must complete for all work items
    SAME_ITEM = "same_item" # Phase must complete for same work item
    ANY = "any"             # Phase must complete for at least one work item


class ConditionalDependency(Dependency):
    """Dependency that only applies when condition is true."""

    def evaluate(self, context: Dict[str, Any]) -> bool:
        """
        Evaluate if this dependency applies.

        Context contains:
        - options: workflow options
        - work_item: current work item scope
        - inventory: resource inventory
        - phase_outputs: outputs from completed phases
        """
        if not self.condition:
            return True

        # Simple expression evaluation
        # Could use something like simpleeval for safety
        return self._eval_expression(self.condition, context)

    def _eval_expression(self, expr: str, context: Dict) -> bool:
        # Examples:
        # "options.ssid_mode == 'per_unit'"
        # "work_item.scope.passphrases | length > 0"
        # "phase_outputs.create_dpsk_pool.pool_id is not none"
        pass


class DependencyGraph:
    """
    DAG of phase dependencies with condition evaluation.
    """

    def __init__(self, phases: List['PhaseDefinition']):
        self.phases = {p.id: p for p in phases}
        self._build_graph()

    def get_runnable_phases(
        self,
        completed: Set[str],
        context: Dict[str, Any],
    ) -> List[str]:
        """Get phases that can run given current state and context."""
        runnable = []

        for phase_id, phase in self.phases.items():
            if phase_id in completed:
                continue

            can_run = True
            for dep in phase.dependencies:
                # Check if dependency applies
                if isinstance(dep, ConditionalDependency):
                    if not dep.evaluate(context):
                        continue  # Skip this dependency

                # Check if dependency is satisfied
                if dep.type == DependencyType.HARD:
                    if dep.phase_id not in completed:
                        can_run = False
                        break
                elif dep.type == DependencyType.SOFT:
                    # Soft deps don't block, but we prefer to wait
                    pass

            if can_run:
                runnable.append(phase_id)

        return runnable

    def get_blocking_deps(
        self,
        phase_id: str,
        completed: Set[str],
        context: Dict[str, Any],
    ) -> List[str]:
        """Get dependencies blocking a specific phase."""
        phase = self.phases[phase_id]
        blocking = []

        for dep in phase.dependencies:
            if isinstance(dep, ConditionalDependency):
                if not dep.evaluate(context):
                    continue

            if dep.type == DependencyType.HARD and dep.phase_id not in completed:
                blocking.append(dep.phase_id)

        return blocking
```

---

### Revision 9: Error Classification

**Problem**: All errors treated the same.

**Solution**: Error taxonomy with handling policies:

```python
# api/workflow/v3/engine/errors.py

class ErrorCategory(Enum):
    """Classification of errors."""
    TRANSIENT = "transient"       # Retry likely to succeed (network, rate limit)
    PERMANENT = "permanent"        # Retry won't help (invalid config, auth)
    PARTIAL = "partial"           # Some items succeeded
    BLOCKING = "blocking"         # Stops dependent phases
    WARNING = "warning"           # Log but continue


class ErrorSeverity(Enum):
    """How severe is the error."""
    LOW = "low"           # Log and continue
    MEDIUM = "medium"     # Warn but continue
    HIGH = "high"         # Fail phase but continue workflow
    CRITICAL = "critical" # Stop entire workflow


class WorkflowError(Exception):
    """Base class for workflow errors with classification."""
    category: ErrorCategory = ErrorCategory.PERMANENT
    severity: ErrorSeverity = ErrorSeverity.HIGH
    retryable: bool = False

    def __init__(
        self,
        message: str,
        details: Dict[str, Any] = None,
        cause: Exception = None,
    ):
        super().__init__(message)
        self.details = details or {}
        self.cause = cause


class TransientError(WorkflowError):
    """Error that may succeed on retry."""
    category = ErrorCategory.TRANSIENT
    retryable = True


class RateLimitError(TransientError):
    """API rate limit hit."""
    severity = ErrorSeverity.MEDIUM

    def __init__(self, retry_after: int = 60, **kwargs):
        super().__init__(**kwargs)
        self.retry_after = retry_after


class NetworkError(TransientError):
    """Network connectivity issue."""
    pass


class PermanentError(WorkflowError):
    """Error that won't succeed on retry."""
    category = ErrorCategory.PERMANENT
    retryable = False


class ConfigurationError(PermanentError):
    """Invalid configuration."""
    severity = ErrorSeverity.CRITICAL


class AuthenticationError(PermanentError):
    """Authentication/authorization failure."""
    severity = ErrorSeverity.CRITICAL


class ResourceConflictError(PermanentError):
    """Resource already exists with different config."""
    severity = ErrorSeverity.HIGH


class PartialSuccessError(WorkflowError):
    """Some items succeeded, some failed."""
    category = ErrorCategory.PARTIAL
    severity = ErrorSeverity.MEDIUM

    def __init__(
        self,
        message: str,
        succeeded: List[str],
        failed: List[Tuple[str, str]],  # [(item_id, error), ...]
        **kwargs,
    ):
        super().__init__(message, **kwargs)
        self.succeeded = succeeded
        self.failed = failed


class ErrorHandler:
    """Handle errors according to policy."""

    def __init__(self, policy: 'ErrorPolicy'):
        self.policy = policy

    async def handle(
        self,
        error: WorkflowError,
        context: 'PhaseContext',
    ) -> ErrorAction:
        """Determine action based on error and policy."""

        if error.category == ErrorCategory.TRANSIENT:
            if error.retryable and context.retry_count < self.policy.max_retries:
                return ErrorAction.RETRY
            return ErrorAction.FAIL_PHASE

        if error.category == ErrorCategory.PARTIAL:
            if self.policy.continue_on_partial:
                return ErrorAction.CONTINUE_WITH_FAILURES
            return ErrorAction.FAIL_PHASE

        if error.severity == ErrorSeverity.CRITICAL:
            return ErrorAction.ABORT_WORKFLOW

        if error.severity == ErrorSeverity.HIGH:
            return ErrorAction.FAIL_PHASE

        return ErrorAction.LOG_AND_CONTINUE


class ErrorAction(Enum):
    RETRY = "retry"
    CONTINUE_WITH_FAILURES = "continue_with_failures"
    FAIL_PHASE = "fail_phase"
    ABORT_WORKFLOW = "abort_workflow"
    LOG_AND_CONTINUE = "log_and_continue"


class ErrorPolicy(BaseModel):
    """Configurable error handling policy."""
    max_retries: int = 3
    retry_backoff: List[int] = [5, 15, 30]
    continue_on_partial: bool = True
    abort_on_critical: bool = True
    failure_threshold: float = 0.5  # Abort if >50% failures
```

---

### Revision 10: Testing Strategy

**Problem**: No defined testing approach.

**Solution**: First-class testing interfaces:

```python
# api/workflow/v3/engine/testing.py

class MockInventoryService(InventoryService):
    """Mock inventory for testing."""

    def __init__(self, resources: Dict[str, List[Resource]] = None):
        self._resources = resources or {}

    def add_resource(self, resource: Resource):
        if resource.type not in self._resources:
            self._resources[resource.type] = []
        self._resources[resource.type].append(resource)

    async def discover(self, scope_type: str, scope_id: str, **kwargs) -> Inventory:
        collections = {
            rtype: ResourceCollection(
                resource_type=rtype,
                resources=resources,
                fetched_at=datetime.utcnow(),
            )
            for rtype, resources in self._resources.items()
        }
        return Inventory(
            scope_type=scope_type,
            scope_id=scope_id,
            collections=collections,
            discovered_at=datetime.utcnow(),
        )


class MockPlatformClient:
    """Mock platform client for testing mutations."""

    def __init__(self):
        self.calls: List[Dict] = []
        self._responses: Dict[str, Any] = {}
        self._errors: Dict[str, Exception] = {}

    def set_response(self, method: str, response: Any):
        self._responses[method] = response

    def set_error(self, method: str, error: Exception):
        self._errors[method] = error

    async def create_ap_group(self, **kwargs) -> Dict:
        self.calls.append({'method': 'create_ap_group', 'kwargs': kwargs})
        if 'create_ap_group' in self._errors:
            raise self._errors['create_ap_group']
        return self._responses.get('create_ap_group', {'id': str(uuid4())})


class TestExecutionEngine:
    """
    Test harness for workflow execution.
    Provides helpers for common test scenarios.
    """

    def __init__(self):
        self.inventory = MockInventoryService()
        self.client = MockPlatformClient()
        self.events: List[Dict] = []

    async def execute_phase(
        self,
        phase_class: Type['PhaseExecutor'],
        inputs: Dict[str, Any],
        work_item: WorkItem = None,
    ) -> 'PhaseResult':
        """Execute a single phase in isolation."""
        context = self._create_test_context(work_item)
        executor = phase_class(context)
        return await executor.execute(phase_class.Inputs(**inputs))

    async def simulate_failure(
        self,
        phase_class: Type['PhaseExecutor'],
        inputs: Dict[str, Any],
        error: Exception,
    ) -> 'PhaseResult':
        """Test phase behavior when platform returns error."""
        self.client.set_error(phase_class.platform_method, error)
        return await self.execute_phase(phase_class, inputs)

    async def simulate_partial_failure(
        self,
        phase_class: Type['PhaseExecutor'],
        inputs: Dict[str, Any],
        succeed_items: List[str],
        fail_items: List[Tuple[str, str]],
    ) -> 'PhaseResult':
        """Test phase behavior with partial success."""
        # Configure mock to succeed/fail specific items
        pass

    def assert_calls(self, expected: List[Dict]):
        """Assert platform calls match expected."""
        assert self.client.calls == expected

    def assert_events(self, expected: List[str]):
        """Assert emitted events match expected types."""
        actual_types = [e['type'] for e in self.events]
        assert actual_types == expected


# Example test
async def test_create_ap_group_reuses_existing():
    """Test that existing AP group is reused."""
    harness = TestExecutionEngine()

    # Setup: AP group already exists
    harness.inventory.add_resource(Resource(
        id="existing-1",
        type="ap_group",
        name="Unit-101",
        external_id="abc-123",
        properties={},
        state=ResourceState.PRESENT,
    ))

    # Execute
    result = await harness.execute_phase(
        CreateAPGroupPhase,
        inputs={'ap_group_name': 'Unit-101'},
    )

    # Assert: No create call made, reused existing
    assert result.outputs.ap_group_id == "abc-123"
    assert result.outputs.reused == True
    assert len(harness.client.calls) == 0
```

---

### Revision 11: Execution as First-Class Resource

**Problem**: Execution is inline with plan, hard to track/manage.

**Solution**: Separate Execution resource:

```python
# api/workflow/v3/engine/execution.py

class Execution(BaseModel):
    """
    A single execution of a plan (or subset).
    Separate from plan - multiple executions per plan.
    """
    id: str
    plan_id: str

    # What to execute
    mode: ExecutionMode
    phases: Optional[List[str]]      # None = all phases
    work_items: Optional[List[str]]  # None = all work items

    # State
    status: ExecutionState
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    current_phase: Optional[str]

    # Progress
    phases_completed: List[str]
    phases_failed: Dict[str, str]
    work_items_completed: List[str]
    work_items_failed: Dict[str, str]

    # Resources created (for rollback)
    resources_created: List[ResourceRef]

    # Event history
    events: List[ExecutionEvent]


class ExecutionMode(Enum):
    FULL = "full"               # All phases, all work items
    PHASES = "phases"           # Specific phases, all work items
    WORK_ITEMS = "work_items"   # All phases, specific work items
    TARGETED = "targeted"       # Specific phases and work items


# API endpoints
"""
POST   /api/v3/executions
       Body: {"plan_id": "...", "mode": "phases", "phases": [...], "work_items": [...]}
       → Create and start new execution

GET    /api/v3/executions/{id}
       → Get execution status

GET    /api/v3/executions/{id}/events
       → SSE stream for this execution

POST   /api/v3/executions/{id}/pause
       → Pause execution (if supported)

POST   /api/v3/executions/{id}/resume
       → Resume paused execution

POST   /api/v3/executions/{id}/cancel
       → Cancel execution

POST   /api/v3/executions/{id}/rollback
       → Rollback created resources

GET    /api/v3/plans/{plan_id}/executions
       → List all executions for a plan
"""
```

---

### Revision 12: Observability

**Problem**: Only SSE events, no metrics/tracing/alerting.

**Solution**: Comprehensive observability hooks:

```python
# api/workflow/v3/engine/observability.py

class MetricsCollector:
    """Collect workflow metrics."""

    def __init__(self, backend: 'MetricsBackend'):
        self.backend = backend

    async def record_phase_duration(
        self,
        workflow_name: str,
        phase_id: str,
        duration_ms: int,
        status: str,
    ):
        await self.backend.histogram(
            "workflow_phase_duration_ms",
            duration_ms,
            tags={
                "workflow": workflow_name,
                "phase": phase_id,
                "status": status,
            },
        )

    async def record_api_call(
        self,
        service: str,
        method: str,
        duration_ms: int,
        status_code: int,
    ):
        await self.backend.histogram(
            "api_call_duration_ms",
            duration_ms,
            tags={
                "service": service,
                "method": method,
                "status": str(status_code),
            },
        )

    async def increment_counter(
        self,
        name: str,
        tags: Dict[str, str] = None,
    ):
        await self.backend.counter(name, 1, tags or {})


class TracingContext:
    """Distributed tracing context."""

    def __init__(self, trace_id: str = None, parent_span_id: str = None):
        self.trace_id = trace_id or str(uuid4())
        self.parent_span_id = parent_span_id
        self.span_id = str(uuid4())

    def child(self) -> 'TracingContext':
        return TracingContext(
            trace_id=self.trace_id,
            parent_span_id=self.span_id,
        )

    def to_headers(self) -> Dict[str, str]:
        """Headers for propagating trace context."""
        return {
            "X-Trace-ID": self.trace_id,
            "X-Span-ID": self.span_id,
            "X-Parent-Span-ID": self.parent_span_id or "",
        }


class StructuredLogger:
    """Structured logging with trace context."""

    def __init__(self, name: str, tracing: TracingContext = None):
        self.logger = logging.getLogger(name)
        self.tracing = tracing

    def _add_context(self, extra: Dict) -> Dict:
        ctx = extra.copy()
        if self.tracing:
            ctx['trace_id'] = self.tracing.trace_id
            ctx['span_id'] = self.tracing.span_id
        return ctx

    def info(self, message: str, **extra):
        self.logger.info(message, extra=self._add_context(extra))

    def error(self, message: str, error: Exception = None, **extra):
        ctx = self._add_context(extra)
        if error:
            ctx['error'] = str(error)
            ctx['error_type'] = type(error).__name__
        self.logger.error(message, extra=ctx, exc_info=error)


class AlertManager:
    """Alert on workflow issues."""

    def __init__(self, backend: 'AlertBackend'):
        self.backend = backend

    async def check_execution_health(self, execution: Execution):
        """Check if execution needs alerting."""

        # Stuck execution
        if execution.status == ExecutionState.RUNNING:
            runtime = (datetime.utcnow() - execution.started_at).total_seconds()
            if runtime > 3600:  # 1 hour
                await self.backend.alert(
                    severity="warning",
                    title=f"Execution {execution.id} running for >1 hour",
                    details={"execution_id": execution.id, "runtime_seconds": runtime},
                )

        # High failure rate
        total = len(execution.work_items_completed) + len(execution.work_items_failed)
        if total > 10:
            failure_rate = len(execution.work_items_failed) / total
            if failure_rate > 0.5:
                await self.backend.alert(
                    severity="error",
                    title=f"Execution {execution.id} has {failure_rate:.0%} failure rate",
                    details={
                        "execution_id": execution.id,
                        "failed": len(execution.work_items_failed),
                        "total": total,
                    },
                )
```

---

### Revision 13: Event Sourcing

**Problem**: Mutable state makes debugging hard, no replay for SSE reconnection.

**Solution**: Event sourcing for execution history:

```python
# api/workflow/v3/engine/event_store.py

class ExecutionEvent(BaseModel):
    """Immutable event in execution history."""
    id: str
    execution_id: str
    timestamp: datetime
    sequence: int
    event_type: str
    data: Dict[str, Any]

    # For correlation
    phase_id: Optional[str]
    work_item_id: Optional[str]


class EventStore:
    """Append-only event store for execution history."""

    def __init__(self, redis: Redis):
        self.redis = redis

    async def append(
        self,
        execution_id: str,
        event_type: str,
        data: Dict[str, Any],
        phase_id: str = None,
        work_item_id: str = None,
    ) -> ExecutionEvent:
        """Append event to execution history."""

        # Get next sequence number
        sequence = await self.redis.incr(f"execution:{execution_id}:seq")

        event = ExecutionEvent(
            id=str(uuid4()),
            execution_id=execution_id,
            timestamp=datetime.utcnow(),
            sequence=sequence,
            event_type=event_type,
            data=data,
            phase_id=phase_id,
            work_item_id=work_item_id,
        )

        # Append to stream
        await self.redis.xadd(
            f"execution:{execution_id}:events",
            {"event": event.model_dump_json()},
        )

        return event

    async def get_events(
        self,
        execution_id: str,
        since_sequence: int = 0,
    ) -> List[ExecutionEvent]:
        """Get events since sequence number (for replay)."""
        events = await self.redis.xrange(
            f"execution:{execution_id}:events",
            min='-',
            max='+',
        )

        result = []
        for event_id, data in events:
            event = ExecutionEvent.model_validate_json(data['event'])
            if event.sequence > since_sequence:
                result.append(event)

        return result

    async def stream_events(
        self,
        execution_id: str,
        last_id: str = '$',
    ) -> AsyncIterator[ExecutionEvent]:
        """Stream events in real-time (for SSE)."""
        while True:
            events = await self.redis.xread(
                {f"execution:{execution_id}:events": last_id},
                block=5000,  # 5 second timeout
            )

            for stream, entries in events:
                for event_id, data in entries:
                    event = ExecutionEvent.model_validate_json(data['event'])
                    yield event
                    last_id = event_id

    def reconstruct_state(
        self,
        execution_id: str,
    ) -> Execution:
        """Reconstruct execution state from events."""
        events = await self.get_events(execution_id)

        state = Execution(
            id=execution_id,
            status=ExecutionState.PENDING,
            phases_completed=[],
            phases_failed={},
            work_items_completed=[],
            work_items_failed={},
            resources_created=[],
            events=events,
        )

        for event in events:
            self._apply_event(state, event)

        return state

    def _apply_event(self, state: Execution, event: ExecutionEvent):
        """Apply event to state (event sourcing reducer)."""
        if event.event_type == "execution_started":
            state.status = ExecutionState.RUNNING
            state.started_at = event.timestamp

        elif event.event_type == "phase_completed":
            state.phases_completed.append(event.phase_id)

        elif event.event_type == "phase_failed":
            state.phases_failed[event.phase_id] = event.data.get('error')

        elif event.event_type == "resource_created":
            state.resources_created.append(ResourceRef(**event.data))

        # ... etc
```

---

### Updated Implementation Phases

With all revisions incorporated, the implementation phases expand:

| Phase | Description | Effort | Dependencies |
|-------|-------------|--------|--------------|
| **1A** | Generic Resource Model | 2 days | None |
| **1B** | Work Items abstraction | 1 day | 1A |
| **1C** | State Machines | 1 day | None |
| **1D** | Concurrency (locks, heartbeat) | 2 days | None |
| **2A** | Inventory Cache | 2 days | 1A |
| **2B** | Reuse Policies | 1 day | 1A, 2A |
| **3A** | Enhanced Dependencies | 2 days | 1B |
| **3B** | Error Classification | 1 day | None |
| **4A** | Compensation/Rollback | 2 days | 1A |
| **4B** | Event Store | 2 days | None |
| **5A** | Execution Resource + API | 2 days | 1C, 4B |
| **5B** | SSE with replay | 1 day | 4B, 5A |
| **6A** | Observability (metrics, tracing) | 2 days | None |
| **7A** | Testing Harness | 2 days | 1A, 1B |
| **8A** | R1 Platform Adapter | 2 days | 1A, 2A |
| **8B** | Cloudpath Workflow Migration | 2 days | All above |
| **9A** | Frontend Updates | 3 days | 5A, 5B |
| **10** | Documentation + Polish | 2 days | All |

**Revised Total: ~4 weeks of focused work**

The additional 2 weeks investment yields:
- Truly platform-agnostic engine
- Production-grade reliability (state machines, concurrency, compensation)
- Debuggable (event sourcing, tracing)
- Testable (mock interfaces, test harness)
- Extensible (new workflows, new platforms)
