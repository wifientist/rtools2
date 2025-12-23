# Workflow Engine Implementation Plan
**Version:** 1.0
**Created:** 2025-12-19
**Purpose:** General-purpose workflow engine for bulk async operations in RuckusONE API

---

## Table of Contents
1. [Overview](#overview)
2. [Architecture](#architecture)
3. [File Structure](#file-structure)
4. [Data Models](#data-models)
5. [Redis Key Structure](#redis-key-structure)
6. [Implementation Phases](#implementation-phases)
7. [API Endpoints](#api-endpoints)
8. [Integration Points](#integration-points)
9. [Error Handling](#error-handling)
10. [Testing Strategy](#testing-strategy)
11. [Usage Examples](#usage-examples)

---

## Overview

### Problem Statement
- RuckusONE API operations often return `202 Accepted` with async task IDs
- Bulk operations (e.g., creating 2000 DPSKs) require tracking hundreds of async tasks
- Need dependency management (Step N depends on Step N-1 outputs)
- Must be resumable on failure with idempotent operations
- Should support partial success with cleanup options

### Solution Architecture
**Workflow Engine** = Reusable orchestration layer for any bulk operation
- **State Storage:** Redis (temporary, 7-day TTL)
- **Execution:** FastAPI background tasks + asyncio (NO Celery needed)
- **Parallelization:** Throttled async task pools
- **Idempotency:** Check existing resources before creating
- **Resume-ability:** Can retry/resume failed jobs

### Key Design Principles
1. **Generic & Reusable:** Not tied to Cloudpath - any bulk operation can use it
2. **Async-First:** Leverages asyncio for parallel R1 API polling
3. **Transparent:** Real-time progress tracking via Redis
4. **Safe:** Graceful partial failure with cleanup options
5. **Simple:** No external job queue - just FastAPI + Redis

---

## Architecture

### High-Level Flow
```
User Request → FastAPI Endpoint → Create Job → Start Background Task
                                      ↓
                                  Redis State
                                      ↓
                            Workflow Engine (asyncio)
                                      ↓
                        ┌─────────────┴─────────────┐
                    Phase 1              Phase 2              Phase N
                   (parallel)           (depends on 1)       (depends on N-1)
                        ↓                    ↓                    ↓
                   Update Redis         Update Redis         Update Redis
                        ↓                    ↓                    ↓
                 User polls /status endpoint to track progress
```

### Components

#### 1. Core Workflow Engine (`api/workflow/`)
- **engine.py** - Main orchestrator
- **models.py** - Pydantic models for Job, Phase, Task
- **state_manager.py** - Redis CRUD for workflow state
- **executor.py** - Task execution with retries
- **async_pool.py** - Bulk async task polling with throttling
- **idempotent.py** - Find-or-create helpers

#### 2. Enhanced R1Client (`api/r1api/client.py`)
- **`await_task_completion_bulk()`** - Poll multiple async tasks in parallel
- Throttled with semaphore (configurable max concurrent polls)
- Returns dict mapping request_id → result

#### 3. Domain-Specific Workflows (`api/routers/cloudpath/`)
- **workflow_definition.py** - Cloudpath-specific phase definitions
- **phases/*.py** - Phase executor functions
- **cloudpath_router.py** - FastAPI endpoints

---

## File Structure

```
api/
├── workflow/                           # ✨ NEW: Core workflow engine (reusable)
│   ├── __init__.py
│   ├── engine.py                       # WorkflowEngine class
│   ├── models.py                       # Job, Phase, Task models
│   ├── state_manager.py                # Redis state management
│   ├── executor.py                     # Task execution logic
│   ├── async_pool.py                   # Bulk async polling
│   └── idempotent.py                   # Idempotency helpers
│
├── r1api/
│   ├── client.py                       # ✏️ MODIFY: Add await_task_completion_bulk()
│   └── services/
│       ├── dpsk.py                     # ✅ DONE
│       ├── identity.py                 # ✅ DONE
│       └── policy_sets.py              # ✅ DONE
│
├── routers/
│   └── cloudpath/                      # ✨ NEW: Cloudpath DPSK workflow
│       ├── __init__.py
│       ├── cloudpath_router.py         # FastAPI endpoints
│       ├── workflow_definition.py      # CLOUDPATH_WORKFLOW definition
│       ├── phases/
│       │   ├── __init__.py
│       │   ├── parse.py                # Phase 1: Parse/validate JSON
│       │   ├── identity_groups.py      # Phase 2: Create identity groups
│       │   ├── dpsk_pools.py           # Phase 3: Create DPSK pools
│       │   ├── policy_sets.py          # Phase 4: Create policy sets
│       │   ├── attach_policies.py      # Phase 5: Attach policies to pools
│       │   ├── passphrases.py          # Phase 6: Create passphrases
│       │   ├── activate.py             # Phase 7: Activate on networks
│       │   └── audit.py                # Phase 8: Audit results
│       └── utils/
│           ├── mapping.py              # Cloudpath→R1 data mapping
│           └── cleanup.py              # Cleanup failed resources
│
├── redis_client.py                     # ✅ DONE: Redis connection
├── config/
│   └── workflow_config.py              # ✨ NEW: Workflow settings
│
└── main.py                             # ✏️ MODIFY: Add cloudpath router
```

---

## Data Models

### WorkflowJob
```python
class WorkflowJob(BaseModel):
    id: str                             # UUID
    workflow_name: str                  # e.g., "cloudpath_dpsk_migration"
    status: JobStatus                   # PENDING, RUNNING, COMPLETED, FAILED, PARTIAL
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime]

    # Configuration
    controller_id: int
    venue_id: str
    tenant_id: str
    options: Dict[str, Any]             # Workflow-specific options

    # Input data
    input_data: Dict[str, Any]          # Original request payload

    # Execution tracking
    current_phase_id: Optional[str]
    phases: List[Phase]

    # Results
    created_resources: Dict[str, List[Dict]]  # {"identity_groups": [...], "dpsk_pools": [...]}
    summary: Dict[str, Any]             # Final stats
    errors: List[str]
```

### Phase
```python
class Phase(BaseModel):
    id: str                             # e.g., "create_dpsk_pools"
    name: str                           # Display name
    status: PhaseStatus                 # PENDING, RUNNING, COMPLETED, FAILED, SKIPPED
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

    dependencies: List[str]             # Phase IDs that must complete first
    parallelizable: bool                # Can tasks run in parallel?
    critical: bool                      # Stop entire job if this fails?
    skip_condition: Optional[str]       # e.g., "options.include_policy_sets == False"

    tasks: List[Task]
    result: Dict[str, Any]              # Output data for dependent phases
    errors: List[str]
```

### Task
```python
class Task(BaseModel):
    id: str                             # UUID
    name: str                           # Display name
    status: TaskStatus                  # PENDING, IN_PROGRESS, POLLING, COMPLETED, FAILED

    # R1 async tracking
    request_id: Optional[str]           # R1 async task ID (for 202 responses)
    poll_count: int = 0
    max_polls: int = 60

    # Data
    input_data: Dict[str, Any]
    output_data: Dict[str, Any]         # Result (e.g., {"identity_group_id": "abc123"})

    # Error handling
    error_message: Optional[str]
    retry_count: int = 0
    max_retries: int = 3

    # Timing
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
```

### Enums
```python
class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"                 # Some tasks succeeded, some failed

class PhaseStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"

class TaskStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    POLLING = "POLLING"                 # Waiting for async task
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
```

---

## Redis Key Structure

### Naming Convention
All keys prefixed with `workflow:` for easy cleanup

### Keys by Type

#### Job Metadata
```
workflow:jobs:{job_id}                  → JSON: WorkflowJob (main job state)
workflow:jobs:{job_id}:lock             → Lock for concurrent access prevention
workflow:jobs:index                     → Sorted Set: All job IDs by created_at
```

#### Phase Tracking
```
workflow:jobs:{job_id}:phases           → List: Phase IDs in order
workflow:jobs:{job_id}:phase:{phase_id} → JSON: Phase state
```

#### Task Tracking
```
workflow:jobs:{job_id}:task:{task_id}   → JSON: Task state
workflow:jobs:{job_id}:tasks:pending    → Set: Task IDs pending execution
workflow:jobs:{job_id}:tasks:completed  → Set: Task IDs completed
workflow:jobs:{job_id}:tasks:failed     → Set: Task IDs failed
```

#### Created Resources (for idempotency)
```
workflow:jobs:{job_id}:created:identity_groups  → JSON Array: Created identity groups
workflow:jobs:{job_id}:created:dpsk_pools       → JSON Array: Created DPSK pools
workflow:jobs:{job_id}:created:passphrases      → JSON Array: Created passphrases
```

#### TTL Settings
```python
JOB_TTL = 604800  # 7 days (604800 seconds)
LOCK_TTL = 300    # 5 minutes (for distributed locks)
```

---

## Implementation Phases

### Phase 1: R1Client Bulk Async Polling ⭐ START HERE
**Goal:** Enable parallel polling of multiple async tasks
**File:** `api/r1api/client.py`

**Tasks:**
1. Add `await_task_completion_bulk()` method to R1Client
2. Implement semaphore-based throttling (max 100 concurrent polls)
3. Add progress callback support
4. Handle mixed success/failure results
5. Add global timeout support

**Acceptance Criteria:**
- Can poll 100+ request IDs in parallel
- Returns dict: `{request_id: result}`
- Respects timeout_seconds parameter
- Throttles to prevent overwhelming API

**Estimated Effort:** 2 hours

---

### Phase 2: Core Workflow Engine
**Goal:** Build reusable workflow orchestration layer
**Files:** `api/workflow/*.py`

**Tasks:**
1. Create Pydantic models (`models.py`)
   - WorkflowJob, Phase, Task
   - JobStatus, PhaseStatus, TaskStatus enums
2. Implement RedisStateManager (`state_manager.py`)
   - save_job(), get_job(), update_job()
   - save/get phase and task states
   - Track created resources
   - Implement Redis locks for concurrent access
3. Build TaskExecutor (`executor.py`)
   - Execute single task with retry logic
   - Handle 202 async responses
   - Track request_ids for polling
4. Create AsyncTaskPool (`async_pool.py`)
   - Bulk poll with semaphore throttling
   - Progress callback integration
5. Build WorkflowEngine (`engine.py`)
   - Phase-by-phase execution
   - Dependency resolution
   - Parallel vs sequential execution
   - Error handling (critical vs non-critical phases)
6. Implement IdempotentHelper (`idempotent.py`)
   - find_or_create_identity_group()
   - find_or_create_dpsk_pool()
   - Generic find_or_create() pattern

**Acceptance Criteria:**
- Can execute multi-phase workflows
- Handles dependencies correctly
- Stores/retrieves state from Redis
- Supports parallel task execution
- Implements retry logic

**Estimated Effort:** 8 hours

---

### Phase 3: Cloudpath DPSK Workflow
**Goal:** Implement Cloudpath-specific workflow using engine
**Files:** `api/routers/cloudpath/*.py`

**Tasks:**
1. Define workflow (`workflow_definition.py`)
   - 8 phases with dependencies
   - Parallelization flags
   - Critical phase markers
2. Implement phase executors (`phases/*.py`)
   - Phase 1: Parse & validate Cloudpath JSON
   - Phase 2: Create identity groups (parallel)
   - Phase 3: Create DPSK pools (parallel, depends on 2)
   - Phase 4: Create policy sets (parallel, optional)
   - Phase 5: Attach policies (parallel, depends on 3+4)
   - Phase 6: Create passphrases (parallel, depends on 3)
   - Phase 7: Activate on networks (parallel, optional)
   - Phase 8: Audit results
3. Implement data mapping (`utils/mapping.py`)
   - Cloudpath JSON → R1 API structures
4. Implement cleanup (`utils/cleanup.py`)
   - Delete resources in reverse order
5. Create FastAPI router (`cloudpath_router.py`)
   - POST /cloudpath-dpsk/import
   - GET /cloudpath-dpsk/jobs/{job_id}/status
   - POST /cloudpath-dpsk/jobs/{job_id}/cleanup
   - POST /cloudpath-dpsk/jobs/{job_id}/audit

**Acceptance Criteria:**
- Can parse Cloudpath JSON
- Creates all required R1 resources
- Handles 2000+ passphrases efficiently
- Provides real-time progress
- Offers cleanup on failure

**Estimated Effort:** 12 hours

---

### Phase 4: Configuration & Integration
**Goal:** Wire everything together
**Files:** `api/config/workflow_config.py`, `api/main.py`

**Tasks:**
1. Create workflow config (`workflow_config.py`)
   - Redis settings
   - Async polling settings
   - Parallelization limits
   - Retry settings
   - TTL settings
2. Update main.py
   - Import cloudpath router
   - Add to app.include_router()
   - Add Redis startup/shutdown events

**Acceptance Criteria:**
- All settings configurable via env vars
- Cloudpath router accessible via API
- Redis connects on startup

**Estimated Effort:** 2 hours

---

### Phase 5: Frontend Integration
**Goal:** Update UI to use workflow engine
**Files:** `src/pages/CloudpathDPSK.tsx`

**Tasks:**
1. Update import endpoint call
   - Store job_id from response
   - Start polling for status
2. Implement status polling
   - Poll every 2 seconds
   - Show progress bar
   - Show current phase
   - Display errors
3. Show results modal
   - Success: Show created resource counts
   - Partial: Show what succeeded + cleanup option
   - Failed: Show errors + cleanup option
4. Implement cleanup modal
   - Option to delete partial resources
   - Option to acknowledge and keep

**Acceptance Criteria:**
- Real-time progress updates
- Shows current phase and task counts
- Handles partial success gracefully
- Cleanup option on failure

**Estimated Effort:** 4 hours

---

### Phase 6: Testing & Documentation
**Goal:** Ensure reliability

**Tasks:**
1. Unit tests
   - Test each phase executor
   - Test idempotency
   - Test error handling
2. Integration tests
   - Test full Cloudpath workflow
   - Test cleanup
   - Test resume on failure
3. Documentation
   - API endpoint docs
   - Workflow definition guide
   - How to add new workflows

**Estimated Effort:** 6 hours

---

## API Endpoints

### POST `/api/cloudpath-dpsk/import`
**Purpose:** Start Cloudpath DPSK migration workflow
**Auth:** Required (user/admin)

**Request:**
```json
{
  "controller_id": 1,
  "venue_id": "venue-123",
  "dpsk_data": {
    // Cloudpath JSON export
  },
  "options": {
    "just_copy_dpsks": true,
    "include_adaptive_policy_sets": false
  }
}
```

**Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "RUNNING",
  "estimated_duration_seconds": 300
}
```

---

### GET `/api/cloudpath-dpsk/jobs/{job_id}/status`
**Purpose:** Get workflow job status
**Auth:** Required

**Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "RUNNING",
  "progress": {
    "total_tasks": 2050,
    "completed": 1234,
    "failed": 5,
    "percent": 60.4
  },
  "current_phase": {
    "id": "create_passphrases",
    "name": "Create DPSK Passphrases",
    "status": "RUNNING",
    "tasks_completed": 1200,
    "tasks_total": 2000
  },
  "phases": [
    {
      "id": "parse_validate",
      "name": "Parse and Validate",
      "status": "COMPLETED",
      "duration_seconds": 2
    },
    {
      "id": "create_identity_groups",
      "name": "Create Identity Groups",
      "status": "COMPLETED",
      "duration_seconds": 15
    },
    // ...
  ],
  "created_resources": {
    "identity_groups": [
      {"id": "ig-123", "name": "Guests"}
    ],
    "dpsk_pools": [
      {"id": "pool-456", "name": "Guest DPSKs"}
    ],
    "passphrases": [
      {"id": "pp-789", "userName": "user1"}
    ]
  },
  "errors": [
    "Failed to create passphrase for user99: Rate limit exceeded"
  ]
}
```

---

### POST `/api/cloudpath-dpsk/jobs/{job_id}/cleanup`
**Purpose:** Clean up failed job resources
**Auth:** Required

**Request:**
```json
{
  "delete_partial_resources": true  // false = just acknowledge
}
```

**Response:**
```json
{
  "status": "cleaned",
  "deleted": {
    "identity_groups": 1,
    "dpsk_pools": 3,
    "passphrases": 1200
  }
}
```

---

### POST `/api/cloudpath-dpsk/jobs/{job_id}/audit`
**Purpose:** Audit created resources in venue
**Auth:** Required

**Response:**
```json
{
  "venue_id": "venue-123",
  "venue_name": "Main Campus",
  "total_identity_groups": 5,
  "total_dpsk_pools": 10,
  "total_passphrases": 2000,
  // ... detailed audit data
}
```

---

## Integration Points

### With Existing Code

#### 1. R1Client (`api/r1api/client.py`)
**Modifications:**
- Add `await_task_completion_bulk()` method
- No changes to existing methods
- Backward compatible

#### 2. DPSK Service (`api/r1api/services/dpsk.py`)
**Usage:**
- Workflow calls `r1_client.dpsk.create_dpsk_pool()`
- No modifications needed
- Already returns proper async responses

#### 3. Identity Service (`api/r1api/services/identity.py`)
**Usage:**
- Workflow calls `r1_client.identity.create_identity_group()`
- No modifications needed

#### 4. Policy Sets Service (`api/r1api/services/policy_sets.py`)
**Usage:**
- Workflow calls `r1_client.policy_sets.create_policy_set()`
- No modifications needed

#### 5. Main App (`api/main.py`)
**Modifications:**
- Add cloudpath router: `app.include_router(cloudpath_router)`
- Add Redis lifecycle events

---

## Error Handling

### Error Types & Strategies

#### 1. Validation Errors (Phase 1)
**Type:** Input data validation
**Handling:**
- Fail fast before creating any resources
- Return 400 Bad Request
- No cleanup needed

#### 2. Network/API Errors
**Type:** Transient failures
**Handling:**
- Retry up to 3 times with exponential backoff
- If still failing, mark task as FAILED
- Continue with other tasks (non-critical phases)

#### 3. Rate Limit Errors
**Type:** 429 Too Many Requests
**Handling:**
- Respect Retry-After header
- Automatic backoff and retry
- Reduce parallelism if persistent

#### 4. Resource Already Exists
**Type:** Idempotency conflict
**Handling:**
- Check if resource matches expected config
- If yes: Use existing, mark as success
- If no: Fail with clear error message

#### 5. Async Task Timeout
**Type:** R1 task never completes
**Handling:**
- After max_attempts, mark as FAILED
- Log request_id for manual investigation
- Continue with other tasks

#### 6. Critical Phase Failure
**Type:** Phase marked as critical=True fails
**Handling:**
- Stop entire workflow
- Mark job as FAILED
- Offer cleanup option

### Rollback Strategy

**No Automatic Rollback** - Instead:
1. Track all created resources in Redis
2. On failure, present options to user:
   - Keep resources (for manual cleanup)
   - Delete all created resources
3. Cleanup executes in reverse dependency order:
   - Delete passphrases first
   - Then DPSK pools
   - Then identity groups
   - Then policy sets

---

## Testing Strategy

### Unit Tests

#### Workflow Engine
```python
# tests/workflow/test_engine.py
def test_phase_dependency_resolution()
def test_parallel_task_execution()
def test_critical_phase_failure_stops_job()
def test_non_critical_phase_failure_continues()
```

#### State Manager
```python
# tests/workflow/test_state_manager.py
def test_save_and_retrieve_job()
def test_concurrent_access_with_locks()
def test_ttl_expiration()
```

#### Task Executor
```python
# tests/workflow/test_executor.py
def test_retry_on_transient_failure()
def test_max_retries_exceeded()
def test_async_task_polling()
```

### Integration Tests

#### Cloudpath Workflow
```python
# tests/routers/cloudpath/test_workflow.py
async def test_full_cloudpath_migration()
async def test_partial_failure_cleanup()
async def test_idempotent_retry()
```

### Manual Testing Checklist
- [ ] Import 10 DPSKs successfully
- [ ] Import 2000 DPSKs successfully
- [ ] Handle network error gracefully
- [ ] Resume failed job successfully
- [ ] Cleanup partial resources
- [ ] Audit shows correct counts
- [ ] Progress updates in real-time

---

## Usage Examples

### Example 1: Simple DPSK Migration
```python
from routers.cloudpath.cloudpath_router import start_migration

# Start workflow
job_id = await start_migration(
    controller_id=1,
    venue_id="venue-123",
    dpsk_data=cloudpath_json,
    options={"just_copy_dpsks": True}
)

# Poll status
while True:
    status = await get_job_status(job_id)
    print(f"Progress: {status['progress']['percent']}%")

    if status['status'] in ['COMPLETED', 'FAILED', 'PARTIAL']:
        break

    await asyncio.sleep(2)

# Handle result
if status['status'] == 'COMPLETED':
    print(f"Success! Created {len(status['created_resources']['passphrases'])} passphrases")
elif status['status'] == 'PARTIAL':
    print(f"Partial success. Cleanup available.")
    # Optionally cleanup
    await cleanup_job(job_id, delete_partial_resources=True)
```

### Example 2: Create New Workflow Type
```python
# Define new workflow
BULK_AP_PROVISIONING = WorkflowDefinition(
    name="bulk_ap_provisioning",
    phases=[
        PhaseDefinition(
            id="parse_csv",
            name="Parse AP CSV",
            dependencies=[],
            executor="bulk_ap.phases.parse.execute"
        ),
        PhaseDefinition(
            id="add_aps",
            name="Add APs to Venue",
            dependencies=["parse_csv"],
            parallelizable=True,
            executor="bulk_ap.phases.add_aps.execute"
        ),
        PhaseDefinition(
            id="assign_to_groups",
            name="Assign to AP Groups",
            dependencies=["add_aps"],
            parallelizable=True,
            executor="bulk_ap.phases.assign_groups.execute"
        )
    ]
)

# Use workflow engine
job = create_workflow_job(BULK_AP_PROVISIONING, input_data)
await workflow_engine.execute(job)
```

---

## Configuration Reference

### Environment Variables

```bash
# Redis
REDIS_HOST=redis              # Redis hostname (in Docker network)
REDIS_PORT=6379               # Redis port (internal Docker port)
REDIS_DB=1                    # Redis database number (DB 1 = workflow state)
REDIS_PASSWORD=               # Optional: Set for password protection (recommended for shared networks)

# Note: From host machine, Docker Redis is accessible on port 6381
#       From backend container, it's accessible on redis:6379

# Workflow Engine
ASYNC_POLL_INTERVAL=3         # Seconds between polls
ASYNC_MAX_ATTEMPTS=60         # Max polls per task (3 minutes)
ASYNC_GLOBAL_TIMEOUT=3600     # Max seconds for entire job (1 hour)
MAX_PARALLEL_API_CALLS=50     # Max concurrent API calls
MAX_PARALLEL_POLLS=100        # Max concurrent async polls
TASK_MAX_RETRIES=3            # Retry attempts per task
TASK_RETRY_BACKOFF=2          # Exponential backoff base
JOB_TTL_DAYS=7                # Days to keep job state
```

### Workflow-Specific Settings
```python
# In workflow_definition.py
CLOUDPATH_CONFIG = {
    "max_passphrases_per_batch": 100,
    "dpsk_pool_batch_size": 50,
    "identity_group_batch_size": 20,
}
```

---

## Success Criteria

### Phase 1 Complete When:
- [x] R1Client can poll 100+ async tasks in parallel
- [x] Throttling prevents API overload
- [x] Returns structured results dict
- [x] Handles timeout gracefully

### Phase 2 Complete When:
- [x] WorkflowEngine executes multi-phase workflows
- [x] Redis stores and retrieves job state
- [x] Dependencies are resolved correctly
- [x] Parallel execution works
- [x] Retry logic handles transient failures

### Phase 3 Complete When:
- [x] Can import Cloudpath JSON
- [x] Creates 2000+ DPSKs successfully
- [x] Idempotent on retry
- [x] Provides real-time progress
- [x] Cleanup works on failure

### Phase 4 Complete When:
- [x] All settings configurable
- [x] Cloudpath endpoints accessible
- [x] Redis lifecycle managed

### Phase 5 Complete When:
- [x] Frontend shows live progress
- [x] Displays created resources
- [x] Offers cleanup on failure
- [x] Handles all status states

### Phase 6 Complete When:
- [x] All core functions unit tested
- [x] Full workflow integration tested
- [x] Documentation complete

---

## Next Steps

1. **Review & Approve** this plan
2. **Start Phase 1**: Implement `await_task_completion_bulk()` in R1Client
3. **Iterate** through phases 2-6
4. **Test** thoroughly with real Cloudpath data
5. **Deploy** to production
6. **Expand** to other bulk operations (AP provisioning, etc.)

---

## Appendix: Key Decisions

### Why Redis Instead of Database?
- **Speed:** In-memory operations for real-time updates
- **Temporary:** Job state doesn't need permanent storage
- **Simplicity:** No schema migrations
- **TTL:** Automatic cleanup after 7 days

### Why No Celery?
- **Overkill:** Jobs run in minutes, not hours
- **Complexity:** Adds worker processes, monitoring, etc.
- **FastAPI Built-in:** Background tasks sufficient
- **Async-Native:** asyncio handles parallelization

### Why Phase-Based Architecture?
- **Dependencies:** Clear phase ordering
- **Resumability:** Can retry from failed phase
- **Progress:** Clear milestones for user
- **Debugging:** Easy to isolate failures

### Why Idempotency?
- **Reliability:** Safe to retry on network failures
- **Resume:** Can pick up where we left off
- **Testing:** Can run multiple times safely

---

**End of Implementation Plan**
*Keep this document updated as implementation progresses*
