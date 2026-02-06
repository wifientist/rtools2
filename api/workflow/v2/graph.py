"""
Dependency Graph Utilities

Handles dependency resolution, cycle detection, topological sorting,
and computing parallel execution levels for workflow phases.

Used by:
- WorkflowBrain: determine what work is ready to execute
- Validation: verify workflow definition is valid before running
- Frontend: compute graph layout for visualization
"""

import logging
from typing import Dict, List, Set, Tuple, Optional
from collections import defaultdict

from workflow.v2.models import PhaseDefinitionV2

logger = logging.getLogger(__name__)


class DependencyGraph:
    """
    DAG representation of phase dependencies.

    Supports:
    - Topological sorting
    - Cycle detection
    - Parallel level computation (which phases can run concurrently)
    - Ready-work detection for per-unit execution
    """

    def __init__(self, phases: List[PhaseDefinitionV2]):
        """
        Build graph from phase definitions.

        Args:
            phases: List of phase definitions with depends_on fields
        """
        self.phases = {p.id: p for p in phases}
        self.phase_list = phases

        # Adjacency lists
        self._dependencies: Dict[str, Set[str]] = {}     # phase → set of phases it depends ON
        self._dependents: Dict[str, Set[str]] = {}        # phase → set of phases that depend on IT

        all_ids = set(self.phases.keys())
        for phase in phases:
            # Only include dependencies on phases in this graph
            # (allows filtering out validate phases for visualization)
            self._dependencies[phase.id] = set(phase.depends_on) & all_ids
            if phase.id not in self._dependents:
                self._dependents[phase.id] = set()
            for dep in self._dependencies[phase.id]:
                if dep not in self._dependents:
                    self._dependents[dep] = set()
                self._dependents[dep].add(phase.id)

    # =========================================================================
    # Validation
    # =========================================================================

    def validate(self) -> List[str]:
        """
        Validate the dependency graph.

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        # Check for missing dependencies
        all_phase_ids = set(self.phases.keys())
        for phase_id, deps in self._dependencies.items():
            for dep in deps:
                if dep not in all_phase_ids:
                    errors.append(
                        f"Phase '{phase_id}' depends on unknown phase '{dep}'"
                    )

        # Check for cycles
        cycle = self._detect_cycle()
        if cycle:
            errors.append(
                f"Circular dependency detected: {' → '.join(cycle)}"
            )

        # Check for orphaned phases (no path from root)
        roots = self.get_root_phases()
        if not roots and self.phases:
            errors.append("No root phases found (all phases have dependencies)")

        reachable = set()
        self._collect_reachable(roots, reachable)
        unreachable = all_phase_ids - reachable
        if unreachable:
            errors.append(
                f"Unreachable phases (not connected to roots): {unreachable}"
            )

        return errors

    def _detect_cycle(self) -> Optional[List[str]]:
        """Detect cycles using DFS. Returns cycle path or None."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {pid: WHITE for pid in self.phases}
        parent = {}

        def dfs(node: str) -> Optional[List[str]]:
            color[node] = GRAY
            for dep_of_node in self._dependents.get(node, set()):
                if dep_of_node not in color:
                    continue
                if color[dep_of_node] == GRAY:
                    # Found cycle - reconstruct path
                    cycle = [dep_of_node, node]
                    current = node
                    while current in parent and current != dep_of_node:
                        current = parent[current]
                        cycle.append(current)
                    cycle.reverse()
                    return cycle
                elif color[dep_of_node] == WHITE:
                    parent[dep_of_node] = node
                    result = dfs(dep_of_node)
                    if result:
                        return result
            color[node] = BLACK
            return None

        for phase_id in self.phases:
            if color[phase_id] == WHITE:
                result = dfs(phase_id)
                if result:
                    return result
        return None

    def _collect_reachable(self, start: Set[str], visited: Set[str]) -> None:
        """Collect all phases reachable from start set."""
        for phase_id in start:
            if phase_id in visited:
                continue
            visited.add(phase_id)
            self._collect_reachable(self._dependents.get(phase_id, set()), visited)

    # =========================================================================
    # Topological Sort
    # =========================================================================

    def topological_sort(self) -> List[str]:
        """
        Return phases in valid execution order (Kahn's algorithm).

        Returns:
            List of phase IDs in topological order

        Raises:
            ValueError: If graph has cycles
        """
        in_degree = {pid: len(deps) for pid, deps in self._dependencies.items()}
        queue = [pid for pid, deg in in_degree.items() if deg == 0]
        result = []

        while queue:
            # Sort for deterministic output
            queue.sort()
            node = queue.pop(0)
            result.append(node)

            for dependent in sorted(self._dependents.get(node, set())):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(result) != len(self.phases):
            raise ValueError("Dependency graph contains cycles")

        return result

    # =========================================================================
    # Parallel Level Computation
    # =========================================================================

    def compute_levels(self) -> Dict[int, List[str]]:
        """
        Compute parallel execution levels.

        Level 0: phases with no dependencies (can all run in parallel)
        Level 1: phases that depend only on level 0 (can all run in parallel)
        Level N: phases that depend only on levels < N

        Returns:
            Dict of level → list of phase IDs at that level
        """
        phase_levels: Dict[str, int] = {}
        levels: Dict[int, List[str]] = defaultdict(list)

        topo_order = self.topological_sort()

        for phase_id in topo_order:
            deps = self._dependencies[phase_id]
            if not deps:
                phase_levels[phase_id] = 0
            else:
                phase_levels[phase_id] = max(
                    phase_levels[dep] for dep in deps
                ) + 1

            levels[phase_levels[phase_id]].append(phase_id)

        return dict(levels)

    def get_phase_level(self, phase_id: str) -> int:
        """Get the execution level for a specific phase."""
        levels = self.compute_levels()
        for level, phases in levels.items():
            if phase_id in phases:
                return level
        raise ValueError(f"Unknown phase: {phase_id}")

    # =========================================================================
    # Ready-Work Detection
    # =========================================================================

    def get_root_phases(self) -> Set[str]:
        """Get phases with no dependencies (entry points)."""
        return {
            pid for pid, deps in self._dependencies.items()
            if not deps
        }

    def get_ready_phases(self, completed: Set[str]) -> Set[str]:
        """
        Get phases that are ready to execute given completed phases.

        A phase is ready when all its dependencies are in the completed set.

        Args:
            completed: Set of completed phase IDs

        Returns:
            Set of phase IDs ready to execute
        """
        ready = set()
        for phase_id, deps in self._dependencies.items():
            if phase_id in completed:
                continue  # Already done
            if deps.issubset(completed):
                ready.add(phase_id)
        return ready

    def get_ready_work_for_unit(
        self,
        unit_completed: Set[str],
        unit_current: Optional[str],
        global_completed: Set[str]
    ) -> Set[str]:
        """
        Get phases ready for a specific unit.

        Considers both per-unit and global phase completions.
        A per-unit phase depends on per-unit completions for THAT unit.
        A per-unit phase can also depend on global phases.

        Args:
            unit_completed: Phases completed for this unit
            unit_current: Phase currently running for this unit (None if idle)
            global_completed: Global (non-per-unit) phases that are completed

        Returns:
            Set of phase IDs ready for this unit
        """
        if unit_current is not None:
            return set()  # Unit is busy

        all_completed = unit_completed | global_completed
        ready = set()

        for phase_id, deps in self._dependencies.items():
            if phase_id in all_completed:
                continue
            phase = self.phases.get(phase_id)
            if not phase or not phase.per_unit:
                continue  # Skip global phases (handled separately)
            if deps.issubset(all_completed):
                ready.add(phase_id)

        return ready

    def get_dependents(self, phase_id: str) -> Set[str]:
        """Get phases that depend on the given phase."""
        return self._dependents.get(phase_id, set())

    def get_dependencies(self, phase_id: str) -> Set[str]:
        """Get phases that the given phase depends on."""
        return self._dependencies.get(phase_id, set())

    # =========================================================================
    # Visualization Helpers
    # =========================================================================

    def to_graph_data(self) -> Dict:
        """
        Convert to a format suitable for frontend visualization.

        Returns:
            Dict with 'nodes' and 'edges' lists for rendering
        """
        levels = self.compute_levels()
        nodes = []
        edges = []

        # Compute Y positions per level (spread nodes vertically)
        for level, phase_ids in sorted(levels.items()):
            for i, phase_id in enumerate(phase_ids):
                phase = self.phases[phase_id]
                nodes.append({
                    "id": phase_id,
                    "type": "phase",
                    "data": {
                        "label": phase.name,
                        "description": phase.description,
                        "per_unit": phase.per_unit,
                        "critical": phase.critical,
                        "api_calls_per_unit": phase.api_calls_per_unit,
                        "inputs": [inp.name for inp in phase.contract.inputs],
                        "outputs": [out.name for out in phase.contract.outputs],
                    },
                    "position": {
                        "x": level * 280,
                        "y": i * 120
                    }
                })

        # Build edges
        for phase_id, deps in self._dependencies.items():
            for dep in deps:
                edges.append({
                    "id": f"{dep}->{phase_id}",
                    "source": dep,
                    "target": phase_id,
                })

        return {"nodes": nodes, "edges": edges}

    def __repr__(self) -> str:
        levels = self.compute_levels()
        lines = []
        for level in sorted(levels.keys()):
            phase_ids = levels[level]
            lines.append(f"  Level {level}: {', '.join(phase_ids)}")
        return f"DependencyGraph(\n{chr(10).join(lines)}\n)"
