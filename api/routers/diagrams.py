"""
Network diagram generation API endpoints.

Generates fossflow-compatible diagram JSON from network topology data.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Literal
from datetime import datetime
import httpx
import os

router = APIRouter(prefix="/diagrams")

# Fossflow backend URL
# When running in Docker, use the container name
# When running locally, use localhost
FOSSFLOW_API_URL = os.getenv("FOSSFLOW_API_URL", "http://rtools-fossflow-dev:3001")


# Fossflow data models
class Position(BaseModel):
    x: float
    y: float


class Color(BaseModel):
    id: str
    value: str  # hex color


class DiagramItem(BaseModel):
    id: str
    icon: str  # e.g., "server", "router", "switch-module" from isoflow isopack
    name: str
    description: Optional[str] = None


class Connector(BaseModel):
    id: str
    from_node: str = Field(alias="from")  # Use alias to serialize as 'from'
    to: str
    name: Optional[str] = None
    color: Optional[str] = None

    model_config = {
        'populate_by_name': True
    }


class FossFLowDiagram(BaseModel):
    """Complete fossflow diagram structure"""
    title: str
    icons: List[str] = []
    colors: List[Color] = []
    items: List[DiagramItem] = []
    connectors: List[Connector] = []
    views: List[Dict] = []
    fitToScreen: bool = True


class NetworkDiagramRequest(BaseModel):
    """Request to generate a network diagram"""
    venue_id: Optional[str] = None
    controller_id: Optional[str] = None
    layout: Literal["auto", "hierarchical", "grid"] = "auto"
    include_clients: bool = False


async def _generate_diagram_data(request: NetworkDiagramRequest) -> dict:
    """
    Internal function to generate diagram data.
    Separated for reuse in different endpoints.
    """
    # Default color palette
    colors = [
        {"id": "blue", "value": "#0066cc"},
        {"id": "green", "value": "#00aa00"},
        {"id": "red", "value": "#cc0000"},
        {"id": "orange", "value": "#ff9900"},
        {"id": "purple", "value": "#9900cc"},
        {"id": "gray", "value": "#666666"},
    ]

    # Mock data - Phase 1
    # TODO: Replace with actual API calls to SmartZone/R1
    # Using isoflow icon names (server, router, switch-module)
    # Items only contain data, not position - position is in the view
    items = [
        {
            "id": "controller-1",
            "icon": "server",  # SmartZone controller - using server icon from isoflow
            "name": "SmartZone Controller",
            "description": "Virtual SmartZone 7.1.1"
        },
        {
            "id": "switch-1",
            "icon": "switch-module",  # Network switch - using switch-module icon from isoflow
            "name": "Core Switch",
            "description": "Distribution layer"
        },
        {
            "id": "ap-1",
            "icon": "router",  # Access point - using router icon from isoflow
            "name": "R650-01",
            "description": "Wi-Fi 6 AP - Floor 1 East"
        },
        {
            "id": "ap-2",
            "icon": "router",  # Access point - using router icon from isoflow
            "name": "R650-02",
            "description": "Wi-Fi 6 AP - Floor 1 West"
        },
        {
            "id": "ap-3",
            "icon": "router",  # Access point - using router icon from isoflow
            "name": "R750-01",
            "description": "Wi-Fi 6 AP - Floor 2"
        },
    ]

    # Define positions for layout (used for view generation)
    # In a real implementation, this would come from layout algorithm
    positions = {
        "controller-1": {"x": 400, "y": 100},
        "switch-1": {"x": 400, "y": 250},
        "ap-1": {"x": 250, "y": 400},
        "ap-2": {"x": 400, "y": 400},
        "ap-3": {"x": 550, "y": 400},
    }

    # Connectors with proper anchor structure for fossflow/isoflow
    # Each connector needs anchors array with start/end references to items
    connectors = [
        {
            "id": "c1",
            "description": "Management",
            "color": "blue",
            "anchors": [
                {"id": "c1-start", "ref": {"item": "controller-1"}},
                {"id": "c1-end", "ref": {"item": "switch-1"}}
            ]
        },
        {
            "id": "c2",
            "description": "VLAN 100",
            "color": "green",
            "anchors": [
                {"id": "c2-start", "ref": {"item": "switch-1"}},
                {"id": "c2-end", "ref": {"item": "ap-1"}}
            ]
        },
        {
            "id": "c3",
            "description": "VLAN 100",
            "color": "green",
            "anchors": [
                {"id": "c3-start", "ref": {"item": "switch-1"}},
                {"id": "c3-end", "ref": {"item": "ap-2"}}
            ]
        },
        {
            "id": "c4",
            "description": "VLAN 100",
            "color": "green",
            "anchors": [
                {"id": "c4-start", "ref": {"item": "switch-1"}},
                {"id": "c4-end", "ref": {"item": "ap-3"}}
            ]
        },
    ]

    # Create a view that contains the connectors and positions
    # In fossflow/isoflow, connectors belong inside views, not at the top level
    # The tile coordinates are grid positions in isometric view (not pixel positions)
    # Convert pixel positions to tile grid (rough approximation: divide by 150 for spacing)
    view_items = []
    for item in items:
        item_id = item["id"]
        pos = positions[item_id]
        tile_x = int(pos["x"] / 150)
        tile_y = int(pos["y"] / 150)
        view_items.append({
            "id": item_id,
            "tile": {"x": tile_x, "y": tile_y},
            "labelHeight": 80  # Default label height (80 is standard, not 0 or 1)
        })

    view = {
        "id": "default-view",
        "name": "Network View",
        "description": "Auto-generated network topology view",
        "items": view_items,
        "connectors": connectors
    }

    return {
        "title": f"Network Diagram - {request.venue_id or 'Mock Venue'}",
        "icons": [],
        "colors": colors,
        "items": items,
        "views": [view],
        "fitToScreen": True
    }


@router.post("/network/generate", response_model=FossFLowDiagram)
async def generate_network_diagram(request: NetworkDiagramRequest):
    """
    Generate a network topology diagram in fossflow format.

    This endpoint will fetch network data from SmartZone/R1 API
    and generate a fossflow-compatible diagram JSON.

    Phase 1: Returns mock data with basic layout
    Future: Will fetch real data from controllers/venues
    """
    return await _generate_diagram_data(request)


@router.post("/network/generate-and-save")
async def generate_and_save_to_fossflow(diagram_request: NetworkDiagramRequest, request: Request):
    """
    Generate a network diagram AND save it to fossflow backend storage.

    This endpoint:
    1. Generates the diagram data from network topology
    2. POSTs it to fossflow's /api/diagrams endpoint
    3. Returns the fossflow diagram ID and access URL

    The diagram will then be available in the fossflow UI.
    """
    # Generate the diagram
    diagram_data = await _generate_diagram_data(diagram_request)

    # Add a name field for the diagram (required by fossflow storage)
    diagram_with_name = {
        **diagram_data,
        "name": diagram_data["title"]  # Use title as name
    }

    # Save to fossflow backend
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{FOSSFLOW_API_URL}/api/diagrams",
                json=diagram_with_name
            )

            if response.status_code in [200, 201]:  # Accept both OK and Created
                result = response.json()
                diagram_id = result.get("id")

                # Build the fossflow URL based on the request's base URL
                # This handles localhost:3000, localhost:8080, and production URLs correctly
                base_url = str(request.base_url).rstrip('/')

                # If accessing through the API (/api/diagrams/...), use the proxy path
                # Otherwise use direct port 3000 access
                if '/api/' in str(request.url):
                    # Accessed through nginx proxy - use /diagrams/ path
                    fossflow_url = f"{base_url}/diagrams/?diagramId={diagram_id}"
                else:
                    # Direct access - use port 3000
                    fossflow_url = f"http://localhost:3000/?diagramId={diagram_id}"

                return {
                    "success": True,
                    "diagram_id": diagram_id,
                    "message": "Diagram saved to fossflow storage",
                    "fossflow_url": fossflow_url,
                    "api_url": f"{FOSSFLOW_API_URL}/api/diagrams/{diagram_id}"
                }
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to save to fossflow: {response.status_code} - {response.text}"
                )

    except httpx.RequestError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Could not connect to fossflow backend at {FOSSFLOW_API_URL}: {str(e)}"
        )


@router.get("/network/{venue_id}", response_model=FossFLowDiagram)
async def get_venue_network_diagram(venue_id: str):
    """
    Get a pre-generated network diagram for a venue.

    This will be stored/cached in the future.
    For now, it calls the generate endpoint.
    """
    request = NetworkDiagramRequest(venue_id=venue_id)
    return await _generate_diagram_data(request)


@router.post("/nodes/add")
async def add_node_to_diagram(
    diagram_id: str,
    node_type: str,
    position: Position,
    name: str,
    description: Optional[str] = None
):
    """
    Programmatically add a node to an existing diagram.

    Phase 2: This will update stored diagrams.
    """
    # TODO: Implement diagram storage and update logic
    return {
        "success": True,
        "message": "Node addition endpoint - implementation pending",
        "node_id": f"node_{datetime.now().timestamp()}"
    }


@router.post("/connectors/add")
async def add_connector_to_diagram(
    diagram_id: str,
    from_node: str,
    to_node: str,
    name: Optional[str] = None,
    color: Optional[str] = None
):
    """
    Programmatically add a connector between two nodes.

    Phase 2: This will update stored diagrams.
    """
    # TODO: Implement diagram storage and update logic
    return {
        "success": True,
        "message": "Connector addition endpoint - implementation pending",
        "connector_id": f"conn_{datetime.now().timestamp()}"
    }


@router.put("/layout/{diagram_id}")
async def apply_layout_algorithm(
    diagram_id: str,
    algorithm: Literal["hierarchical", "grid", "force-directed", "circular"]
):
    """
    Apply a layout algorithm to reposition nodes in a diagram.

    Algorithms:
    - hierarchical: Top-down tree layout
    - grid: Evenly spaced grid
    - force-directed: Physics-based layout
    - circular: Nodes arranged in a circle

    Phase 2: Implement actual layout algorithms.
    """
    # TODO: Implement layout algorithms
    return {
        "success": True,
        "message": f"Layout algorithm '{algorithm}' applied",
        "algorithm": algorithm
    }
