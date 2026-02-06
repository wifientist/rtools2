"""
Workflow Engine V2 Data Models

Core data structures for the V2 workflow engine:
- UnitMapping: Tracks all resources for a single unit throughout a workflow
- PhaseContract: Declares phase input/output requirements
- WorkflowJobV2: Enhanced job with per-unit tracking
- ValidationResult: Phase 0 dry-run output
- ActivityRef: R1 activity tracking

Design principle: Phases declare WHAT they need (inputs), not WHERE it comes from.
The Brain wires outputs to inputs based on the dependency graph.
"""

from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Union
from datetime import datetime
from enum import Enum


# =============================================================================
# Status Enums (compatible with V1, adds new states)
# =============================================================================

class JobStatus(str, Enum):
    """Status of a workflow job"""
    PENDING = "PENDING"              # Created, not yet started
    VALIDATING = "VALIDATING"        # Phase 0 running
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"  # Dry-run complete, waiting for user
    RUNNING = "RUNNING"              # Executing phases
    COMPLETED = "COMPLETED"          # All phases done successfully
    FAILED = "FAILED"                # Critical phase failed
    PARTIAL = "PARTIAL"              # Some non-critical phases failed
    CANCELLED = "CANCELLED"          # User cancelled


class PhaseStatus(str, Enum):
    """Status of a workflow phase"""
    PENDING = "PENDING"              # Not yet started
    READY = "READY"                  # Dependencies met, can start
    RUNNING = "RUNNING"              # Actively executing
    WAITING = "WAITING"              # Tasks fired, awaiting R1 activities
    COMPLETED = "COMPLETED"          # All tasks done
    FAILED = "FAILED"                # Phase failed
    SKIPPED = "SKIPPED"              # Skip condition met


class UnitStatus(str, Enum):
    """Status of a single unit within a workflow"""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"              # Some phases failed, some succeeded


class TaskStatus(str, Enum):
    """Status of an individual task"""
    PENDING = "PENDING"
    RUNNING = "RUNNING"              # API call being made
    IN_PROGRESS = "IN_PROGRESS"      # Alias for RUNNING (V1 compat)
    POLLING = "POLLING"              # Waiting on R1 activity
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"


class Task(BaseModel):
    """Individual unit of work within a phase - used by phase executors"""
    id: str = Field(..., description="Unique task ID")
    name: str = Field(..., description="Human-readable task name")
    task_type: str = Field(default="", description="Task type identifier")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="Current task status")

    # Data
    input_data: Dict[str, Any] = Field(default_factory=dict, description="Input data for task")
    output_data: Dict[str, Any] = Field(default_factory=dict, description="Result data from task")

    # Error handling
    error_message: Optional[str] = Field(None, description="Error message if failed")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class WorkflowDefinition(BaseModel):
    """Complete workflow definition"""
    name: str = Field(..., description="Workflow name")
    description: str = Field(default="", description="Workflow description")
    phases: List['LegacyPhaseDefinition'] = Field(..., description="Phase definitions in execution order")


class LegacyPhaseDefinition(BaseModel):
    """Definition for a workflow phase (used for simple workflow definitions)"""
    id: str = Field(..., description="Unique phase ID")
    name: str = Field(..., description="Human-readable phase name")
    dependencies: List[str] = Field(default_factory=list, description="Phase IDs that must complete first")
    parallelizable: bool = Field(default=True, description="Can tasks run in parallel?")
    critical: bool = Field(default=False, description="Stop entire job if this fails?")
    skip_condition: Optional[str] = Field(None, description="Python expression to evaluate for skipping")
    executor: str = Field(..., description="Fully qualified path to executor function")


# Alias for backward compatibility
PhaseDefinition = LegacyPhaseDefinition


# =============================================================================
# Unit Mapping Models (Core Data Structure)
# =============================================================================

class UnitPlan(BaseModel):
    """
    What WILL be created/reused for this unit.
    Built during Phase 0 validation using names only.
    """
    ap_group_name: Optional[str] = None
    identity_group_name: Optional[str] = None
    dpsk_pool_name: Optional[str] = None
    dpsk_service_name: Optional[str] = None
    network_name: Optional[str] = None
    ssid_name: Optional[str] = None  # Broadcast SSID name (may differ from network_name)

    # Validation results (what already exists)
    ap_group_exists: bool = False
    identity_group_exists: bool = False
    dpsk_pool_exists: bool = False
    dpsk_service_exists: bool = False
    network_exists: bool = False

    # What needs to happen
    will_create_ap_group: bool = True
    will_create_identity_group: bool = False
    will_create_dpsk_pool: bool = False
    will_create_dpsk_service: bool = False
    will_create_network: bool = True

    # Passphrase info
    passphrase_count: int = 0

    # LAN port config
    lan_port_config: Optional[Dict[str, Any]] = None

    # AP info
    ap_serial_numbers: List[str] = Field(default_factory=list)

    # Extensible: workflow-specific plan data
    extra: Dict[str, Any] = Field(default_factory=dict)


class UnitResolved(BaseModel):
    """
    Actual IDs after resources are created or found.
    Enriched progressively as phases complete.
    """
    ap_group_id: Optional[str] = None
    identity_group_id: Optional[str] = None
    dpsk_pool_id: Optional[str] = None
    dpsk_service_id: Optional[str] = None
    network_id: Optional[str] = None
    passphrase_ids: List[str] = Field(default_factory=list)

    # AP assignments
    ap_ids: List[str] = Field(default_factory=list)

    # DPSK pool tracking (for workflows with multiple pools)
    dpsk_pool_ids: Dict[str, str] = Field(default_factory=dict)

    # Passphrase creation tracking
    created_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    created_passphrases: List[Dict[str, Any]] = Field(default_factory=list)
    failed_passphrases: List[Dict[str, Any]] = Field(default_factory=list)

    # Extensible: workflow-specific resolved data
    extra: Dict[str, Any] = Field(default_factory=dict)


class UnitMapping(BaseModel):
    """
    Tracks all resources for a single unit throughout the workflow.
    Starts as a plan (names only), gets enriched with IDs as phases complete.
    Persisted to Redis for multi-worker access.
    """
    unit_id: str                     # e.g., "unit_101"
    unit_number: str                 # Display name, e.g., "101"

    # Plan and resolved data
    plan: UnitPlan = Field(default_factory=UnitPlan)
    resolved: UnitResolved = Field(default_factory=UnitResolved)

    # Phase tracking for this unit
    current_phase: Optional[str] = None
    completed_phases: List[str] = Field(default_factory=list)
    failed_phases: List[str] = Field(default_factory=list)
    phase_errors: Dict[str, str] = Field(default_factory=dict)  # phase_id → error message

    # Unit-level status
    status: UnitStatus = UnitStatus.PENDING

    # Original input config for this unit
    input_config: Dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# Phase Contract Models
# =============================================================================

class PhaseInput(BaseModel):
    """Declares a single input that a phase requires."""
    name: str                        # e.g., "identity_group_id"
    type: str = "str"                # e.g., "str", "List[str]", "Dict"
    required: bool = True
    description: str = ""
    source_hint: Optional[str] = None  # Optional hint: "create_identity_groups"


class PhaseOutput(BaseModel):
    """Declares a single output that a phase produces."""
    name: str                        # e.g., "dpsk_pool_id"
    type: str = "str"
    description: str = ""


class PhaseContract(BaseModel):
    """
    Full input/output contract for a phase.
    Declares WHAT the phase needs and produces, not WHERE it comes from.
    """
    inputs: List[PhaseInput] = Field(default_factory=list)
    outputs: List[PhaseOutput] = Field(default_factory=list)

    def get_required_inputs(self) -> List[str]:
        """Get names of all required inputs."""
        return [i.name for i in self.inputs if i.required]

    def get_output_names(self) -> List[str]:
        """Get names of all outputs."""
        return [o.name for o in self.outputs]


# =============================================================================
# Phase Definition
# =============================================================================

class PhaseDefinitionV2(BaseModel):
    """
    Enhanced phase definition with explicit contracts.
    Used in workflow DSL to define what phases do and how they connect.
    """
    id: str                          # Unique phase ID, e.g., "create_dpsk_pools"
    name: str                        # Human-readable, e.g., "Create DPSK Pools"
    description: str = ""

    # Contract
    contract: PhaseContract = Field(default_factory=PhaseContract)

    # Dependencies (phase IDs that must complete first FOR EACH UNIT)
    depends_on: List[str] = Field(default_factory=list)

    # Execution config
    executor: str                    # Python path to executor class
    critical: bool = True            # Stop entire workflow if this fails?
    per_unit: bool = True            # Run once per unit or once globally?

    # Skip condition (evaluated at runtime)
    skip_if: Optional[str] = None    # e.g., "not options.get('configure_lan_ports')"

    # API call estimate (for dry-run display)
    api_calls_per_unit: Union[int, str] = 1  # int or "dynamic"


# =============================================================================
# Activity Tracking
# =============================================================================

class ActivityRef(BaseModel):
    """Reference to a pending R1 async activity."""
    activity_id: str
    job_id: str
    unit_id: Optional[str] = None    # None for global phases
    phase_id: str
    task_id: Optional[str] = None
    registered_at: datetime = Field(default_factory=datetime.utcnow)


class ActivityResult(BaseModel):
    """Result of a completed R1 activity."""
    activity_id: str
    success: bool
    resource_id: Optional[str] = None
    error: Optional[str] = None
    raw_response: Dict[str, Any] = Field(default_factory=dict)
    completed_at: datetime = Field(default_factory=datetime.utcnow)


# =============================================================================
# Validation / Dry-Run Models
# =============================================================================

class ResourceAction(BaseModel):
    """Describes what will happen to a single resource."""
    resource_type: str               # e.g., "ap_group", "identity_group"
    name: str                        # Resource name
    action: str                      # "create", "reuse", "skip"
    existing_id: Optional[str] = None  # Set if reusing
    notes: List[str] = Field(default_factory=list)  # Warnings, info


class ValidationSummary(BaseModel):
    """Summary statistics from Phase 0 validation."""
    total_units: int
    ap_groups_to_create: int = 0
    ap_groups_to_reuse: int = 0
    identity_groups_to_create: int = 0
    identity_groups_to_reuse: int = 0
    dpsk_pools_to_create: int = 0
    dpsk_pools_to_reuse: int = 0
    networks_to_create: int = 0
    networks_to_reuse: int = 0
    passphrases_to_create: int = 0
    passphrases_to_update: int = 0  # Existing passphrases needing VLAN update
    passphrases_existing: int = 0
    # Access policies
    policies_to_create: int = 0
    policies_existing: int = 0
    radius_groups_to_create: int = 0
    radius_groups_existing: int = 0
    total_api_calls: int = 0


class ConflictDetail(BaseModel):
    """A conflict that blocks workflow execution."""
    unit_id: Optional[str] = None    # None for global conflicts
    resource_type: str
    resource_name: str
    description: str
    severity: str = "error"          # "error" blocks, "warning" does not


class ValidationResult(BaseModel):
    """Complete output from Phase 0 validation."""
    valid: bool                      # Can the workflow proceed?
    conflicts: List[ConflictDetail] = Field(default_factory=list)
    summary: ValidationSummary
    unit_plans: Dict[str, UnitPlan] = Field(default_factory=dict)
    actions: List[ResourceAction] = Field(default_factory=list)


# =============================================================================
# Phase Execution Result
# =============================================================================

class PhaseResult(BaseModel):
    """Result of executing a phase for a unit (or globally)."""
    success: bool
    phase_id: str
    unit_id: Optional[str] = None    # None for global phases
    outputs: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    reused: bool = False             # True if existing resource was found
    duration_ms: Optional[int] = None


# =============================================================================
# Workflow Job V2
# =============================================================================

class WorkflowJobV2(BaseModel):
    """
    Enhanced workflow job with per-unit tracking.
    Stored in Redis as source of truth.
    """
    id: str = Field(..., description="Unique job ID (UUID)")
    workflow_name: str
    status: JobStatus = JobStatus.PENDING

    # Context
    venue_id: str = ""
    tenant_id: str = ""
    controller_id: int = 0
    user_id: int = 0
    options: Dict[str, Any] = Field(default_factory=dict)

    # Unit tracking (THE core data structure)
    units: Dict[str, UnitMapping] = Field(default_factory=dict)

    # Phase definitions (from workflow definition)
    phase_definitions: List[PhaseDefinitionV2] = Field(default_factory=list)

    # Global phase status (for per_unit=False phases)
    global_phase_status: Dict[str, PhaseStatus] = Field(default_factory=dict)
    global_phase_results: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    # Validation result (from Phase 0)
    validation_result: Optional[ValidationResult] = None

    # Created resources (for cleanup reference)
    created_resources: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)

    # Timing
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Input data (original request)
    input_data: Dict[str, Any] = Field(default_factory=dict)

    # Errors
    errors: List[str] = Field(default_factory=list)

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }

    def get_phase_definition(self, phase_id: str) -> Optional[PhaseDefinitionV2]:
        """Get a phase definition by ID."""
        for phase in self.phase_definitions:
            if phase.id == phase_id:
                return phase
        return None

    def get_progress(self) -> Dict[str, Any]:
        """Calculate overall workflow progress."""
        total_units = len(self.units)
        total_phase_defs = len(self.phase_definitions)

        if total_units == 0:
            return {
                "percent": 0,
                "total_units": 0,
                "total_phases": total_phase_defs,
                "completed_phases": 0,
                "failed_phases": 0,
            }

        per_unit_phases = [p for p in self.phase_definitions if p.per_unit]
        total_phase_count = len(per_unit_phases)
        if total_phase_count == 0:
            return {
                "percent": 0,
                "total_units": total_units,
                "total_phases": total_phase_defs,
                "completed_phases": 0,
                "failed_phases": 0,
            }

        total_work = total_units * total_phase_count
        completed_work = sum(
            len([p for p in unit.completed_phases if self._is_per_unit_phase(p)])
            for unit in self.units.values()
        )

        # Count global phases
        global_phases = [p for p in self.phase_definitions if not p.per_unit]
        total_work += len(global_phases)
        completed_work += sum(
            1 for p in global_phases
            if self.global_phase_status.get(p.id) == PhaseStatus.COMPLETED
        )

        percent = (completed_work / total_work * 100) if total_work > 0 else 0

        # Per-unit breakdown
        units_completed = sum(
            1 for u in self.units.values() if u.status == UnitStatus.COMPLETED
        )
        units_failed = sum(
            1 for u in self.units.values() if u.status == UnitStatus.FAILED
        )
        units_running = sum(
            1 for u in self.units.values() if u.status == UnitStatus.RUNNING
        )

        # Count completed/failed phases (for UI display)
        completed_phases = sum(
            1 for p in self.phase_definitions
            if self.global_phase_status.get(p.id) == PhaseStatus.COMPLETED
        )
        failed_phases = sum(
            1 for p in self.phase_definitions
            if self.global_phase_status.get(p.id) == PhaseStatus.FAILED
        )

        # Per-phase breakdown (for UI display)
        phase_stats = {}
        for phase_def in self.phase_definitions:
            if phase_def.per_unit:
                completed = sum(1 for u in self.units.values() if phase_def.id in u.completed_phases)
                failed = sum(1 for u in self.units.values() if phase_def.id in u.failed_phases)
                running = sum(1 for u in self.units.values() if u.current_phase == phase_def.id)
                phase_stats[phase_def.id] = {
                    "name": phase_def.name,
                    "completed": completed,
                    "failed": failed,
                    "running": running,
                    "pending": total_units - completed - failed - running,
                    "total": total_units,
                }
            else:
                # Global phase
                status = self.global_phase_status.get(phase_def.id, PhaseStatus.PENDING)
                phase_stats[phase_def.id] = {
                    "name": phase_def.name,
                    "status": status.value if hasattr(status, 'value') else str(status),
                }

        return {
            "percent": round(percent, 1),
            "total_units": total_units,
            "units_completed": units_completed,
            "units_failed": units_failed,
            "units_running": units_running,
            "units_pending": total_units - units_completed - units_failed - units_running,
            "total_phases": len(self.phase_definitions),
            "completed_phases": completed_phases,
            "failed_phases": failed_phases,
            "completed_work": completed_work,
            "total_work": total_work,
            "phase_stats": phase_stats,
        }

    def _is_per_unit_phase(self, phase_id: str) -> bool:
        """Check if a phase is per-unit."""
        defn = self.get_phase_definition(phase_id)
        return defn.per_unit if defn else False

    def get_phase_aggregate_status(self, phase_id: str) -> PhaseStatus:
        """
        Get the aggregate status of a phase across all units.

        For global phases: returns global_phase_status directly.
        For per-unit phases: aggregates across all units:
          - COMPLETED if all units have completed it
          - RUNNING if any unit is currently running it
          - FAILED if any unit has failed it (and none running)
          - PENDING otherwise

        Args:
            phase_id: Phase ID to check

        Returns:
            Aggregate PhaseStatus
        """
        defn = self.get_phase_definition(phase_id)
        if not defn:
            return PhaseStatus.PENDING

        # Global phases - use global_phase_status directly
        if not defn.per_unit:
            return self.global_phase_status.get(phase_id, PhaseStatus.PENDING)

        # Per-unit phases - aggregate across all units
        if not self.units:
            return PhaseStatus.PENDING

        completed_count = 0
        running_count = 0
        failed_count = 0

        for unit in self.units.values():
            if phase_id in unit.completed_phases:
                completed_count += 1
            elif unit.current_phase == phase_id:
                running_count += 1
            elif phase_id in unit.failed_phases:
                failed_count += 1

        total_units = len(self.units)

        if completed_count == total_units:
            return PhaseStatus.COMPLETED
        elif running_count > 0:
            return PhaseStatus.RUNNING
        elif failed_count > 0 and completed_count + failed_count == total_units:
            return PhaseStatus.FAILED
        elif completed_count > 0:
            # Some completed, some pending - treat as running
            return PhaseStatus.RUNNING
        else:
            return PhaseStatus.PENDING
