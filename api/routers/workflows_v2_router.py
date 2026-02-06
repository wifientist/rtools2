"""
V2 Workflow Discovery & Graph Router

Generic endpoints for workflow discovery and DAG visualization.
Works with any registered V2 workflow definition.

Endpoints:
- GET /workflows/v2                    → List all registered workflows
- GET /workflows/v2/{name}/graph       → Static graph for any workflow
- GET /workflows/v2/{name}/phases      → Phase details for any workflow
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

from dependencies import get_current_user
from models.user import User
from workflow.workflows import (
    list_workflows,
    get_workflow,
    get_all_workflows,
)
from workflow.v2.graph import DependencyGraph

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workflows/v2",
    tags=["Workflow Engine V2"],
)


# ==================== Response Models ====================

class WorkflowSummary(BaseModel):
    """Summary of a registered workflow."""
    name: str
    description: str
    phase_count: int
    requires_confirmation: bool = False
    phases: List[str] = Field(
        default_factory=list,
        description="Phase IDs in topological order",
    )


class WorkflowListResponse(BaseModel):
    """List of all registered workflows."""
    workflows: List[WorkflowSummary]
    total: int


class GraphNode(BaseModel):
    """A node (phase) in the workflow graph."""
    id: str
    type: str = "phase"
    data: Dict[str, Any] = Field(default_factory=dict)
    position: Dict[str, float] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    """An edge (dependency) in the workflow graph."""
    id: str
    source: str
    target: str


class WorkflowGraphResponse(BaseModel):
    """Workflow graph for visualization."""
    workflow_name: str
    description: str
    nodes: List[Dict[str, Any]] = Field(default_factory=list)
    edges: List[Dict[str, Any]] = Field(default_factory=list)
    levels: Dict[str, List[str]] = Field(default_factory=dict)
    phase_count: int = 0


class PhaseDetail(BaseModel):
    """Detailed info about a single phase."""
    id: str
    name: str
    description: str = ""
    per_unit: bool = False
    critical: bool = True
    depends_on: List[str] = Field(default_factory=list)
    inputs: List[str] = Field(default_factory=list)
    outputs: List[str] = Field(default_factory=list)
    api_calls_per_unit: Any = 0


class WorkflowPhasesResponse(BaseModel):
    """Full phase details for a workflow."""
    workflow_name: str
    description: str
    phases: List[PhaseDetail]


# ==================== API Endpoints ====================

@router.get("", response_model=WorkflowListResponse)
async def list_registered_workflows(
    current_user: User = Depends(get_current_user),
):
    """
    List all registered V2 workflow definitions.

    Returns workflow names, descriptions, and phase counts.
    """
    all_wfs = get_all_workflows()
    summaries = []

    for name, wf in sorted(all_wfs.items()):
        phase_ids = [p.id for p in wf.phases]
        summaries.append(WorkflowSummary(
            name=wf.name,
            description=wf.description,
            phase_count=len(wf.phases),
            requires_confirmation=wf.requires_confirmation,
            phases=phase_ids,
        ))

    return WorkflowListResponse(
        workflows=summaries,
        total=len(summaries),
    )


@router.get("/{workflow_name}/graph", response_model=WorkflowGraphResponse)
async def get_workflow_graph(
    workflow_name: str,
    include_validate: bool = False,
    current_user: User = Depends(get_current_user),
):
    """
    Get the static workflow dependency graph for visualization.

    Returns nodes and edges suitable for rendering as a DAG.
    By default excludes the validate phase (Phase 0) since it runs
    before the user sees the execution graph.

    Set include_validate=true to include it.
    """
    try:
        wf = get_workflow(workflow_name)
    except ValueError:
        available = list_workflows()
        raise HTTPException(
            status_code=404,
            detail=(
                f"Workflow '{workflow_name}' not found. "
                f"Available: {available}"
            ),
        )

    definitions = wf.get_phase_definitions()

    if not include_validate:
        definitions = [
            d for d in definitions
            if not d.id.startswith("validate")
        ]

    graph = DependencyGraph(definitions)
    graph_data = graph.to_graph_data()

    return WorkflowGraphResponse(
        workflow_name=wf.name,
        description=wf.description,
        nodes=graph_data["nodes"],
        edges=graph_data["edges"],
        levels={
            str(k): v
            for k, v in graph.compute_levels().items()
        },
        phase_count=len(definitions),
    )


@router.get(
    "/{workflow_name}/phases",
    response_model=WorkflowPhasesResponse,
)
async def get_workflow_phases(
    workflow_name: str,
    current_user: User = Depends(get_current_user),
):
    """
    Get detailed phase information for a workflow.

    Returns all phases with their inputs, outputs, dependencies,
    and execution characteristics.
    """
    try:
        wf = get_workflow(workflow_name)
    except ValueError:
        available = list_workflows()
        raise HTTPException(
            status_code=404,
            detail=(
                f"Workflow '{workflow_name}' not found. "
                f"Available: {available}"
            ),
        )

    phases = []
    for p in wf.phases:
        phases.append(PhaseDetail(
            id=p.id,
            name=p.name,
            description=p.description,
            per_unit=p.per_unit,
            critical=p.critical,
            depends_on=p.depends_on,
            inputs=p.inputs,
            outputs=p.outputs,
            api_calls_per_unit=p.api_calls_per_unit,
        ))

    return WorkflowPhasesResponse(
        workflow_name=wf.name,
        description=wf.description,
        phases=phases,
    )
