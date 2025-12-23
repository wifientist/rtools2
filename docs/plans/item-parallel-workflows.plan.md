# Item-Parallel Workflow Execution Plan

**Status**: Planned (not implemented)
**Created**: 2024-12-23
**Purpose**: Support per-item workflow execution as an alternative to phase-first execution

## Problem Statement

Current workflow execution is **phase-first**:
```
Phase 1: Create SSID for Unit1, Unit2, Unit3... (all units)
    ↓
Phase 2: Create AP Group for Unit1, Unit2, Unit3... (all units)
    ↓
Phase 3: Assign AP for Unit1, Unit2, Unit3... (all units)
```

For some workflows, **item-first** execution is more appropriate:
```
Unit 1: Create SSID → Create AP Group → Assign AP → Done
    ↓ (parallel)
Unit 2: Create SSID → Create AP Group → Assign AP → Done
    ↓ (parallel)
Unit 3: ...
```

## Design Goals

1. Support both execution modes with a simple flag
2. Run items in parallel (with concurrency limits)
3. Preserve partial results - failed items don't stop others
4. Support mixed phases (some run once globally, some run per-item)
5. Minimal changes to existing phase executors

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      WorkflowJob                                │
│  execution_mode: "item_parallel"                                │
│  item_key: "units"                                              │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ VirtualItem  │  │ VirtualItem  │  │ VirtualItem  │   ...    │
│  │   Unit 101   │  │   Unit 102   │  │   Unit 103   │          │
│  ├──────────────┤  ├──────────────┤  ├──────────────┤          │
│  │ create_ssid  │  │ create_ssid  │  │ create_ssid  │          │
│  │      ↓       │  │      ↓       │  │      ↓       │          │
│  │ create_group │  │ create_group │  │ create_group │          │
│  │      ↓       │  │      ↓       │  │      ↓       │          │
│  │ assign_ap    │  │ assign_ap    │  │ assign_ap    │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│         ↓                ↓                  ↓                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              Aggregate Results (final phase)             │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Model Changes

### 1. New Enums (models.py)

```python
class ExecutionMode(str, Enum):
    PHASE_FIRST = "phase_first"      # Current behavior
    ITEM_PARALLEL = "item_parallel"  # Per-item workflows in parallel

class PhaseScope(str, Enum):
    PER_ITEM = "per_item"            # Runs once per item
    PER_WORKFLOW = "per_workflow"    # Runs once for entire workflow
```

### 2. Extend WorkflowDefinition (models.py:205-210)

```python
class WorkflowDefinition(BaseModel):
    name: str
    description: str
    phases: List[PhaseDefinition]
    # New fields:
    execution_mode: ExecutionMode = ExecutionMode.PHASE_FIRST
    item_key: Optional[str] = None  # e.g., "units" - which input_data field has items
    max_concurrent_items: int = 50  # Concurrency limit for item_parallel mode
```

### 3. Extend PhaseDefinition (models.py:194-202)

```python
class PhaseDefinition(BaseModel):
    id: str
    name: str
    dependencies: List[str] = []
    parallelizable: bool = True
    critical: bool = False
    skip_condition: Optional[str] = None
    executor: str
    # New field:
    scope: PhaseScope = PhaseScope.PER_ITEM  # Default for backwards compat
```

### 4. New ItemResult Model (models.py)

```python
class ItemResult(BaseModel):
    """Tracks result of processing a single item through all its phases"""
    item_id: str                              # e.g., unit_number
    item_data: Dict[str, Any]                 # Original item data
    status: Literal["pending", "running", "completed", "failed"]
    phase_results: Dict[str, Dict[str, Any]]  # phase_id → result
    errors: List[str] = []
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
```

### 5. Extend WorkflowJob (models.py:99-128)

```python
class WorkflowJob(BaseModel):
    # ... existing fields ...

    # New for item_parallel mode:
    item_results: Dict[str, ItemResult] = {}  # item_id → ItemResult
```

---

## Engine Changes

### 1. Branch in execute_workflow (engine.py:53-169)

```python
async def execute_workflow(
    self,
    job: WorkflowJob,
    phase_executors: Dict[str, Callable],
    workflow_definition: WorkflowDefinition  # New parameter
) -> WorkflowJob:

    # ... initial setup ...

    if workflow_definition.execution_mode == ExecutionMode.ITEM_PARALLEL:
        return await self._execute_item_parallel(
            job,
            phase_executors,
            workflow_definition
        )
    else:
        # Current phase-first logic (unchanged)
        return await self._execute_phase_first(job, phase_executors)
```

### 2. New Method: _execute_item_parallel

```python
async def _execute_item_parallel(
    self,
    job: WorkflowJob,
    phase_executors: Dict[str, Callable],
    definition: WorkflowDefinition
) -> WorkflowJob:
    """
    Execute workflow in item-parallel mode.
    Each item runs through all phases before the next item starts.
    Items run in parallel up to max_concurrent_items.
    """
    # 1. Extract items from input_data
    items = job.input_data.get(definition.item_key, [])
    if not items:
        job.status = JobStatus.COMPLETED
        return job

    # 2. Separate phases by scope
    per_workflow_phases = [p for p in definition.phases if p.scope == PhaseScope.PER_WORKFLOW]
    per_item_phases = [p for p in definition.phases if p.scope == PhaseScope.PER_ITEM]

    # 3. Execute PER_WORKFLOW phases first (e.g., shared setup)
    for phase_def in self._resolve_dependencies(per_workflow_phases):
        await self._execute_phase(job, phase_def, phase_executors, job.options)

    # 4. Initialize item results
    for item in items:
        item_id = self._get_item_id(item, definition)
        job.item_results[item_id] = ItemResult(
            item_id=item_id,
            item_data=item,
            status="pending",
            phase_results={}
        )

    # 5. Process items in parallel with semaphore
    semaphore = asyncio.Semaphore(definition.max_concurrent_items)

    async def process_single_item(item: Dict) -> ItemResult:
        async with semaphore:
            return await self._execute_item_workflow(
                job, item, per_item_phases, phase_executors
            )

    # 6. Run all items concurrently
    await asyncio.gather(
        *[process_single_item(item) for item in items],
        return_exceptions=True  # Don't fail entire job if one item fails
    )

    # 7. Execute any trailing PER_WORKFLOW phases (e.g., cleanup, aggregation)
    # These would have dependencies on per-item phases and run after all items complete

    # 8. Aggregate final status
    job = self._aggregate_item_results(job)
    return job
```

### 3. New Method: _execute_item_workflow

```python
async def _execute_item_workflow(
    self,
    job: WorkflowJob,
    item: Dict[str, Any],
    phases: List[PhaseDefinition],
    phase_executors: Dict[str, Callable]
) -> ItemResult:
    """
    Execute all phases for a single item sequentially.
    This is the 'virtual workflow' for one item.
    """
    item_id = self._get_item_id(item)
    item_result = job.item_results[item_id]
    item_result.status = "running"
    item_result.started_at = datetime.utcnow()

    # Build item-specific context
    item_context = {
        'job_id': job.id,
        'item': item,  # Single item, not list!
        'item_id': item_id,
        'venue_id': job.venue_id,
        'tenant_id': job.tenant_id,
        'r1_client': self.task_executor.r1_client,
        'event_publisher': self.event_publisher,
        'previous_phase_results': {},  # Will accumulate as we go
        **job.options
    }

    # Execute phases sequentially for this item
    for phase_def in self._resolve_dependencies(phases):
        try:
            executor = phase_executors.get(phase_def.id)

            # Call the single-item executor
            result = await executor(item_context)

            # Store result for next phase's context
            item_result.phase_results[phase_def.id] = result
            item_context['previous_phase_results'][phase_def.id] = result

            # Emit progress event
            if self.event_publisher:
                await self.event_publisher.message(
                    job.id,
                    f"[{item_id}] Completed {phase_def.name}",
                    "info"
                )

        except Exception as e:
            item_result.errors.append(f"{phase_def.name}: {str(e)}")

            if phase_def.critical:
                item_result.status = "failed"
                item_result.completed_at = datetime.utcnow()
                return item_result

    item_result.status = "completed"
    item_result.completed_at = datetime.utcnow()
    return item_result
```

### 4. New Method: _aggregate_item_results

```python
def _aggregate_item_results(self, job: WorkflowJob) -> WorkflowJob:
    """Determine final job status based on item results"""
    completed = [r for r in job.item_results.values() if r.status == "completed"]
    failed = [r for r in job.item_results.values() if r.status == "failed"]

    if not failed:
        job.status = JobStatus.COMPLETED
    elif not completed:
        job.status = JobStatus.FAILED
    else:
        job.status = JobStatus.PARTIAL

    job.summary = {
        'total_items': len(job.item_results),
        'completed_items': len(completed),
        'failed_items': len(failed),
        'failed_item_ids': [r.item_id for r in failed]
    }

    return job
```

---

## Workflow Definition Example

### Per-Unit SSID (workflow_definition.py)

```python
def get_workflow_definition(
    configure_lan_ports: bool = False,
    execution_mode: str = "phase_first"  # or "item_parallel"
) -> WorkflowDefinition:

    phases = [
        PhaseDefinition(
            id="create_ssid",
            name="Create SSID",
            scope=PhaseScope.PER_ITEM,
            executor="routers.per_unit_ssid.phases.create_ssid_single.execute"
        ),
        PhaseDefinition(
            id="create_ap_group",
            name="Create AP Group",
            dependencies=["create_ssid"],  # Within same item
            scope=PhaseScope.PER_ITEM,
            executor="routers.per_unit_ssid.phases.create_ap_group_single.execute"
        ),
        PhaseDefinition(
            id="assign_ap",
            name="Assign AP to Group",
            dependencies=["create_ap_group"],
            scope=PhaseScope.PER_ITEM,
            executor="routers.per_unit_ssid.phases.assign_ap_single.execute"
        ),
        PhaseDefinition(
            id="activate_ssid",
            name="Activate SSID on Group",
            dependencies=["assign_ap"],
            scope=PhaseScope.PER_ITEM,
            executor="routers.per_unit_ssid.phases.activate_ssid_single.execute"
        ),
    ]

    return WorkflowDefinition(
        name="per_unit_ssid_configuration",
        description="Configure per-unit SSIDs",
        phases=phases,
        execution_mode=ExecutionMode(execution_mode),
        item_key="units",
        max_concurrent_items=50
    )
```

---

## Phase Executor Pattern

### Option A: Separate Single-Item Executors

```python
# phases/create_ssid_single.py
async def execute(context: Dict[str, Any]) -> Dict[str, Any]:
    """Create SSID for a SINGLE unit"""
    item = context['item']  # Single unit dict
    unit_number = item['unit_number']
    ssid_name = item['ssid_name']

    ssid_result = await r1_client.networks.create_wifi_network(...)

    return {
        'ssid_id': ssid_result['id'],
        'ssid_name': ssid_name,
        'unit_number': unit_number
    }
```

### Option B: Shared Core with Wrapper (Recommended)

```python
# phases/create_ssids.py - works for both modes

async def execute_single(context: Dict[str, Any]) -> Dict[str, Any]:
    """Single-item executor (core logic)"""
    item = context['item']
    r1_client = context['r1_client']
    # ... create one SSID ...
    return {'ssid_id': '...', 'unit_number': item['unit_number']}

async def execute(context: Dict[str, Any]) -> List[Task]:
    """Batch executor (phase-first mode) - wraps single-item logic"""
    units = context.get('units', [])
    results = []

    for unit in units:
        item_context = {**context, 'item': unit}
        result = await execute_single(item_context)
        results.append(result)

    return [Task(
        id="create_ssids",
        name=f"Created {len(results)} SSIDs",
        status=TaskStatus.COMPLETED,
        output_data={'ssid_results': results}
    )]
```

---

## Mixed Phases Example

For workflows needing global setup/teardown:

```python
phases = [
    # Runs ONCE at the start
    PhaseDefinition(
        id="fetch_venue_aps",
        name="Fetch All Venue APs",
        scope=PhaseScope.PER_WORKFLOW,
        executor="..."
    ),

    # Runs PER ITEM (in parallel)
    PhaseDefinition(
        id="create_ssid",
        scope=PhaseScope.PER_ITEM,
        dependencies=["fetch_venue_aps"],  # Waits for global phase
        executor="..."
    ),
    PhaseDefinition(
        id="assign_ap",
        scope=PhaseScope.PER_ITEM,
        dependencies=["create_ssid"],
        executor="..."
    ),

    # Runs ONCE at the end (after all items)
    PhaseDefinition(
        id="generate_report",
        scope=PhaseScope.PER_WORKFLOW,
        dependencies=["assign_ap"],  # Means: after ALL assign_ap complete
        executor="..."
    )
]
```

---

## Progress Reporting

| Mode | Progress Events |
|------|-----------------|
| Phase-First | "Phase 2 of 5: Create AP Groups" |
| Item-Parallel | "[Unit 101] Step 2/5: Create AP Group" + "45/100 units complete" |

```python
# Item-level progress
await self.event_publisher.message(
    job.id,
    f"[{item_id}] {phase_def.name}",
    "info",
    details={'item_id': item_id, 'phase': phase_def.id}
)

# Aggregate progress
completed = len([r for r in job.item_results.values() if r.status == "completed"])
total = len(job.item_results)
await self.event_publisher.progress_update(job.id, {
    'completed_items': completed,
    'total_items': total,
    'percent': round(completed / total * 100, 1)
})
```

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Item fails on critical phase | That item stops, other items continue |
| Item fails on non-critical phase | Item continues to next phase |
| All items fail | Job status = FAILED |
| Some items fail | Job status = PARTIAL, summary shows which failed |

---

## Implementation Summary

| File | Changes |
|------|---------|
| `api/workflow/models.py` | Add `ExecutionMode`, `PhaseScope`, `ItemResult`; extend `WorkflowDefinition`, `PhaseDefinition`, `WorkflowJob` |
| `api/workflow/engine.py` | Add `_execute_item_parallel`, `_execute_item_workflow`, `_aggregate_item_results`; branch in `execute_workflow` |
| `api/routers/*/workflow_definition.py` | Add `execution_mode`, `item_key`, `scope` to phases |
| Phase executors | Either create single-item versions OR refactor to shared core with batch wrapper |

**Estimated effort**: ~200-300 lines new engine code, ~50 lines model additions

---

## When to Implement

Consider implementing when:
- Processing 100+ items per workflow
- Item processing time varies significantly (parallel helps)
- Need better failure isolation (one item failing shouldn't block others)
- Want more granular progress reporting

Current phase-first approach works fine for:
- Smaller item counts
- When all items typically succeed
- When phase-level progress is sufficient
