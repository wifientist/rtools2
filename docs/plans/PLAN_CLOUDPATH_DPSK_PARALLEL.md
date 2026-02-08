# Implementation Plan: Cloudpath DPSK Parallel Execution

## Overview

Add parallel execution capability to the existing Cloudpath DPSK migration workflow, similar to what's already implemented for Per-Unit SSID. This will significantly speed up large migrations (1000s-10000s of passphrases).

---

## Current State

The Cloudpath DPSK workflow currently runs **sequentially**:
- All identity groups created one-by-one
- All DPSK pools created one-by-one
- All passphrases created one-by-one (the bottleneck)
- All policy attachments done one-by-one

**Problem**: A migration with 10,000 passphrases takes 5-10 minutes sequentially.

---

## Goal

Enable parallel execution using the same `ParallelJobOrchestrator` pattern from Per-Unit SSID:
- Split work into child jobs
- Run multiple child jobs concurrently
- Throttle to avoid API rate limits
- **Target**: 5-8x speedup (10,000 passphrases in 1-2 minutes)

---

## Work Unit Strategy

**Recommended: Per DPSK Pool**

Each child job processes:
- 1 Identity Group
- 1 DPSK Pool (linked to that identity group)
- N Passphrases (all passphrases for that pool)
- Policy attachments (if applicable)

This is the natural grouping from the Cloudpath export data.

---

## Implementation Steps

### Step 1: Add Parallel Execution Parameters

**File**: `api/routers/cloudpath/cloudpath_router.py`

```python
class CloudpathDPSKRequest(BaseModel):
    # ... existing fields ...
    parallel_execution: bool = Field(default=False, description="Enable parallel execution")
    max_concurrent: int = Field(default=5, ge=1, le=20, description="Max concurrent child jobs")
```

### Step 2: Create Parallel Workflow Function

**File**: `api/routers/cloudpath/cloudpath_router.py`

Add `run_parallel_cloudpath_workflow()` function that:
1. Groups input data by DPSK pool
2. Creates child jobs (1 per pool)
3. Uses `ParallelJobOrchestrator` to run them concurrently
4. Aggregates results

### Step 3: Refactor Phases for Single-Pool Mode

Each phase needs to work with a single pool's data (child job mode) OR all pools (sequential mode).

**Files to modify**:
- `api/routers/cloudpath/phases/identity_groups.py`
- `api/routers/cloudpath/phases/dpsk_pools.py`
- `api/routers/cloudpath/phases/passphrases.py`
- `api/routers/cloudpath/phases/policy_sets.py` (if used)
- `api/routers/cloudpath/phases/attach_policies.py` (if used)
- `api/routers/cloudpath/phases/activate.py` (if used)

### Step 4: Add Passphrase Throttling

Add semaphore to prevent overwhelming the R1 API during bulk passphrase creation:

```python
passphrase_semaphore = asyncio.Semaphore(max_concurrent)
```

### Step 5: Update Frontend

**File**: `src/pages/CloudpathDPSK.tsx`

Add toggle for parallel execution mode and max_concurrent setting.

---

## Detailed Changes

### Router Changes

```python
# api/routers/cloudpath/cloudpath_router.py

@router.post("/dpsk/migrate")
async def migrate_dpsk(request: CloudpathDPSKRequest, ...):
    if request.parallel_execution:
        return await run_parallel_cloudpath_workflow(request, ...)
    else:
        return await run_cloudpath_workflow(request, ...)  # existing


async def run_parallel_cloudpath_workflow(
    request: CloudpathDPSKRequest,
    r1_client: R1Client,
    user_id: int,
    background_tasks: BackgroundTasks
):
    """
    Run Cloudpath DPSK migration in parallel mode.

    Work unit: 1 DPSK pool + its identity group + its passphrases
    """
    # Group data by pool
    pools_data = group_by_pool(request.dpsk_pools, request.passphrases)

    # Create parent job
    parent_job = WorkflowJob(
        id=str(uuid.uuid4()),
        workflow_name="cloudpath_dpsk_migration_parallel",
        status=JobStatus.PENDING,
        # ... etc
    )

    # Setup orchestrator
    orchestrator = ParallelJobOrchestrator(
        state_manager=state_manager,
        event_publisher=event_publisher
    )

    # Create semaphore for throttling
    passphrase_semaphore = asyncio.Semaphore(request.max_concurrent)

    # Run in background
    background_tasks.add_task(
        orchestrator.run_parallel_workflow,
        parent_job=parent_job,
        items=pools_data,
        item_key="pool_name",
        workflow_definition=get_workflow_definition(),
        r1_client=r1_client,
        max_concurrent=request.max_concurrent,
        extra_context={'passphrase_semaphore': passphrase_semaphore}
    )

    return {"job_id": parent_job.id, "status": "started", "mode": "parallel"}
```

### Phase Refactoring Pattern

Each phase should handle both modes:

```python
# api/routers/cloudpath/phases/passphrases.py

async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Create passphrases - works in both sequential and parallel mode.

    Sequential mode: context contains ALL passphrases for ALL pools
    Parallel mode: context contains passphrases for ONE pool only
    """
    # Get passphrases from context (works for both modes)
    passphrases = context.get('input_data', {}).get('passphrases', [])

    # Get pool map from previous phase
    pool_map = get_previous_phase_data(context, 'create_dpsk_pools', 'pool_map', {})

    # Optional semaphore for throttling (only present in parallel mode)
    semaphore = context.get('passphrase_semaphore', asyncio.Semaphore(999))

    created = []
    async with semaphore:
        for pp in passphrases:
            pool_id = pool_map.get(pp['pool_name'], {}).get('pool_id')
            if not pool_id:
                continue

            result = await r1_client.dpsk.create_passphrase(
                pool_id=pool_id,
                tenant_id=tenant_id,
                passphrase=pp.get('passphrase'),
                # ... etc
            )
            created.append(result)

    return [Task(..., output_data={'created_passphrases': created})]
```

### Data Grouping Function

```python
def group_by_pool(dpsk_pools: List[dict], passphrases: List[dict]) -> List[dict]:
    """
    Group migration data by DPSK pool for parallel processing.

    Returns list of work units, each containing:
    - identity_group: dict
    - dpsk_pool: dict
    - passphrases: List[dict]
    """
    pool_map = {p['name']: p for p in dpsk_pools}

    # Group passphrases by pool
    pp_by_pool = defaultdict(list)
    for pp in passphrases:
        pp_by_pool[pp['dpsk_pool_name']].append(pp)

    # Create work units
    work_units = []
    for pool_name, pool_data in pool_map.items():
        work_units.append({
            'pool_name': pool_name,
            'identity_group': {
                'name': pool_data.get('identity_group_name'),
                'description': pool_data.get('identity_group_description')
            },
            'dpsk_pool': pool_data,
            'passphrases': pp_by_pool.get(pool_name, [])
        })

    return work_units
```

---

## Expected Performance

| Dataset | Sequential | Parallel (5 concurrent) | Speedup |
|---------|------------|-------------------------|---------|
| 10 pools, 100 pp | ~3-6 sec | ~1-2 sec | 3x |
| 50 pools, 1,000 pp | ~30-60 sec | ~6-12 sec | 5x |
| 100 pools, 10,000 pp | ~5-10 min | ~1-2 min | 5-8x |

---

## Implementation Checklist

### Backend
- [ ] Add `parallel_execution` and `max_concurrent` to CloudpathDPSKRequest
- [ ] Create `group_by_pool()` utility function
- [ ] Create `run_parallel_cloudpath_workflow()` function
- [ ] Refactor `identity_groups.py` for single-pool mode
- [ ] Refactor `dpsk_pools.py` for single-pool mode
- [ ] Refactor `passphrases.py` for single-pool mode + semaphore
- [ ] Refactor `policy_sets.py` for single-pool mode (if applicable)
- [ ] Refactor `attach_policies.py` for single-pool mode (if applicable)
- [ ] Refactor `activate.py` for single-pool mode (if applicable)
- [ ] Test sequential mode still works
- [ ] Test parallel mode with small dataset
- [ ] Test parallel mode with large dataset

### Frontend
- [ ] Add parallel execution toggle to CloudpathDPSK.tsx
- [ ] Add max_concurrent slider/input
- [ ] Update progress display for parallel mode
- [ ] Show child job status in UI

---

## Risk Assessment

**Low Risk**:
- Pattern is proven (Per-Unit SSID uses it successfully)
- Existing phases only need minor refactoring
- Sequential mode remains default (opt-in parallel)

**Considerations**:
- R1 API rate limits (mitigated with semaphore)
- Error handling in child jobs (handled by ParallelJobOrchestrator)
- Progress tracking (already built into orchestrator)

---

## Future: Shared Phases (Optional)

After this is working, we can optionally extract phases to a shared location (`api/workflow/phases/`) for reuse across workflows. This would benefit:
- Cloudpath DPSK
- Per-Unit SSID DPSK mode (future)
- Any future workflows needing identity groups, DPSK pools, passphrases

This is a separate, optional refactor and NOT required for parallel execution.
