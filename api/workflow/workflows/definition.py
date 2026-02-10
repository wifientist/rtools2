"""
Workflow Definition DSL

Clean Python DSL for defining workflows as compositions of independent phases.

Usage:
    from workflow.workflows.definition import Workflow, Phase

    PerUnitPSKWorkflow = Workflow(
        name="per_unit_psk",
        description="Configure per-unit PSK SSIDs",
        phases=[
            Phase(
                id="validate",
                name="Validate & Plan",
                executor="workflow.phases.validate.ValidatePhase",
                per_unit=False,
            ),
            Phase(
                id="create_ap_groups",
                name="Create AP Groups",
                executor="workflow.phases.ap_groups.CreateAPGroupPhase",
                depends_on=["validate"],
            ),
            ...
        ]
    )
"""

from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Union
from workflow.v2.models import PhaseDefinitionV2, PhaseContract, PhaseInput, PhaseOutput


class Phase(BaseModel):
    """
    Phase definition within a workflow.

    Simplified DSL that maps to PhaseDefinitionV2.
    """
    id: str                          # Unique phase ID
    name: str                        # Human-readable name
    description: str = ""
    executor: str                    # Python path to executor class

    # Dependencies
    depends_on: List[str] = Field(default_factory=list)

    # Execution behavior
    per_unit: bool = True            # Runs once per unit (True) or once globally (False)
    critical: bool = True            # Stop workflow if this fails?

    # Skip condition (Python expression)
    skip_if: Optional[str] = None

    # Contract hints (used for documentation, actual contract comes from executor class)
    inputs: List[str] = Field(default_factory=list)     # e.g., ["identity_group_id", "venue_id"]
    outputs: List[str] = Field(default_factory=list)    # e.g., ["dpsk_pool_id"]

    # API call estimate for dry-run display
    api_calls_per_unit: Union[int, str] = 1  # int or "dynamic"

    # Activation slot group - for R1's 15-SSID-per-AP-Group limit
    # Phases with activation_slot="acquire" get a slot before starting
    # Phases with activation_slot="release" release the slot after completing
    # This ensures the activate→assign cycle completes before too many SSIDs are in-flight
    activation_slot: Optional[str] = None  # "acquire" | "release" | None

    def to_definition(self) -> PhaseDefinitionV2:
        """Convert to V2 phase definition model."""
        contract = PhaseContract(
            inputs=[PhaseInput(name=n) for n in self.inputs],
            outputs=[PhaseOutput(name=n) for n in self.outputs],
        )

        return PhaseDefinitionV2(
            id=self.id,
            name=self.name,
            description=self.description,
            contract=contract,
            depends_on=self.depends_on,
            executor=self.executor,
            critical=self.critical,
            per_unit=self.per_unit,
            skip_if=self.skip_if,
            api_calls_per_unit=self.api_calls_per_unit,
            activation_slot=self.activation_slot,
        )


class Workflow(BaseModel):
    """
    Complete workflow definition.

    A workflow is a named composition of independent phases with dependencies.
    """
    name: str                        # Unique workflow name
    description: str = ""
    phases: List[Phase]

    # Workflow-level settings
    requires_confirmation: bool = True   # Pause after validation for user confirmation?
    default_options: Dict[str, Any] = Field(default_factory=dict)

    # Activation slot limit - for R1's 15-SSID-per-AP-Group limit
    # Controls how many units can have an "in-flight" SSID activation
    # (activated on venue but not yet assigned to specific AP Group)
    # Default 12 leaves buffer for existing venue-wide SSIDs
    max_activation_slots: int = 12

    def get_phase(self, phase_id: str) -> Optional[Phase]:
        """Get a phase by ID."""
        for phase in self.phases:
            if phase.id == phase_id:
                return phase
        return None

    def get_phase_definitions(self) -> List[PhaseDefinitionV2]:
        """Convert all phases to V2 definitions."""
        return [p.to_definition() for p in self.phases]

    def get_phase_ids(self) -> List[str]:
        """Get all phase IDs in definition order."""
        return [p.id for p in self.phases]

    def get_per_unit_phases(self) -> List[Phase]:
        """Get phases that run per-unit."""
        return [p for p in self.phases if p.per_unit]

    def get_global_phases(self) -> List[Phase]:
        """Get phases that run once globally."""
        return [p for p in self.phases if not p.per_unit]

    def validate_definition(self) -> List[str]:
        """
        Validate the workflow definition for internal consistency.

        Returns:
            List of error messages (empty if valid)
        """
        from workflow.v2.graph import DependencyGraph

        errors = []

        # Check for duplicate IDs
        ids = [p.id for p in self.phases]
        dupes = [pid for pid in ids if ids.count(pid) > 1]
        if dupes:
            errors.append(f"Duplicate phase IDs: {set(dupes)}")

        # Validate dependency graph
        definitions = self.get_phase_definitions()
        graph = DependencyGraph(definitions)
        graph_errors = graph.validate()
        errors.extend(graph_errors)

        return errors
