# Workflow Engine V2: Architecture Plan

> **Status**: Draft for Review
> **Author**: Claude + Human collaboration
> **Date**: 2025-01-28

## Executive Summary

This document outlines a major refactor of the workflow engine to achieve:

1. **Truly independent phases** with explicit input/output contracts
2. **Per-unit parallel execution** where Unit 1's Phase 3 can start as soon as Unit 1's Phase 2 completes
3. **A central "brain"** that orchestrates flows, tracks all activities in bulk, and manages dependencies
4. **Phase 0 validation** (dry-run) that pre-computes the entire plan and awaits user confirmation
5. **Redis as source of truth** for multi-worker support with unit mapping persistence
6. **Visual flow representation** for frontend documentation

---

## Table of Contents

1. [Core Architecture](#1-core-architecture)
2. [Data Models](#2-data-models)
3. [Phase System](#3-phase-system)
4. [Workflow Definition DSL](#4-workflow-definition-dsl)
5. [Execution Engine (The Brain)](#5-execution-engine-the-brain)
6. [Activity Tracker](#6-activity-tracker)
7. [Validation Phase (Phase 0)](#7-validation-phase-phase-0)
8. [Cleanup Workflow](#8-cleanup-workflow)
9. [Frontend Visualization](#9-frontend-visualization)
10. [Migration Path](#10-migration-path)
11. [File Structure](#11-file-structure)
12. [Implementation Phases](#12-implementation-phases)

---

## 1. Core Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              WORKFLOW BRAIN                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │  Dependency │  │   Activity  │  │    Unit     │  │   Event Publisher   │ │
│  │  Resolver   │  │   Tracker   │  │   Router    │  │   (Redis Pub/Sub)   │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
            ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
            │   Unit 1    │   │   Unit 2    │   │   Unit N    │
            │   Pipeline  │   │   Pipeline  │   │   Pipeline  │
            └─────────────┘   └─────────────┘   └─────────────┘
                    │                 │                 │
        ┌───────────┼───────────┐     │     ┌───────────┼───────────┐
        ▼           ▼           ▼     │     ▼           ▼           ▼
    ┌───────┐   ┌───────┐   ┌───────┐ │ ┌───────┐   ┌───────┐   ┌───────┐
    │Phase 1│   │Phase 2│   │Phase 3│ │ │Phase 1│   │Phase 2│   │Phase 3│
    │(done) │──▶│(done) │──▶│(run)  │ │ │(done) │──▶│(run)  │   │(wait) │
    └───────┘   └───────┘   └───────┘ │ └───────┘   └───────┘   └───────┘
                                      │
                              ┌───────┴───────┐
                              │    REDIS      │
                              │ Source of     │
                              │ Truth         │
                              └───────────────┘
```

### Key Principles

1. **Phases declare WHAT they need, not WHERE it comes from**
   - Input contracts specify required data types
   - Output contracts specify what the phase produces
   - The brain wires outputs to inputs

2. **Per-unit parallel pipelines**
   - Each unit (building/site) runs its own pipeline
   - Unit 1's Phase 3 can start when Unit 1's Phase 2 finishes
   - No waiting for all units to complete a phase

3. **Redis is the single source of truth**
   - All state persisted to Redis
   - Workers are stateless and replaceable
   - Unit mappings enriched with IDs as phases complete

4. **Bulk activity tracking**
   - Single poller for ALL pending R1 activities
   - 2s polling interval (1s when <10 activities remain)
   - Local notifications via Redis pub/sub

---

## 2. Data Models

### 2.1 Unit Mapping (The Core Data Structure)

```python
class UnitMapping(BaseModel):
    """
    Tracks all resources for a single unit throughout the workflow.
    Starts as a plan (names only), gets enriched with IDs as phases complete.
    """
    unit_id: str  # e.g., "unit_101" or "building_a"
    unit_number: str  # Display name

    # Plan (set during validation)
    plan: UnitPlan

    # Resolved IDs (populated as phases complete)
    resolved: UnitResolved = Field(default_factory=UnitResolved)

    # Status tracking
    current_phase: Optional[str] = None
    completed_phases: List[str] = Field(default_factory=list)
    failed_phases: List[str] = Field(default_factory=list)
    status: UnitStatus = UnitStatus.PENDING


class UnitPlan(BaseModel):
    """What WILL be created/reused for this unit (names only)"""
    ap_group_name: str
    identity_group_name: Optional[str] = None
    dpsk_pool_name: Optional[str] = None
    dpsk_service_name: Optional[str] = None
    network_name: str

    # Validation results
    ap_group_exists: bool = False
    identity_group_exists: bool = False
    dpsk_pool_exists: bool = False
    network_exists: bool = False

    # What needs to happen
    will_create_ap_group: bool = True
    will_create_identity_group: bool = True
    will_create_dpsk_pool: bool = True
    will_create_network: bool = True


class UnitResolved(BaseModel):
    """Actual IDs after resources are created/found"""
    ap_group_id: Optional[str] = None
    identity_group_id: Optional[str] = None
    dpsk_pool_id: Optional[str] = None
    dpsk_service_id: Optional[str] = None
    network_id: Optional[str] = None
    passphrase_ids: List[str] = Field(default_factory=list)

    # AP assignments
    ap_ids: List[str] = Field(default_factory=list)
```

### 2.2 Enhanced Workflow Models

```python
class PhaseInput(BaseModel):
    """Declares what a phase needs"""
    name: str  # e.g., "identity_group_id"
    type: str  # e.g., "str", "List[str]", "IdentityGroupRef"
    source: Optional[str] = None  # e.g., "create_identity_groups" (optional hint)
    required: bool = True


class PhaseOutput(BaseModel):
    """Declares what a phase produces"""
    name: str  # e.g., "dpsk_pool_id"
    type: str  # e.g., "str"
    description: str


class PhaseContract(BaseModel):
    """Full contract for a phase"""
    inputs: List[PhaseInput] = Field(default_factory=list)
    outputs: List[PhaseOutput] = Field(default_factory=list)


class PhaseDefinitionV2(BaseModel):
    """Enhanced phase definition with contracts"""
    id: str
    name: str
    description: str

    # Contract
    contract: PhaseContract

    # Dependencies (phase IDs that must complete first)
    depends_on: List[str] = Field(default_factory=list)

    # Execution settings
    executor: str  # Python path to executor class
    critical: bool = True

    # Per-unit behavior
    per_unit: bool = True  # Does this phase run once per unit?

    # Estimated API calls (for dry-run display)
    api_calls_per_unit: int = 1


class WorkflowJobV2(BaseModel):
    """Enhanced job with unit tracking"""
    id: str
    workflow_name: str
    status: JobStatus

    # Context
    venue_id: str
    tenant_id: str
    controller_id: int
    user_id: int
    options: Dict[str, Any]

    # Unit tracking (the core innovation)
    units: Dict[str, UnitMapping] = Field(default_factory=dict)  # unit_id → UnitMapping

    # Phase definitions (from workflow)
    phases: List[PhaseDefinitionV2]

    # Global phase status (for non-per-unit phases)
    phase_status: Dict[str, PhaseStatus] = Field(default_factory=dict)

    # Activity tracking
    pending_activities: Dict[str, ActivityRef] = Field(default_factory=dict)

    # Timestamps
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Validation result (from Phase 0)
    validation_result: Optional[ValidationResult] = None
    awaiting_confirmation: bool = False
```

### 2.3 Redis Schema

```
Redis Keys:
─────────────────────────────────────────────────────────────────────────────

# Job State (JSON blob)
workflow:v2:jobs:{job_id}                    → WorkflowJobV2 (full state)

# Unit Mappings (separate for atomic updates)
workflow:v2:jobs:{job_id}:units:{unit_id}    → UnitMapping (JSON)

# Activity Tracking
workflow:v2:activities:pending               → Hash: activity_id → {job_id, unit_id, phase_id, task_id}
workflow:v2:jobs:{job_id}:activities         → Set of activity_ids for this job

# Events (Pub/Sub channels)
workflow:v2:events:{job_id}                  → Pub/Sub channel for job events
workflow:v2:events:global                    → Global events (new jobs, completions)

# Indexes
workflow:v2:jobs:index                       → Sorted Set: job_id → created_at timestamp
workflow:v2:jobs:by_venue:{venue_id}         → Set of job_ids for this venue
workflow:v2:jobs:active                      → Set of currently running job_ids

# Locks
workflow:v2:jobs:{job_id}:lock               → Distributed lock for job
workflow:v2:units:{job_id}:{unit_id}:lock    → Per-unit lock for concurrent updates
```

---

## 3. Phase System

### 3.1 Base Phase Executor

```python
from abc import ABC, abstractmethod
from typing import TypeVar, Generic, List
from pydantic import BaseModel

TInput = TypeVar('TInput', bound=BaseModel)
TOutput = TypeVar('TOutput', bound=BaseModel)


class PhaseExecutor(ABC, Generic[TInput, TOutput]):
    """
    Base class for all phase executors.

    Phases are:
    - Stateless (all state comes from inputs)
    - Typed (explicit input/output contracts)
    - Atomic (one unit at a time)
    """

    # Declare input/output types (override in subclass)
    input_type: type[TInput]
    output_type: type[TOutput]

    def __init__(self, context: PhaseContext):
        """
        Initialize with execution context.

        Args:
            context: Contains r1_client, venue_id, tenant_id, etc.
                    Does NOT contain unit-specific data (that comes via inputs)
        """
        self.context = context
        self.r1_client = context.r1_client
        self.venue_id = context.venue_id
        self.tenant_id = context.tenant_id

    @abstractmethod
    async def execute(self, inputs: TInput) -> TOutput:
        """
        Execute the phase for a single unit.

        Args:
            inputs: Typed inputs (from previous phases or validation)

        Returns:
            Typed outputs (for dependent phases)
        """
        pass

    @abstractmethod
    async def validate(self, inputs: TInput) -> ValidationResult:
        """
        Validate inputs and check existing resources (dry-run).

        Args:
            inputs: Typed inputs

        Returns:
            ValidationResult with what will be created/reused
        """
        pass

    async def register_activity(self, activity_id: str) -> None:
        """Register an R1 activity for bulk tracking."""
        await self.context.activity_tracker.register(
            activity_id=activity_id,
            job_id=self.context.job_id,
            unit_id=self.context.unit_id,
            phase_id=self.phase_id
        )

    async def wait_for_activity(self, activity_id: str) -> ActivityResult:
        """Wait for a registered activity to complete."""
        return await self.context.activity_tracker.wait(activity_id)
```

### 3.2 Example: CreateDPSKPoolPhase

```python
class CreateDPSKPoolInputs(BaseModel):
    """Inputs for creating a DPSK pool"""
    unit_id: str
    pool_name: str
    identity_group_id: str  # From create_identity_groups phase
    dpsk_service_id: str    # From create_dpsk_service phase
    vlan_id: Optional[int] = None


class CreateDPSKPoolOutputs(BaseModel):
    """Outputs from creating a DPSK pool"""
    unit_id: str
    dpsk_pool_id: str
    dpsk_pool_name: str
    reused: bool = False  # True if existing pool was found


class CreateDPSKPoolPhase(PhaseExecutor[CreateDPSKPoolInputs, CreateDPSKPoolOutputs]):
    """Creates a DPSK pool linked to an identity group."""

    input_type = CreateDPSKPoolInputs
    output_type = CreateDPSKPoolOutputs

    phase_id = "create_dpsk_pool"
    phase_name = "Create DPSK Pool"

    async def validate(self, inputs: CreateDPSKPoolInputs) -> ValidationResult:
        """Check if pool already exists."""
        existing = await self.r1_client.identity.find_dpsk_pool_by_name(
            self.tenant_id, inputs.pool_name
        )

        if existing:
            return ValidationResult(
                valid=True,
                will_create=False,
                will_reuse=True,
                existing_resource_id=existing['id'],
                message=f"DPSK Pool '{inputs.pool_name}' already exists"
            )

        return ValidationResult(
            valid=True,
            will_create=True,
            will_reuse=False,
            estimated_api_calls=1,
            message=f"Will create DPSK Pool '{inputs.pool_name}'"
        )

    async def execute(self, inputs: CreateDPSKPoolInputs) -> CreateDPSKPoolOutputs:
        """Create the DPSK pool."""
        # Check for existing (idempotent)
        existing = await self.r1_client.identity.find_dpsk_pool_by_name(
            self.tenant_id, inputs.pool_name
        )

        if existing:
            return CreateDPSKPoolOutputs(
                unit_id=inputs.unit_id,
                dpsk_pool_id=existing['id'],
                dpsk_pool_name=inputs.pool_name,
                reused=True
            )

        # Create new pool
        result = await self.r1_client.identity.create_dpsk_pool(
            tenant_id=self.tenant_id,
            name=inputs.pool_name,
            identity_group_id=inputs.identity_group_id,
            dpsk_service_id=inputs.dpsk_service_id,
            vlan_id=inputs.vlan_id
        )

        # Register activity for bulk tracking
        if result.get('requestId'):
            await self.register_activity(result['requestId'])
            activity_result = await self.wait_for_activity(result['requestId'])
            pool_id = activity_result.resource_id
        else:
            pool_id = result['id']

        return CreateDPSKPoolOutputs(
            unit_id=inputs.unit_id,
            dpsk_pool_id=pool_id,
            dpsk_pool_name=inputs.pool_name,
            reused=False
        )
```

### 3.3 Phase Registry

```python
# workflow/phases/registry.py

from typing import Dict, Type
from workflow.phases.base import PhaseExecutor

# Global phase registry
_PHASE_REGISTRY: Dict[str, Type[PhaseExecutor]] = {}


def register_phase(phase_id: str):
    """Decorator to register a phase executor."""
    def decorator(cls: Type[PhaseExecutor]):
        _PHASE_REGISTRY[phase_id] = cls
        cls.phase_id = phase_id
        return cls
    return decorator


def get_phase_executor(phase_id: str) -> Type[PhaseExecutor]:
    """Get a phase executor by ID."""
    if phase_id not in _PHASE_REGISTRY:
        raise ValueError(f"Unknown phase: {phase_id}")
    return _PHASE_REGISTRY[phase_id]


# Example usage:
@register_phase("create_dpsk_pool")
class CreateDPSKPoolPhase(PhaseExecutor[CreateDPSKPoolInputs, CreateDPSKPoolOutputs]):
    ...
```

---

## 4. Workflow Definition DSL

### 4.1 Python DSL

```python
# workflow/workflows/per_unit_dpsk.py

from workflow.definition import Workflow, Phase


PerUnitDPSKWorkflow = Workflow(
    name="per_unit_dpsk",
    description="Configure per-unit DPSK SSIDs with identity groups and passphrases",

    phases=[
        # Phase 0: Validation (always first, always runs once)
        Phase(
            id="validate",
            name="Validate & Plan",
            executor="workflow.phases.validate.ValidatePhase",
            per_unit=False,  # Runs once for all units
            critical=True,
            outputs=["unit_mappings"]
        ),

        # Phase 1: Create AP Groups (per unit, no dependencies except validate)
        Phase(
            id="create_ap_groups",
            name="Create AP Groups",
            executor="workflow.phases.ap_groups.CreateAPGroupPhase",
            depends_on=["validate"],
            per_unit=True,
            inputs=["unit_id", "ap_group_name"],
            outputs=["ap_group_id"],
            api_calls_per_unit=1
        ),

        # Phase 2: Create Identity Groups (per unit, parallel to AP groups!)
        Phase(
            id="create_identity_groups",
            name="Create Identity Groups",
            executor="workflow.phases.identity_groups.CreateIdentityGroupPhase",
            depends_on=["validate"],  # NOT depends on create_ap_groups!
            per_unit=True,
            inputs=["unit_id", "identity_group_name"],
            outputs=["identity_group_id"],
            api_calls_per_unit=1
        ),

        # Phase 3: Create DPSK Service (once for all, or per-unit if needed)
        Phase(
            id="create_dpsk_service",
            name="Create DPSK Service",
            executor="workflow.phases.dpsk_service.CreateDPSKServicePhase",
            depends_on=["validate"],
            per_unit=False,  # One service for all units
            inputs=["dpsk_service_name"],
            outputs=["dpsk_service_id"],
            api_calls_per_unit=1
        ),

        # Phase 4: Create DPSK Pools (per unit, depends on identity group AND dpsk service)
        Phase(
            id="create_dpsk_pools",
            name="Create DPSK Pools",
            executor="workflow.phases.dpsk_pools.CreateDPSKPoolPhase",
            depends_on=["create_identity_groups", "create_dpsk_service"],
            per_unit=True,
            inputs=["unit_id", "identity_group_id", "dpsk_service_id", "pool_name"],
            outputs=["dpsk_pool_id"],
            api_calls_per_unit=1
        ),

        # Phase 5: Create DPSK Networks (per unit, depends on pool AND ap_group)
        Phase(
            id="create_networks",
            name="Create DPSK Networks",
            executor="workflow.phases.wifi_networks.CreateDPSKNetworkPhase",
            depends_on=["create_dpsk_pools", "create_ap_groups"],
            per_unit=True,
            inputs=["unit_id", "ap_group_id", "dpsk_pool_id", "network_name"],
            outputs=["network_id"],
            api_calls_per_unit=1
        ),

        # Phase 6: Create Passphrases (per unit, depends on pool)
        Phase(
            id="create_passphrases",
            name="Create Passphrases",
            executor="workflow.phases.passphrases.CreatePassphrasesPhase",
            depends_on=["create_dpsk_pools"],
            per_unit=True,
            inputs=["unit_id", "dpsk_pool_id", "passphrases"],
            outputs=["passphrase_ids"],
            api_calls_per_unit="dynamic"  # Varies based on passphrase count
        ),

        # Phase 7: Activate Networks (per unit, depends on network creation)
        Phase(
            id="activate_networks",
            name="Activate Networks on Venue",
            executor="workflow.phases.activation.ActivateNetworkPhase",
            depends_on=["create_networks"],
            per_unit=True,
            inputs=["unit_id", "network_id"],
            outputs=["activated"],
            api_calls_per_unit=1
        ),

        # Phase 8: Assign APs (per unit, depends on activation)
        Phase(
            id="assign_aps",
            name="Assign APs to Groups",
            executor="workflow.phases.ap_assignment.AssignAPsPhase",
            depends_on=["activate_networks"],
            per_unit=True,
            inputs=["unit_id", "ap_group_id", "ap_serial_numbers"],
            outputs=["assigned_ap_ids"],
            api_calls_per_unit="dynamic"
        ),

        # Phase 9: Configure LAN Ports (optional, per unit)
        Phase(
            id="configure_lan_ports",
            name="Configure LAN Ports",
            executor="workflow.phases.lan_ports.ConfigureLANPortsPhase",
            depends_on=["assign_aps"],
            per_unit=True,
            critical=False,  # Non-critical - workflow succeeds even if this fails
            skip_if="not options.get('configure_lan_ports', False)",
            inputs=["unit_id", "ap_ids", "lan_port_config"],
            outputs=["configured"],
            api_calls_per_unit="dynamic"
        ),
    ]
)
```

### 4.2 Workflow Registry

```python
# workflow/workflows/__init__.py

from workflow.workflows.per_unit_dpsk import PerUnitDPSKWorkflow
from workflow.workflows.per_unit_psk import PerUnitPSKWorkflow
from workflow.workflows.cloudpath_dpsk import CloudpathDPSKWorkflow
from workflow.workflows.ap_lan_ports import APLanPortConfigWorkflow
from workflow.workflows.cleanup import VenueCleanupWorkflow

WORKFLOWS = {
    "per_unit_dpsk": PerUnitDPSKWorkflow,
    "per_unit_psk": PerUnitPSKWorkflow,
    "cloudpath_dpsk": CloudpathDPSKWorkflow,
    "ap_lan_port_config": APLanPortConfigWorkflow,
    "venue_cleanup": VenueCleanupWorkflow,
}


def get_workflow(name: str) -> Workflow:
    if name not in WORKFLOWS:
        raise ValueError(f"Unknown workflow: {name}")
    return WORKFLOWS[name]
```

### 4.3 Phase-as-Workflow Pattern

Some phases are useful both as steps within larger workflows AND as standalone
workflows. AP LAN Port Config is the canonical example. The architecture handles
this naturally - a standalone workflow is just a thin wrapper around the same phase(s):

```python
# workflow/workflows/ap_lan_ports.py
# Standalone workflow that reuses the same phase from per_unit_psk/dpsk

APLanPortConfigWorkflow = Workflow(
    name="ap_lan_port_config",
    description="Configure LAN port VLANs on APs (standalone)",

    phases=[
        Phase(
            id="validate",
            name="Validate AP Port Config",
            executor="workflow.phases.validate.ValidatePhase",
            per_unit=False,
            critical=True,
        ),

        Phase(
            id="configure_lan_ports",
            name="Configure LAN Ports",
            executor="workflow.phases.lan_ports.ConfigureLANPortsPhase",
            depends_on=["validate"],
            per_unit=True,
            inputs=["unit_id", "ap_ids", "lan_port_config"],
            outputs=["configured"],
            api_calls_per_unit="dynamic"
        ),
    ]
)

# The SAME ConfigureLANPortsPhase is used here as in PerUnitPSKWorkflow
# and PerUnitDPSKWorkflow. Zero duplication.
```

This pattern validates the architecture: if a phase has clean input/output
contracts, it can be composed into any workflow that satisfies its inputs.
The `configure_lan_ports` phase doesn't care whether it's running standalone
or as step 9 of a DPSK workflow - it just needs `ap_ids` and `lan_port_config`.

---

## 5. Execution Engine (The Brain)

### 5.1 Brain Architecture

```python
class WorkflowBrain:
    """
    Central orchestrator for workflow execution.

    Responsibilities:
    - Parse workflow definition and build dependency graph
    - Track per-unit progress through phases
    - Route phase outputs to phase inputs
    - Coordinate with ActivityTracker for bulk polling
    - Publish events for frontend updates
    """

    def __init__(
        self,
        state_manager: RedisStateManagerV2,
        activity_tracker: ActivityTracker,
        event_publisher: EventPublisher,
        r1_client: RuckusOneClient
    ):
        self.state = state_manager
        self.tracker = activity_tracker
        self.events = event_publisher
        self.r1 = r1_client

    async def execute_workflow(self, job: WorkflowJobV2) -> WorkflowJobV2:
        """
        Execute a complete workflow with per-unit parallelism.
        """
        # Build dependency graph
        graph = self._build_dependency_graph(job.phases)

        # Start the execution loop
        while not self._is_complete(job):
            # Find all ready work (phases that can start)
            ready_work = self._find_ready_work(job, graph)

            if not ready_work:
                # Nothing ready - wait for activities to complete
                await self.tracker.wait_for_any()
                continue

            # Execute all ready work in parallel
            tasks = []
            for unit_id, phase_id in ready_work:
                task = asyncio.create_task(
                    self._execute_phase_for_unit(job, unit_id, phase_id)
                )
                tasks.append(task)

            # Wait for at least one to complete (or activity updates)
            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED
            )

            # Process completed phases
            for task in done:
                result = await task
                await self._handle_phase_completion(job, result)

        return job

    def _find_ready_work(
        self,
        job: WorkflowJobV2,
        graph: DependencyGraph
    ) -> List[Tuple[str, str]]:
        """
        Find all (unit_id, phase_id) pairs that are ready to execute.

        A phase is ready for a unit when:
        1. All dependencies are satisfied FOR THAT UNIT
        2. The phase hasn't started yet for that unit
        3. The job isn't cancelled
        """
        ready = []

        for unit_id, unit in job.units.items():
            for phase in job.phases:
                if phase.id in unit.completed_phases:
                    continue  # Already done
                if phase.id == unit.current_phase:
                    continue  # Already running

                # Check dependencies FOR THIS UNIT
                deps_satisfied = all(
                    dep_id in unit.completed_phases
                    for dep_id in phase.depends_on
                )

                if deps_satisfied:
                    ready.append((unit_id, phase.id))

        return ready

    async def _execute_phase_for_unit(
        self,
        job: WorkflowJobV2,
        unit_id: str,
        phase_id: str
    ) -> PhaseResult:
        """Execute a single phase for a single unit."""
        unit = job.units[unit_id]
        phase_def = self._get_phase(job, phase_id)

        # Update unit status
        unit.current_phase = phase_id
        await self.state.update_unit(job.id, unit)

        # Publish event
        await self.events.phase_started(job.id, unit_id, phase_id)

        # Get phase executor
        executor_class = get_phase_executor(phase_id)

        # Build inputs from unit mapping
        inputs = self._build_phase_inputs(unit, phase_def)

        # Create executor with context
        context = PhaseContext(
            job_id=job.id,
            unit_id=unit_id,
            r1_client=self.r1,
            venue_id=job.venue_id,
            tenant_id=job.tenant_id,
            activity_tracker=self.tracker
        )

        executor = executor_class(context)

        try:
            # Execute
            outputs = await executor.execute(inputs)

            # Update unit mapping with outputs
            self._apply_phase_outputs(unit, phase_def, outputs)
            unit.completed_phases.append(phase_id)
            unit.current_phase = None

            await self.state.update_unit(job.id, unit)
            await self.events.phase_completed(job.id, unit_id, phase_id)

            return PhaseResult(success=True, unit_id=unit_id, phase_id=phase_id)

        except Exception as e:
            unit.failed_phases.append(phase_id)
            unit.current_phase = None
            unit.status = UnitStatus.FAILED

            await self.state.update_unit(job.id, unit)
            await self.events.phase_failed(job.id, unit_id, phase_id, str(e))

            return PhaseResult(success=False, unit_id=unit_id, phase_id=phase_id, error=str(e))
```

### 5.2 Input/Output Wiring

```python
def _build_phase_inputs(
    self,
    unit: UnitMapping,
    phase: PhaseDefinitionV2
) -> BaseModel:
    """
    Build typed inputs for a phase from the unit mapping.

    The unit mapping contains both the plan (names) and resolved (IDs).
    We wire the appropriate values based on phase input requirements.
    """
    executor_class = get_phase_executor(phase.id)
    input_class = executor_class.input_type

    # Build input dict from unit mapping
    input_data = {"unit_id": unit.unit_id}

    for field_name, field_info in input_class.model_fields.items():
        if field_name == "unit_id":
            continue

        # Check resolved first (IDs from completed phases)
        if hasattr(unit.resolved, field_name):
            value = getattr(unit.resolved, field_name)
            if value is not None:
                input_data[field_name] = value
                continue

        # Check plan (names)
        if hasattr(unit.plan, field_name):
            value = getattr(unit.plan, field_name)
            if value is not None:
                input_data[field_name] = value
                continue

        # Check if required
        if field_info.is_required():
            raise ValueError(f"Missing required input '{field_name}' for phase '{phase.id}'")

    return input_class(**input_data)


def _apply_phase_outputs(
    self,
    unit: UnitMapping,
    phase: PhaseDefinitionV2,
    outputs: BaseModel
) -> None:
    """
    Apply phase outputs to the unit mapping.

    This enriches the unit's resolved IDs for use by dependent phases.
    """
    for field_name, value in outputs.model_dump().items():
        if field_name == "unit_id":
            continue

        if hasattr(unit.resolved, field_name):
            setattr(unit.resolved, field_name, value)
```

---

## 6. Activity Tracker

### 6.1 Centralized Activity Polling

```python
class ActivityTracker:
    """
    Centralized tracker for all R1 async activities.

    - Single poller for ALL pending activities across all jobs
    - 2s polling interval (1s when < 10 activities)
    - Notifications via Redis pub/sub
    """

    def __init__(
        self,
        r1_client: RuckusOneClient,
        redis_client: redis.Redis
    ):
        self.r1 = r1_client
        self.redis = redis_client
        self._pending: Dict[str, ActivityRef] = {}
        self._events: Dict[str, asyncio.Event] = {}
        self._results: Dict[str, ActivityResult] = {}
        self._polling = False

    async def register(
        self,
        activity_id: str,
        job_id: str,
        unit_id: str,
        phase_id: str
    ) -> None:
        """Register an activity for tracking."""
        ref = ActivityRef(
            activity_id=activity_id,
            job_id=job_id,
            unit_id=unit_id,
            phase_id=phase_id,
            registered_at=datetime.utcnow()
        )

        self._pending[activity_id] = ref
        self._events[activity_id] = asyncio.Event()

        # Store in Redis for multi-worker visibility
        await self.redis.hset(
            "workflow:v2:activities:pending",
            activity_id,
            ref.model_dump_json()
        )

        # Start polling if not already running
        if not self._polling:
            asyncio.create_task(self._poll_loop())

    async def wait(self, activity_id: str) -> ActivityResult:
        """Wait for an activity to complete."""
        if activity_id in self._results:
            return self._results[activity_id]

        if activity_id not in self._events:
            raise ValueError(f"Unknown activity: {activity_id}")

        await self._events[activity_id].wait()
        return self._results[activity_id]

    async def wait_for_any(self) -> None:
        """Wait for any activity to complete (used by brain)."""
        if not self._events:
            await asyncio.sleep(0.1)
            return

        # Wait for first completion
        done, _ = await asyncio.wait(
            [asyncio.create_task(e.wait()) for e in self._events.values()],
            return_when=asyncio.FIRST_COMPLETED
        )

    async def _poll_loop(self) -> None:
        """Background polling loop."""
        self._polling = True

        try:
            while self._pending:
                # Determine poll interval
                interval = 1.0 if len(self._pending) < 10 else 2.0

                # Get all pending activity IDs
                activity_ids = list(self._pending.keys())

                # Bulk poll R1
                results = await self.r1.activities.get_bulk_status(activity_ids)

                # Process results
                for activity_id, status in results.items():
                    if status['completed']:
                        await self._handle_completion(activity_id, status)

                await asyncio.sleep(interval)
        finally:
            self._polling = False

    async def _handle_completion(
        self,
        activity_id: str,
        status: Dict[str, Any]
    ) -> None:
        """Handle activity completion."""
        ref = self._pending.pop(activity_id)

        result = ActivityResult(
            activity_id=activity_id,
            success=status.get('success', True),
            resource_id=status.get('resourceId'),
            error=status.get('error')
        )

        self._results[activity_id] = result

        # Signal waiters
        if activity_id in self._events:
            self._events[activity_id].set()

        # Remove from Redis
        await self.redis.hdel("workflow:v2:activities:pending", activity_id)

        # Publish completion event
        await self.redis.publish(
            f"workflow:v2:events:{ref.job_id}",
            json.dumps({
                "type": "activity_completed",
                "activity_id": activity_id,
                "unit_id": ref.unit_id,
                "phase_id": ref.phase_id,
                "success": result.success
            })
        )
```

---

## 7. Validation Phase (Phase 0)

### 7.1 Validation Executor

```python
@register_phase("validate")
class ValidatePhase(PhaseExecutor):
    """
    Phase 0: Validate entire workflow and build execution plan.

    This phase:
    1. Checks all unit names for conflicts
    2. Queries existing resources
    3. Builds UnitMapping for each unit
    4. Estimates total API calls
    5. Returns dry-run report
    """

    async def execute(self, inputs: ValidateInputs) -> ValidateOutputs:
        """Build the execution plan."""
        units = inputs.units

        # Pre-fetch existing resources
        existing_ap_groups = await self.r1.wifi.list_ap_groups(self.venue_id)
        existing_networks = await self.r1.wifi.list_networks(self.venue_id)
        existing_idgs = await self.r1.identity.list_identity_groups(self.tenant_id)
        existing_pools = await self.r1.identity.list_dpsk_pools(self.tenant_id)

        # Build index maps for fast lookup
        ap_group_by_name = {g['name']: g for g in existing_ap_groups}
        network_by_name = {n['name']: n for n in existing_networks}
        idg_by_name = {g['name']: g for g in existing_idgs}
        pool_by_name = {p['name']: p for p in existing_pools}

        # Build unit mappings
        unit_mappings: Dict[str, UnitMapping] = {}
        conflicts: List[str] = []
        total_api_calls = 0

        for unit_config in units:
            unit_id = f"unit_{unit_config['unit_number']}"

            # Build names
            ap_group_name = f"{inputs.ap_group_prefix}{unit_config['unit_number']}{inputs.ap_group_postfix}"
            network_name = f"{unit_config['unit_number']}-{inputs.ssid_name}"
            idg_name = f"{unit_config['unit_number']}-IDG" if inputs.dpsk_mode else None
            pool_name = f"{unit_config['unit_number']}-Pool" if inputs.dpsk_mode else None

            # Check existing
            plan = UnitPlan(
                ap_group_name=ap_group_name,
                network_name=network_name,
                identity_group_name=idg_name,
                dpsk_pool_name=pool_name,

                ap_group_exists=ap_group_name in ap_group_by_name,
                network_exists=network_name in network_by_name,
                identity_group_exists=idg_name in idg_by_name if idg_name else False,
                dpsk_pool_exists=pool_name in pool_by_name if pool_name else False,

                will_create_ap_group=ap_group_name not in ap_group_by_name,
                will_create_network=network_name not in network_by_name,
                will_create_identity_group=(idg_name and idg_name not in idg_by_name),
                will_create_dpsk_pool=(pool_name and pool_name not in pool_by_name),
            )

            # Pre-populate resolved IDs for existing resources
            resolved = UnitResolved()
            if ap_group_name in ap_group_by_name:
                resolved.ap_group_id = ap_group_by_name[ap_group_name]['id']
            if network_name in network_by_name:
                resolved.network_id = network_by_name[network_name]['id']
            if idg_name and idg_name in idg_by_name:
                resolved.identity_group_id = idg_by_name[idg_name]['id']
            if pool_name and pool_name in pool_by_name:
                resolved.dpsk_pool_id = pool_by_name[pool_name]['id']

            # Check for conflicts
            if not plan.will_create_network and network_by_name[network_name].get('settings_differ'):
                conflicts.append(f"Network '{network_name}' exists with different settings")

            unit_mappings[unit_id] = UnitMapping(
                unit_id=unit_id,
                unit_number=str(unit_config['unit_number']),
                plan=plan,
                resolved=resolved
            )

            # Count API calls
            if plan.will_create_ap_group:
                total_api_calls += 1
            if plan.will_create_identity_group:
                total_api_calls += 1
            if plan.will_create_dpsk_pool:
                total_api_calls += 1
            if plan.will_create_network:
                total_api_calls += 1
            total_api_calls += len(unit_config.get('passphrases', []))
            total_api_calls += 2  # Activation + AP assignment

        return ValidateOutputs(
            valid=len(conflicts) == 0,
            conflicts=conflicts,
            unit_mappings=unit_mappings,
            summary=ValidationSummary(
                total_units=len(units),
                ap_groups_to_create=sum(1 for u in unit_mappings.values() if u.plan.will_create_ap_group),
                ap_groups_to_reuse=sum(1 for u in unit_mappings.values() if not u.plan.will_create_ap_group),
                networks_to_create=sum(1 for u in unit_mappings.values() if u.plan.will_create_network),
                networks_to_reuse=sum(1 for u in unit_mappings.values() if not u.plan.will_create_network),
                identity_groups_to_create=sum(1 for u in unit_mappings.values() if u.plan.will_create_identity_group),
                dpsk_pools_to_create=sum(1 for u in unit_mappings.values() if u.plan.will_create_dpsk_pool),
                total_api_calls=total_api_calls
            )
        )
```

### 7.2 Dry-Run API Flow

```python
# Router endpoint
@router.post("/workflows/{workflow_name}/plan")
async def plan_workflow(
    workflow_name: str,
    request: WorkflowRequest,
    r1_client: RuckusOneClient = Depends(get_r1_client)
):
    """
    Create a workflow plan (dry-run).

    Returns:
        - Validation results
        - What will be created/reused
        - Estimated API calls
        - Conflicts that would block execution
    """
    workflow = get_workflow(workflow_name)

    # Create job in PENDING state
    job = await create_job(workflow, request, status=JobStatus.PENDING)

    # Run validation phase only
    brain = WorkflowBrain(...)
    validation_result = await brain.execute_validation(job)

    # Store result and mark as awaiting confirmation
    job.validation_result = validation_result
    job.awaiting_confirmation = True
    await state_manager.save_job(job)

    return {
        "job_id": job.id,
        "valid": validation_result.valid,
        "conflicts": validation_result.conflicts,
        "summary": validation_result.summary,
        "unit_plans": {
            unit_id: unit.plan.model_dump()
            for unit_id, unit in job.units.items()
        }
    }


@router.post("/workflows/jobs/{job_id}/confirm")
async def confirm_workflow(
    job_id: str,
    background_tasks: BackgroundTasks
):
    """
    Confirm and execute a planned workflow.
    """
    job = await state_manager.get_job(job_id)

    if not job.awaiting_confirmation:
        raise HTTPException(400, "Job is not awaiting confirmation")

    if not job.validation_result.valid:
        raise HTTPException(400, "Cannot execute invalid workflow - resolve conflicts first")

    # Start execution in background
    job.awaiting_confirmation = False
    await state_manager.save_job(job)

    background_tasks.add_task(execute_workflow_background, job_id)

    return {"job_id": job_id, "status": "STARTED"}
```

---

## 8. Cleanup Workflow

### 8.1 Nuclear Cleanup Definition

```python
VenueCleanupWorkflow = Workflow(
    name="venue_cleanup",
    description="Delete all workflow-created resources from a venue (NUCLEAR OPTION)",

    phases=[
        # Phase 0: Inventory what exists
        Phase(
            id="inventory",
            name="Inventory Resources",
            executor="workflow.phases.cleanup.InventoryPhase",
            per_unit=False,
            outputs=["resources_to_delete"]
        ),

        # Delete in reverse dependency order
        Phase(
            id="delete_passphrases",
            name="Delete DPSK Passphrases",
            executor="workflow.phases.cleanup.DeletePassphrasesPhase",
            depends_on=["inventory"],
            per_unit=False,
            critical=False
        ),

        Phase(
            id="delete_dpsk_pools",
            name="Delete DPSK Pools",
            executor="workflow.phases.cleanup.DeleteDPSKPoolsPhase",
            depends_on=["delete_passphrases"],
            per_unit=False,
            critical=False
        ),

        Phase(
            id="delete_identity_groups",
            name="Delete Identity Groups",
            executor="workflow.phases.cleanup.DeleteIdentityGroupsPhase",
            depends_on=["delete_dpsk_pools"],
            per_unit=False,
            critical=False
        ),

        Phase(
            id="delete_networks",
            name="Delete WiFi Networks",
            executor="workflow.phases.cleanup.DeleteNetworksPhase",
            depends_on=["delete_identity_groups"],
            per_unit=False,
            critical=False
        ),

        Phase(
            id="delete_ap_groups",
            name="Delete AP Groups",
            executor="workflow.phases.cleanup.DeleteAPGroupsPhase",
            depends_on=["delete_networks"],
            per_unit=False,
            critical=False
        ),

        Phase(
            id="verify",
            name="Verify Cleanup",
            executor="workflow.phases.cleanup.VerifyCleanupPhase",
            depends_on=["delete_ap_groups"],
            per_unit=False
        ),
    ]
)
```

### 8.2 Inventory Phase

```python
@register_phase("inventory")
class InventoryPhase(PhaseExecutor):
    """Inventory all resources in the venue for cleanup."""

    async def execute(self, inputs: InventoryInputs) -> InventoryOutputs:
        """Find all resources to delete."""

        # Fetch everything
        ap_groups = await self.r1.wifi.list_ap_groups(self.venue_id)
        networks = await self.r1.wifi.list_networks(self.venue_id)
        identity_groups = await self.r1.identity.list_identity_groups(self.tenant_id)
        dpsk_pools = await self.r1.identity.list_dpsk_pools(self.tenant_id)

        # Filter by venue association (for tenant-wide resources)
        venue_idgs = [g for g in identity_groups if self._is_venue_resource(g)]
        venue_pools = [p for p in dpsk_pools if self._is_venue_resource(p)]

        # Optionally filter by naming pattern
        if inputs.name_pattern:
            import re
            pattern = re.compile(inputs.name_pattern)
            ap_groups = [g for g in ap_groups if pattern.match(g['name'])]
            networks = [n for n in networks if pattern.match(n['name'])]
            venue_idgs = [g for g in venue_idgs if pattern.match(g['name'])]
            venue_pools = [p for p in venue_pools if pattern.match(p['name'])]

        return InventoryOutputs(
            ap_groups=ap_groups,
            networks=networks,
            identity_groups=venue_idgs,
            dpsk_pools=venue_pools,
            summary={
                "ap_groups": len(ap_groups),
                "networks": len(networks),
                "identity_groups": len(venue_idgs),
                "dpsk_pools": len(venue_pools)
            }
        )
```

---

## 9. Frontend Visualization

### 9.1 Workflow Graph API

```python
@router.get("/workflows/{workflow_name}/graph")
async def get_workflow_graph(workflow_name: str):
    """
    Get workflow graph for visualization.

    Returns nodes and edges for rendering with React Flow or similar.
    """
    workflow = get_workflow(workflow_name)

    nodes = []
    edges = []

    # Build node positions (simple left-to-right layout)
    phase_levels = _compute_phase_levels(workflow.phases)

    for phase in workflow.phases:
        level = phase_levels[phase.id]
        nodes.append({
            "id": phase.id,
            "type": "phase",
            "data": {
                "label": phase.name,
                "description": phase.description,
                "per_unit": phase.per_unit,
                "critical": phase.critical,
                "api_calls": phase.api_calls_per_unit
            },
            "position": {"x": level * 250, "y": _get_y_position(phase.id, level)}
        })

        for dep in phase.depends_on:
            edges.append({
                "id": f"{dep}->{phase.id}",
                "source": dep,
                "target": phase.id,
                "animated": False
            })

    return {
        "nodes": nodes,
        "edges": edges,
        "workflow": {
            "name": workflow.name,
            "description": workflow.description,
            "total_phases": len(workflow.phases)
        }
    }


@router.get("/workflows/jobs/{job_id}/live-graph")
async def get_live_workflow_graph(job_id: str):
    """
    Get live workflow graph with current status.

    Extends the static graph with:
    - Per-unit status indicators
    - Phase completion states
    - Current activity counts
    """
    job = await state_manager.get_job(job_id)
    workflow = get_workflow(job.workflow_name)

    base_graph = await get_workflow_graph(job.workflow_name)

    # Enhance nodes with status
    for node in base_graph["nodes"]:
        phase_id = node["id"]

        if node["data"]["per_unit"]:
            # Per-unit phase: show unit-level status
            completed = sum(1 for u in job.units.values() if phase_id in u.completed_phases)
            running = sum(1 for u in job.units.values() if u.current_phase == phase_id)
            failed = sum(1 for u in job.units.values() if phase_id in u.failed_phases)

            node["data"]["status"] = {
                "completed": completed,
                "running": running,
                "failed": failed,
                "total": len(job.units)
            }
        else:
            # Global phase: single status
            node["data"]["status"] = job.phase_status.get(phase_id, "PENDING")

    return base_graph
```

### 9.2 Simple React Component

```tsx
// WorkflowGraph.tsx
// A simple visualization without heavy dependencies

interface Phase {
  id: string;
  name: string;
  depends_on: string[];
  status?: {
    completed: number;
    running: number;
    failed: number;
    total: number;
  };
}

export function WorkflowGraph({ phases, jobId }: { phases: Phase[], jobId?: string }) {
  const levels = computeLevels(phases);

  return (
    <div className="workflow-graph">
      {Object.entries(levels).map(([level, phaseIds]) => (
        <div key={level} className="workflow-level">
          {phaseIds.map(phaseId => {
            const phase = phases.find(p => p.id === phaseId)!;
            return (
              <PhaseNode
                key={phase.id}
                phase={phase}
                showProgress={!!jobId}
              />
            );
          })}
        </div>
      ))}

      {/* SVG overlay for edges */}
      <svg className="workflow-edges">
        {phases.flatMap(phase =>
          phase.depends_on.map(dep => (
            <Edge key={`${dep}-${phase.id}`} from={dep} to={phase.id} />
          ))
        )}
      </svg>
    </div>
  );
}

function PhaseNode({ phase, showProgress }: { phase: Phase, showProgress: boolean }) {
  const status = phase.status;

  return (
    <div className={`phase-node ${getStatusClass(status)}`}>
      <div className="phase-name">{phase.name}</div>

      {showProgress && status && (
        <div className="phase-progress">
          <div className="progress-bar">
            <div
              className="progress-fill completed"
              style={{ width: `${(status.completed / status.total) * 100}%` }}
            />
            <div
              className="progress-fill running"
              style={{ width: `${(status.running / status.total) * 100}%` }}
            />
            <div
              className="progress-fill failed"
              style={{ width: `${(status.failed / status.total) * 100}%` }}
            />
          </div>
          <div className="progress-text">
            {status.completed}/{status.total}
          </div>
        </div>
      )}
    </div>
  );
}
```

---

## 10. Migration Path

### 10.1 Strangler Fig Pattern

Rather than a big-bang rewrite, we'll wrap the existing system and migrate incrementally:

```
Week 1-2: Foundation
├── Create workflow/v2/ directory structure
├── Implement new data models (UnitMapping, etc.)
├── Implement RedisStateManagerV2
└── Implement ActivityTracker

Week 3-4: Brain & Simple Workflow
├── Implement WorkflowBrain
├── Create Phase base class with contracts
├── Migrate simplest workflow (Per-Unit PSK)
└── Keep old system running for other workflows

Week 5-6: Complex Workflows
├── Migrate Per-Unit DPSK workflow
├── Migrate Cloudpath DPSK workflow
├── Add validation phase to all workflows
└── Test parallel execution

Week 7-8: Polish & Cleanup
├── Add frontend visualization
├── Implement cleanup workflow
├── Remove legacy code paths
└── Documentation
```

### 10.2 Compatibility Layer

```python
# workflow/compat.py
# Allows gradual migration

async def execute_workflow(
    workflow_name: str,
    job: WorkflowJob,
    use_v2: bool = None
) -> WorkflowJob:
    """
    Execute workflow using either v1 or v2 engine.

    Args:
        workflow_name: Name of workflow
        job: Job to execute
        use_v2: Force v1 or v2 engine (None = auto-detect)
    """
    # Auto-detect based on workflow
    if use_v2 is None:
        use_v2 = workflow_name in V2_ENABLED_WORKFLOWS

    if use_v2:
        # Convert job to v2 format
        job_v2 = convert_to_v2(job)
        brain = WorkflowBrain(...)
        result = await brain.execute_workflow(job_v2)
        return convert_from_v2(result)
    else:
        # Use existing engine
        engine = WorkflowEngine(...)
        return await engine.execute_workflow(job, phase_executors)


# Feature flag for gradual rollout
V2_ENABLED_WORKFLOWS = set()

def enable_v2_for_workflow(workflow_name: str):
    V2_ENABLED_WORKFLOWS.add(workflow_name)
```

---

## 11. File Structure

```
api/workflow/
├── __init__.py
├── models.py              # Keep existing, add V2 models
├── engine.py              # Keep existing (V1)
├── state_manager.py       # Keep existing (V1)
├── executor.py            # Keep existing (V1)
├── events.py              # Enhance for V2
├── compat.py              # NEW: Compatibility layer
│
├── v2/                    # NEW: V2 implementation
│   ├── __init__.py
│   ├── brain.py           # WorkflowBrain orchestrator
│   ├── models.py          # UnitMapping, PhaseContract, etc.
│   ├── state_manager.py   # RedisStateManagerV2
│   ├── activity_tracker.py # Centralized activity polling
│   └── graph.py           # Dependency graph utilities
│
├── phases/                # Shared phases (enhance existing)
│   ├── __init__.py        # Phase registry
│   ├── base.py            # BasePhaseExecutor → PhaseExecutor
│   ├── registry.py        # NEW: Phase registration
│   ├── contracts.py       # NEW: Input/Output contracts
│   │
│   ├── # Existing phases (refactor to use contracts)
│   ├── ap_groups.py
│   ├── identity_groups.py
│   ├── dpsk_pools.py
│   ├── wifi_networks.py
│   ├── passphrases.py
│   ├── ssid_activation.py
│   ├── ap_assignment.py
│   ├── lan_ports.py
│   │
│   ├── # NEW phases
│   ├── validate.py        # Phase 0 validation
│   └── cleanup/           # Cleanup phases
│       ├── __init__.py
│       ├── inventory.py
│       ├── delete_passphrases.py
│       ├── delete_pools.py
│       ├── delete_identity_groups.py
│       ├── delete_networks.py
│       ├── delete_ap_groups.py
│       └── verify.py
│
└── workflows/             # NEW: Workflow definitions
    ├── __init__.py        # Registry
    ├── definition.py      # Workflow, Phase DSL classes
    ├── per_unit_psk.py
    ├── per_unit_dpsk.py
    ├── cloudpath_dpsk.py
    ├── ap_lan_ports.py    # Standalone (reuses same phase from larger workflows)
    └── cleanup.py
```

---

## 12. Implementation Phases

### Phase 1: Foundation (Est. 3-5 days)

**Goal**: Core infrastructure without breaking existing functionality

- [ ] Create `workflow/v2/` directory structure
- [ ] Implement `UnitMapping`, `UnitPlan`, `UnitResolved` models
- [ ] Implement `PhaseContract`, `PhaseInput`, `PhaseOutput` models
- [ ] Implement `RedisStateManagerV2` with unit-level operations
- [ ] Implement `ActivityTracker` with bulk polling
- [ ] Write unit tests for new models

### Phase 2: Brain & Simple Workflow (Est. 4-6 days)

**Goal**: Working V2 engine with simplest workflow

- [ ] Implement `WorkflowBrain` orchestrator
- [ ] Implement `PhaseExecutor` base class with contracts
- [ ] Implement phase registry with `@register_phase` decorator
- [ ] Create `ValidatePhase` (Phase 0)
- [ ] Migrate `CreateAPGroupPhase` to V2
- [ ] Migrate `CreateNetworkPhase` (PSK) to V2
- [ ] Migrate `ActivateNetworkPhase` to V2
- [ ] Migrate `AssignAPsPhase` to V2
- [ ] Define `PerUnitPSKWorkflow` using DSL
- [ ] Test end-to-end with PSK workflow

### Phase 3: DPSK & Parallel Execution (Est. 5-7 days)

**Goal**: Complex workflows with true per-unit parallelism

- [ ] Migrate `CreateIdentityGroupPhase` to V2
- [ ] Migrate `CreateDPSKPoolPhase` to V2
- [ ] Migrate `CreateDPSKNetworkPhase` to V2
- [ ] Migrate `CreatePassphrasesPhase` to V2
- [ ] Define `PerUnitDPSKWorkflow` using DSL
- [ ] Test per-unit parallel execution
- [ ] Verify activity tracking at scale (50+ units)

### Phase 4: Cleanup & Cloudpath (Est. 3-4 days)

**Goal**: Cleanup workflow and Cloudpath migration

- [ ] Implement cleanup phases (inventory, delete_*)
- [ ] Define `VenueCleanupWorkflow`
- [ ] Test nuclear cleanup
- [ ] Migrate Cloudpath DPSK workflow to V2
- [ ] Test Cloudpath parallel passphrase batching

### Phase 5: Frontend & Polish (Est. 3-4 days)

**Goal**: Visualization and production readiness

- [ ] Implement `/workflows/{name}/graph` API
- [ ] Implement `/workflows/jobs/{id}/live-graph` API
- [ ] Create React `WorkflowGraph` component
- [ ] Add workflow graph to job detail page
- [ ] Implement dry-run confirmation flow in UI
- [ ] Documentation and cleanup

### Phase 6: Migration & Cleanup (Est. 2-3 days)

**Goal**: Complete migration, remove legacy code

- [ ] Enable V2 for all workflows
- [ ] Remove legacy phase implementations
- [ ] Remove V1 engine code (or keep for reference)
- [ ] Update all router endpoints to use V2
- [ ] Final testing and validation

---

## Summary

This refactor transforms the workflow system from a hybrid serial/parallel model to a truly parallel, per-unit execution model with:

1. **Explicit contracts**: Phases declare inputs/outputs, brain wires them together
2. **Per-unit parallelism**: Unit 1's Phase 3 doesn't wait for Unit 50's Phase 2
3. **Centralized tracking**: Single activity poller, Redis as source of truth
4. **Validation first**: Phase 0 builds the plan, user confirms, then execute
5. **Visual representation**: Graph API for frontend display

The migration is incremental - we can enable V2 workflow-by-workflow while keeping existing functionality stable.

---

## Questions for Review

1. **API call batching**: Should we batch R1 API calls within a phase (e.g., create 10 AP groups in one call if R1 supports it)?

2. **Failure handling**: When one unit fails, should we:
   - a) Continue other units (current plan)
   - b) Pause and ask user
   - c) Configurable per workflow?

3. **Retry strategy**: Should retries be:
   - a) Per-activity (retry individual R1 calls)
   - b) Per-phase (retry entire phase for a unit)
   - c) Both?

4. **Resource naming**: Should we enforce naming conventions in the Phase 0 validation, or allow flexibility?

5. **Cleanup safety**: For nuclear cleanup, should we require:
   - a) Confirmation only
   - b) Type the venue name to confirm
   - c) Both?
