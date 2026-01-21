# DPSK Orchestrator Implementation Plan

## Overview

The DPSK Orchestrator is an **ongoing service** that automatically syncs passphrases from per-unit DPSK pools to a site-wide DPSK pool, enabling seamless roaming across a property while maintaining per-unit isolation via VLAN tags.

### The Problem
- RuckusONE limit: 1 DPSK pool → max 64 SSIDs
- Customer has 200+ units, each with per-unit SSID + DPSK pool
- Customer wants a site-wide SSID for seamless roaming
- VLAN tags ensure users land on their private network regardless of which SSID they connect to

### The Solution
```
Per-Unit Pools (Source of Truth)          Site-Wide Pool (Aggregated Replica)
┌─────────────────────────────────┐      ┌────────────────────────────────────┐
│ Unit101DPSK → 4 passphrases     │──┐   │ SiteWideDPSK                       │
│ Unit102DPSK → 4 passphrases     │  │   │   - All passphrases from all units │
│ Unit103DPSK → 4 passphrases     │  ├──▶│   - VLAN tags preserved            │
│ ... x 200 units                 │  │   │   - One-way sync (per-unit → site) │
└─────────────────────────────────┘──┘   └────────────────────────────────────┘
```

---

## Architecture

### Components

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DPSK Orchestrator                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────────┐   │
│  │  Webhook Receiver │    │  Scheduler       │    │  Sync Engine         │   │
│  │  (FastAPI POST)   │    │  (APScheduler)   │    │                      │   │
│  │                   │    │                   │    │  - Diff detection    │   │
│  │  - BULK_CREATE    │    │  - Every 30 min  │    │  - Add passphrases   │   │
│  │  - UPDATE         │    │  - Full diff     │    │  - Update passphrases│   │
│  │  - DELETE         │    │  - Catch missed  │    │  - Flag removals     │   │
│  └────────┬──────────┘    └────────┬─────────┘    └──────────┬───────────┘   │
│           │                        │                         │               │
│           └────────────────────────┴─────────────────────────┘               │
│                                    │                                         │
│                          ┌─────────▼─────────┐                               │
│                          │  Redis State      │                               │
│                          │  - Orchestrator   │                               │
│                          │    configs        │                               │
│                          │  - Sync history   │                               │
│                          │  - Passphrase map │                               │
│                          └───────────────────┘                               │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Webhook Trigger** (Primary):
   ```
   RuckusONE Activity → Webhook POST → Orchestrator → Fetch Details → Sync to Site-Wide
   ```

2. **Scheduled Polling** (Backup):
   ```
   Every 30 min → Fetch all per-unit pools → Diff against site-wide → Sync missing
   ```

3. **Removal Handling**:
   ```
   Passphrase deleted from per-unit → Flag in site-wide (don't auto-delete) → Notify admin
   ```

---

## Implementation Steps

### Phase 1: Database & Models

**New SQLAlchemy Models** (`api/models/orchestrator.py`):

```python
class DPSKOrchestrator(Base):
    """Configuration for a DPSK orchestrator instance"""
    __tablename__ = "dpsk_orchestrators"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)              # "Parkview Apartments"
    controller_id = Column(Integer, ForeignKey("controllers.id"))
    tenant_id = Column(String, nullable=True)          # For MSP
    venue_id = Column(String, nullable=True)           # Scope to venue

    # Site-wide pool target
    site_wide_pool_id = Column(String, nullable=False) # Target DPSK pool ID
    site_wide_pool_name = Column(String)               # For display

    # Configuration
    sync_interval_minutes = Column(Integer, default=30)
    enabled = Column(Boolean, default=True)
    auto_delete = Column(Boolean, default=False)       # False = flag only

    # Webhook config
    webhook_secret = Column(String, nullable=True)     # For verification

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)
    last_sync_at = Column(DateTime, nullable=True)

    # Relationships
    source_pools = relationship("OrchestratorSourcePool", back_populates="orchestrator")
    sync_history = relationship("OrchestratorSyncEvent", back_populates="orchestrator")


class OrchestratorSourcePool(Base):
    """Per-unit DPSK pools that feed into the site-wide pool"""
    __tablename__ = "orchestrator_source_pools"

    id = Column(Integer, primary_key=True)
    orchestrator_id = Column(Integer, ForeignKey("dpsk_orchestrators.id"))

    pool_id = Column(String, nullable=False)           # RuckusONE DPSK pool ID
    pool_name = Column(String)                         # "Unit101DPSK"
    identity_group_id = Column(String)                 # Associated identity group

    # Tracking
    last_sync_at = Column(DateTime, nullable=True)
    passphrase_count = Column(Integer, default=0)

    orchestrator = relationship("DPSKOrchestrator", back_populates="source_pools")


class OrchestratorSyncEvent(Base):
    """Audit log of sync operations"""
    __tablename__ = "orchestrator_sync_events"

    id = Column(Integer, primary_key=True)
    orchestrator_id = Column(Integer, ForeignKey("dpsk_orchestrators.id"))

    event_type = Column(String)                        # "webhook", "scheduled", "manual"
    trigger_activity_id = Column(String, nullable=True) # RuckusONE activity ID

    # Results
    status = Column(String)                            # "success", "partial", "failed"
    added_count = Column(Integer, default=0)
    updated_count = Column(Integer, default=0)
    flagged_for_removal = Column(Integer, default=0)
    errors = Column(JSON, default=[])

    # Timestamps
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    orchestrator = relationship("DPSKOrchestrator", back_populates="sync_history")


class PassphraseMapping(Base):
    """Tracks which source passphrases map to which site-wide passphrases"""
    __tablename__ = "passphrase_mappings"

    id = Column(Integer, primary_key=True)
    orchestrator_id = Column(Integer, ForeignKey("dpsk_orchestrators.id"))

    # Source (per-unit pool)
    source_pool_id = Column(String, nullable=False)
    source_passphrase_id = Column(String, nullable=False)
    source_username = Column(String)

    # Target (site-wide pool)
    target_passphrase_id = Column(String, nullable=True)  # Null if not yet synced

    # State
    sync_status = Column(String, default="pending")    # pending, synced, flagged_removal
    vlan_id = Column(Integer, nullable=True)           # Preserved VLAN

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    last_synced_at = Column(DateTime, nullable=True)
    flagged_at = Column(DateTime, nullable=True)       # When marked for removal
```

### Phase 2: Webhook Receiver

**New Router** (`api/routers/orchestrator/webhook_router.py`):

```python
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from typing import Optional
import hmac
import hashlib

router = APIRouter(prefix="/orchestrator/webhook", tags=["orchestrator"])

@router.post("/ruckusone/{orchestrator_id}")
async def receive_webhook(
    orchestrator_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Receive webhook from RuckusONE activity notifications.

    Expected payload:
    {
        "type": "activity",
        "payload": {
            "useCase": "BULK_CREATE_PERSONAS" | "UPDATE_PERSONA" | "DELETE_PERSONA",
            "status": "SUCCESS" | "IN_PROGRESS" | "FAILED",
            "entityId": "pool_or_group_id",
            "requestId": "activity_id"
        }
    }
    """
    body = await request.json()

    # 1. Validate orchestrator exists
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator or not orchestrator.enabled:
        raise HTTPException(404, "Orchestrator not found or disabled")

    # 2. Optional: Verify webhook signature
    if orchestrator.webhook_secret:
        signature = request.headers.get("X-Webhook-Signature")
        if not verify_signature(body, orchestrator.webhook_secret, signature):
            raise HTTPException(401, "Invalid signature")

    # 3. Filter for relevant events
    payload = body.get("payload", {})
    use_case = payload.get("useCase", "")
    status = payload.get("status", "")

    relevant_use_cases = [
        "BULK_CREATE_PERSONAS",
        "CREATE_PERSONA",
        "UPDATE_PERSONA",
        "DELETE_PERSONA"
    ]

    if use_case not in relevant_use_cases:
        return {"status": "ignored", "reason": f"useCase {use_case} not relevant"}

    if status != "SUCCESS":
        return {"status": "ignored", "reason": f"status {status} not actionable"}

    # 4. Enqueue sync task
    background_tasks.add_task(
        process_webhook_event,
        orchestrator_id=orchestrator_id,
        activity_id=payload.get("requestId"),
        entity_id=payload.get("entityId"),
        use_case=use_case
    )

    return {"status": "accepted", "activity_id": payload.get("requestId")}


async def process_webhook_event(
    orchestrator_id: int,
    activity_id: str,
    entity_id: str,
    use_case: str
):
    """Background task to process webhook and sync passphrases"""
    # Implementation in Phase 4
    pass
```

### Phase 3: Scheduler Service

**APScheduler Integration** (`api/scheduler.py`):

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from contextlib import asynccontextmanager

scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan handler for scheduler"""
    # Startup
    scheduler.start()
    await schedule_orchestrators()
    yield
    # Shutdown
    scheduler.shutdown()


async def schedule_orchestrators():
    """Load all enabled orchestrators and schedule their sync jobs"""
    db = SessionLocal()
    try:
        orchestrators = db.query(DPSKOrchestrator).filter_by(enabled=True).all()

        for orch in orchestrators:
            scheduler.add_job(
                run_scheduled_sync,
                trigger=IntervalTrigger(minutes=orch.sync_interval_minutes),
                id=f"orchestrator_{orch.id}_sync",
                replace_existing=True,
                kwargs={"orchestrator_id": orch.id}
            )
            logger.info(f"Scheduled orchestrator {orch.id} every {orch.sync_interval_minutes} min")
    finally:
        db.close()


async def run_scheduled_sync(orchestrator_id: int):
    """Scheduled task: full diff sync for an orchestrator"""
    logger.info(f"Running scheduled sync for orchestrator {orchestrator_id}")

    sync_engine = SyncEngine(orchestrator_id)
    result = await sync_engine.full_sync()

    logger.info(f"Scheduled sync complete: +{result.added}, ~{result.updated}, -{result.flagged}")
```

**Update main.py**:
```python
from api.scheduler import lifespan

app = FastAPI(lifespan=lifespan)
```

### Phase 4: Sync Engine

**Core Sync Logic** (`api/routers/orchestrator/sync_engine.py`):

```python
from dataclasses import dataclass
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)

@dataclass
class SyncResult:
    added: int = 0
    updated: int = 0
    flagged: int = 0
    errors: List[str] = None

    def __post_init__(self):
        self.errors = self.errors or []


class SyncEngine:
    def __init__(self, orchestrator_id: int, db: Session = None):
        self.orchestrator_id = orchestrator_id
        self.db = db or SessionLocal()
        self.orchestrator = None
        self.r1_client = None

    async def initialize(self):
        """Load orchestrator config and initialize API client"""
        self.orchestrator = self.db.query(DPSKOrchestrator).get(self.orchestrator_id)
        if not self.orchestrator:
            raise ValueError(f"Orchestrator {self.orchestrator_id} not found")

        controller = self.db.query(Controller).get(self.orchestrator.controller_id)
        self.r1_client = await get_r1_client(controller)

    async def full_sync(self) -> SyncResult:
        """
        Full diff sync: Compare all source pools against site-wide pool.
        Used by scheduler as backup to webhooks.
        """
        await self.initialize()
        result = SyncResult()

        # 1. Fetch all passphrases from all source pools
        source_passphrases = await self._fetch_all_source_passphrases()
        logger.info(f"Found {len(source_passphrases)} passphrases across source pools")

        # 2. Fetch all passphrases from site-wide pool
        site_wide_passphrases = await self._fetch_site_wide_passphrases()
        logger.info(f"Found {len(site_wide_passphrases)} passphrases in site-wide pool")

        # 3. Build lookup maps
        # Key: (username, vlan_id) - unique identifier for a passphrase
        source_map = {
            (p['userName'], p.get('vlanId')): p
            for p in source_passphrases
        }
        site_wide_map = {
            (p['userName'], p.get('vlanId')): p
            for p in site_wide_passphrases
        }

        # 4. Find passphrases to ADD (in source but not in site-wide)
        to_add = []
        for key, source_pp in source_map.items():
            if key not in site_wide_map:
                to_add.append(source_pp)

        # 5. Find passphrases to UPDATE (in both but different)
        to_update = []
        for key, source_pp in source_map.items():
            if key in site_wide_map:
                site_pp = site_wide_map[key]
                if self._needs_update(source_pp, site_pp):
                    to_update.append((source_pp, site_pp))

        # 6. Find passphrases to FLAG (in site-wide but not in source)
        to_flag = []
        for key, site_pp in site_wide_map.items():
            if key not in source_map:
                # Check if this was a synced passphrase (not manually created)
                mapping = self._get_mapping_by_target(site_pp['id'])
                if mapping:
                    to_flag.append(site_pp)

        # 7. Execute sync operations
        result.added = await self._add_passphrases(to_add)
        result.updated = await self._update_passphrases(to_update)
        result.flagged = await self._flag_passphrases(to_flag)

        # 8. Log sync event
        await self._log_sync_event("scheduled", result)

        return result

    async def sync_pool(self, pool_id: str) -> SyncResult:
        """
        Incremental sync: Sync a single source pool (triggered by webhook).
        More efficient than full sync.
        """
        await self.initialize()
        result = SyncResult()

        # 1. Verify pool is a source pool for this orchestrator
        source_pool = self.db.query(OrchestratorSourcePool).filter_by(
            orchestrator_id=self.orchestrator_id,
            pool_id=pool_id
        ).first()

        if not source_pool:
            logger.warning(f"Pool {pool_id} is not a source pool for orchestrator {self.orchestrator_id}")
            return result

        # 2. Fetch passphrases from this source pool
        source_passphrases = await self._fetch_pool_passphrases(pool_id)

        # 3. Get existing mappings for this pool
        existing_mappings = self.db.query(PassphraseMapping).filter_by(
            orchestrator_id=self.orchestrator_id,
            source_pool_id=pool_id
        ).all()
        mapping_by_source = {m.source_passphrase_id: m for m in existing_mappings}

        # 4. Process each source passphrase
        for pp in source_passphrases:
            pp_id = pp['id']

            if pp_id in mapping_by_source:
                # Already synced - check if needs update
                mapping = mapping_by_source[pp_id]
                if mapping.sync_status == "synced":
                    # Check for changes
                    if await self._sync_passphrase_update(pp, mapping):
                        result.updated += 1
            else:
                # New passphrase - add to site-wide
                if await self._sync_passphrase_add(pp, pool_id):
                    result.added += 1

        # 5. Check for deletions (passphrases in mapping but not in source)
        source_ids = {pp['id'] for pp in source_passphrases}
        for mapping in existing_mappings:
            if mapping.source_passphrase_id not in source_ids:
                if mapping.sync_status == "synced":
                    await self._flag_passphrase_removal(mapping)
                    result.flagged += 1

        # 6. Update source pool tracking
        source_pool.last_sync_at = datetime.utcnow()
        source_pool.passphrase_count = len(source_passphrases)
        self.db.commit()

        # 7. Log sync event
        await self._log_sync_event("webhook", result, pool_id=pool_id)

        return result

    async def _fetch_all_source_passphrases(self) -> List[Dict]:
        """Fetch passphrases from all source pools"""
        all_passphrases = []

        for source_pool in self.orchestrator.source_pools:
            passphrases = await self._fetch_pool_passphrases(source_pool.pool_id)
            # Tag each passphrase with its source pool
            for pp in passphrases:
                pp['_source_pool_id'] = source_pool.pool_id
            all_passphrases.extend(passphrases)

        return all_passphrases

    async def _fetch_pool_passphrases(self, pool_id: str) -> List[Dict]:
        """Fetch all passphrases from a single pool"""
        tenant_id = self.orchestrator.tenant_id

        result = await self.r1_client.dpsk.query_passphrases(
            pool_id=pool_id,
            tenant_id=tenant_id,
            filters={"status": ["ACTIVE"]},
            page=0,
            limit=1000  # Adjust as needed
        )

        return result.get('data', [])

    async def _fetch_site_wide_passphrases(self) -> List[Dict]:
        """Fetch all passphrases from site-wide pool"""
        return await self._fetch_pool_passphrases(self.orchestrator.site_wide_pool_id)

    async def _sync_passphrase_add(self, source_pp: Dict, source_pool_id: str) -> bool:
        """Add a passphrase to the site-wide pool"""
        try:
            # Create passphrase in site-wide pool with same properties
            result = await self.r1_client.dpsk.create_passphrase(
                pool_id=self.orchestrator.site_wide_pool_id,
                tenant_id=self.orchestrator.tenant_id,
                user_name=source_pp['userName'],
                user_email=source_pp.get('userEmail'),
                passphrase=source_pp.get('passphrase'),  # Copy exact passphrase
                vlan_id=source_pp.get('vlanId'),         # CRITICAL: preserve VLAN
                max_devices=source_pp.get('maxDevices', 5),
                expiration_date=source_pp.get('expirationDate')
            )

            # Create mapping record
            mapping = PassphraseMapping(
                orchestrator_id=self.orchestrator_id,
                source_pool_id=source_pool_id,
                source_passphrase_id=source_pp['id'],
                source_username=source_pp['userName'],
                target_passphrase_id=result['id'],
                sync_status="synced",
                vlan_id=source_pp.get('vlanId'),
                last_synced_at=datetime.utcnow()
            )
            self.db.add(mapping)
            self.db.commit()

            logger.info(f"Added passphrase {source_pp['userName']} to site-wide pool")
            return True

        except Exception as e:
            logger.error(f"Failed to add passphrase {source_pp['userName']}: {e}")
            return False

    async def _flag_passphrase_removal(self, mapping: PassphraseMapping):
        """Flag a passphrase for removal (don't auto-delete)"""
        mapping.sync_status = "flagged_removal"
        mapping.flagged_at = datetime.utcnow()
        self.db.commit()

        # TODO: Send notification (email, Slack, etc.)
        logger.warning(
            f"Flagged passphrase {mapping.source_username} for removal "
            f"(source: {mapping.source_passphrase_id}, target: {mapping.target_passphrase_id})"
        )

    # ... additional helper methods
```

### Phase 5: Management API

**Orchestrator CRUD Router** (`api/routers/orchestrator/orchestrator_router.py`):

```python
router = APIRouter(prefix="/api/orchestrators", tags=["orchestrator"])

@router.get("/", response_model=List[OrchestratorResponse])
async def list_orchestrators(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all orchestrators for the current user's controllers"""
    # Filter by user's accessible controllers
    pass

@router.post("/", response_model=OrchestratorResponse)
async def create_orchestrator(
    request: CreateOrchestratorRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create a new DPSK orchestrator.

    Request body:
    {
        "name": "Parkview Apartments",
        "controller_id": 123,
        "tenant_id": "optional-msp-tenant",
        "venue_id": "optional-venue-scope",
        "site_wide_pool_id": "target-pool-id",
        "source_pool_ids": ["pool1", "pool2", ...],
        "sync_interval_minutes": 30
    }
    """
    pass

@router.get("/{orchestrator_id}", response_model=OrchestratorDetailResponse)
async def get_orchestrator(orchestrator_id: int, ...):
    """Get orchestrator details including source pools and sync history"""
    pass

@router.put("/{orchestrator_id}")
async def update_orchestrator(orchestrator_id: int, request: UpdateOrchestratorRequest, ...):
    """Update orchestrator configuration"""
    pass

@router.delete("/{orchestrator_id}")
async def delete_orchestrator(orchestrator_id: int, ...):
    """Delete orchestrator (does not delete actual DPSK pools)"""
    pass

@router.post("/{orchestrator_id}/source-pools")
async def add_source_pool(orchestrator_id: int, request: AddSourcePoolRequest, ...):
    """Add a source pool to an existing orchestrator"""
    pass

@router.delete("/{orchestrator_id}/source-pools/{pool_id}")
async def remove_source_pool(orchestrator_id: int, pool_id: str, ...):
    """Remove a source pool from an orchestrator"""
    pass

@router.post("/{orchestrator_id}/sync")
async def trigger_manual_sync(
    orchestrator_id: int,
    background_tasks: BackgroundTasks,
    ...
):
    """Manually trigger a full sync"""
    pass

@router.get("/{orchestrator_id}/history")
async def get_sync_history(orchestrator_id: int, limit: int = 50, ...):
    """Get sync event history"""
    pass

@router.get("/{orchestrator_id}/flagged")
async def get_flagged_passphrases(orchestrator_id: int, ...):
    """Get passphrases flagged for removal"""
    pass

@router.post("/{orchestrator_id}/flagged/{mapping_id}/resolve")
async def resolve_flagged_passphrase(
    orchestrator_id: int,
    mapping_id: int,
    action: str,  # "delete" or "ignore"
    ...
):
    """Resolve a flagged passphrase - either delete from site-wide or ignore"""
    pass
```

### Phase 6: Frontend UI

**New Page** (`src/pages/DPSKOrchestrator.tsx`):

```
Components needed:
├── OrchestratorList          # List of configured orchestrators
├── CreateOrchestratorModal   # Wizard to set up new orchestrator
│   ├── Step 1: Select controller/venue
│   ├── Step 2: Select site-wide pool (target)
│   ├── Step 3: Select source pools (per-unit)
│   └── Step 4: Configure sync interval
├── OrchestratorDetail        # Detail view of single orchestrator
│   ├── Status card (enabled, last sync, stats)
│   ├── Source pools table
│   ├── Sync history timeline
│   └── Flagged passphrases alerts
├── SyncHistoryTable          # Detailed sync event log
└── FlaggedPassphrasesModal   # Review and resolve flagged items
```

---

## File Structure

```
api/
├── models/
│   └── orchestrator.py              # NEW: SQLAlchemy models
├── routers/
│   └── orchestrator/
│       ├── __init__.py
│       ├── orchestrator_router.py   # NEW: CRUD endpoints
│       ├── webhook_router.py        # NEW: Webhook receiver
│       └── sync_engine.py           # NEW: Core sync logic
├── scheduler.py                     # NEW: APScheduler setup
└── main.py                          # UPDATE: Add lifespan handler

src/
├── pages/
│   └── DPSKOrchestrator.tsx         # NEW: Main orchestrator page
├── components/
│   └── orchestrator/
│       ├── OrchestratorList.tsx
│       ├── CreateOrchestratorModal.tsx
│       ├── OrchestratorDetail.tsx
│       └── FlaggedPassphrasesModal.tsx
└── api/
    └── orchestrator.ts              # NEW: API client functions
```

---

## Key Design Decisions

### 1. One-Way Sync
- Source of truth: Per-unit DPSK pools
- Site-wide pool is a replica, never edited directly
- Changes flow: per-unit → site-wide (never reverse)

### 2. VLAN Preservation
- VLAN ID is copied exactly from source passphrase
- Enables isolation even when roaming

### 3. Removal Handling
- Default: Flag for manual review (don't auto-delete)
- Optional: Enable auto-delete via config
- Prevents accidental loss of access

### 4. Dual Trigger Strategy
- Primary: Webhooks for real-time sync
- Backup: Scheduled polling every 30 min
- Ensures no changes are missed

### 5. Passphrase Mapping Table
- Tracks source → target relationships
- Enables efficient incremental sync
- Supports audit trail

---

## Resolved Questions

1. **Passphrase copying**: ✅ Can specify exact passphrase strings via API

2. **Identity sync**: ✅ Need to sync identities alongside passphrases. RuckusONE may auto-associate due to existing relationships, but we should verify and handle explicitly.

3. **Webhook setup**: ✅ Can create webhooks programmatically via RuckusONE API

4. **Notification method**: ✅ Frontend in-app notifications only for MVP. Email and outbound webhook notifications can be added later.

5. **Pool auto-discovery**: ✅ See new section below

---

## Pool Auto-Discovery Mechanism

### The Challenge
How do we know which DPSK pools belong to a specific property/orchestrator?

### The Solution: Venue-Based Scoping

DPSK pools are activated on a **venue's networks**. This gives us a natural scoping mechanism:

```
Orchestrator Config:
┌─────────────────────────────────────────────────────────────────┐
│  name: "Parkview Apartments"                                    │
│  venue_id: "venue-abc-123"  ← Primary scope                     │
│  site_wide_pool_id: "pool-xyz"  ← Target for sync               │
│                                                                  │
│  Discovery Rules:                                                │
│  ├── Include pools matching: "Unit*DPSK", "*PerUnit*"           │
│  ├── Exclude pools matching: "SiteWide*", "Guest*"              │
│  └── Only pools active on venue's networks                      │
└─────────────────────────────────────────────────────────────────┘
```

### Auto-Discovery Flow (30-minute scheduled task)

```python
async def auto_discover_source_pools(orchestrator_id: int):
    """
    Discover new per-unit pools that should be added as sources.
    Runs as part of the 30-minute scheduled sync.
    """
    orchestrator = get_orchestrator(orchestrator_id)

    # 1. Get all DPSK pools active on this venue's networks
    venue_pools = await r1_client.dpsk.query_dpsk_pools(
        tenant_id=orchestrator.tenant_id,
        venue_id=orchestrator.venue_id  # Key filter!
    )

    # 2. Filter by discovery rules
    discovered = []
    for pool in venue_pools:
        # Skip the site-wide pool itself
        if pool['id'] == orchestrator.site_wide_pool_id:
            continue

        # Apply include/exclude patterns
        if matches_include_pattern(pool['name'], orchestrator.include_patterns):
            if not matches_exclude_pattern(pool['name'], orchestrator.exclude_patterns):
                discovered.append(pool)

    # 3. Find new pools (not already tracked)
    existing_pool_ids = {sp.pool_id for sp in orchestrator.source_pools}
    new_pools = [p for p in discovered if p['id'] not in existing_pool_ids]

    # 4. Auto-add new pools as sources
    for pool in new_pools:
        add_source_pool(orchestrator_id, pool['id'], pool['name'])
        logger.info(f"Auto-discovered new source pool: {pool['name']}")

    return new_pools
```

### Updated Database Model

```python
class DPSKOrchestrator(Base):
    # ... existing fields ...

    # Auto-discovery configuration
    auto_discover_enabled = Column(Boolean, default=True)
    include_patterns = Column(JSON, default=["Unit*", "*PerUnit*"])  # Glob patterns
    exclude_patterns = Column(JSON, default=["SiteWide*", "Guest*", "Visitor*"])

    # Track discovery
    last_discovery_at = Column(DateTime, nullable=True)
    discovered_pools_count = Column(Integer, default=0)
```

### Discovery Events

```python
class OrchestratorDiscoveryEvent(Base):
    """Log of auto-discovery runs"""
    __tablename__ = "orchestrator_discovery_events"

    id = Column(Integer, primary_key=True)
    orchestrator_id = Column(Integer, ForeignKey("dpsk_orchestrators.id"))

    # Results
    pools_scanned = Column(Integer, default=0)
    pools_discovered = Column(Integer, default=0)
    pools_added = Column(JSON, default=[])  # List of {id, name}

    # Timestamps
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
```

### 30-Minute Scheduled Task (Updated)

```python
async def run_scheduled_sync(orchestrator_id: int):
    """Scheduled task: discovery + full diff sync"""
    logger.info(f"Running scheduled sync for orchestrator {orchestrator_id}")

    sync_engine = SyncEngine(orchestrator_id)

    # Step 1: Auto-discover new source pools
    if sync_engine.orchestrator.auto_discover_enabled:
        new_pools = await sync_engine.auto_discover_source_pools()
        if new_pools:
            logger.info(f"Discovered {len(new_pools)} new source pools")

    # Step 2: Full sync across all source pools
    result = await sync_engine.full_sync()

    logger.info(f"Scheduled sync complete: +{result.added}, ~{result.updated}, -{result.flagged}")
```

---

## Identity Sync Strategy

### The Association Chain

```
Identity Group → contains → Identities
      ↓
DPSK Pool → linked to Identity Group
      ↓
Passphrases → each linked to an Identity
```

### Sync Approach

When syncing a passphrase from per-unit to site-wide:

1. **Check if identity exists in site-wide's identity group**
   - Match by username + email

2. **If identity doesn't exist → create it**
   ```python
   # Create identity in site-wide identity group
   identity = await r1_client.identity.create_identity(
       group_id=site_wide_identity_group_id,
       username=source_passphrase['userName'],
       email=source_passphrase.get('userEmail'),
       description=f"Synced from {source_pool_name}"
   )
   ```

3. **Create passphrase linked to identity**
   ```python
   passphrase = await r1_client.dpsk.create_passphrase(
       pool_id=site_wide_pool_id,
       identity_id=identity['id'],  # Link to identity
       passphrase=source_passphrase['passphrase'],
       vlan_id=source_passphrase['vlanId'],
       # ...
   )
   ```

### Updated Mapping Table

```python
class PassphraseMapping(Base):
    # ... existing fields ...

    # Identity tracking
    source_identity_id = Column(String, nullable=True)
    target_identity_id = Column(String, nullable=True)
```

---

## Webhook Auto-Configuration

### On Orchestrator Creation

```python
async def create_orchestrator(...):
    # 1. Create orchestrator record
    orchestrator = DPSKOrchestrator(...)
    db.add(orchestrator)
    db.commit()

    # 2. Auto-create webhook in RuckusONE
    webhook_url = f"{settings.BASE_URL}/api/orchestrator/webhook/ruckusone/{orchestrator.id}"

    webhook = await r1_client.webhooks.create_webhook(
        name=f"dpsk-orchestrator-{orchestrator.id}",
        url=webhook_url,
        events=["activity"],  # Or specific DPSK events
        tenant_id=orchestrator.tenant_id
    )

    # 3. Store webhook ID for later management
    orchestrator.webhook_id = webhook['id']
    orchestrator.webhook_secret = webhook.get('secret')
    db.commit()

    return orchestrator
```

### On Orchestrator Deletion

```python
async def delete_orchestrator(orchestrator_id: int):
    orchestrator = get_orchestrator(orchestrator_id)

    # 1. Delete webhook from RuckusONE
    if orchestrator.webhook_id:
        await r1_client.webhooks.delete_webhook(orchestrator.webhook_id)

    # 2. Delete local records
    db.delete(orchestrator)
    db.commit()
```

---

## Error Handling & Rate Limiting

### API Unavailability
- **Strategy**: Simple retry on next scheduled sync
- If RuckusONE is temporarily unavailable during webhook processing → log error, wait for 30-min sync
- No complex retry logic needed for MVP

### Rate Limiting
- RuckusONE handles rate limiting via async calls
- **Self-imposed cap**: 120 requests/second max
- Implementation:
```python
from asyncio import Semaphore

class SyncEngine:
    def __init__(self, ...):
        self.rate_limiter = Semaphore(120)  # Max concurrent requests

    async def _rate_limited_call(self, coro):
        async with self.rate_limiter:
            return await coro
```

---

## Conflict Resolution

### The Scenario
A passphrase exists in the site-wide pool but:
- It wasn't synced by the orchestrator (no mapping record)
- It doesn't exist in any per-unit source pool

This could happen if someone manually created a passphrase directly in the site-wide pool.

### Detection
During full sync, when building the diff:
```python
# In full_sync()
for key, site_pp in site_wide_map.items():
    if key not in source_map:
        mapping = self._get_mapping_by_target(site_pp['id'])
        if mapping:
            # We synced this, but source was deleted → flag for removal
            to_flag_removal.append(site_pp)
        else:
            # We didn't sync this → orphan, flag for resolution
            to_flag_orphan.append(site_pp)
```

### Orphan Passphrase Handling

```python
class PassphraseMapping(Base):
    # ... existing fields ...

    # Sync status options:
    # - "pending": Not yet synced
    # - "synced": Successfully synced
    # - "flagged_removal": Source deleted, awaiting manual resolution
    # - "orphan": Exists in site-wide but not in any source pool
    sync_status = Column(String, default="pending")

    # For orphans: suggested target pool (if we can guess)
    suggested_source_pool_id = Column(String, nullable=True)
```

### Resolution Actions (Frontend)

For **flagged_removal** (source was deleted):
```
┌─────────────────────────────────────────────────────────────────┐
│  ⚠️ Flagged for Removal                                         │
│                                                                  │
│  Username: john.doe@unit101                                      │
│  VLAN: 101                                                       │
│  Source pool: Unit101DPSK (passphrase deleted)                   │
│                                                                  │
│  [Delete from Site-Wide]  [Keep in Site-Wide]  [Dismiss]        │
└─────────────────────────────────────────────────────────────────┘
```

For **orphan** (manually created in site-wide):
```
┌─────────────────────────────────────────────────────────────────┐
│  ⚠️ Orphan Passphrase (not synced from any source)              │
│                                                                  │
│  Username: jane.doe                                              │
│  VLAN: 102                                                       │
│  Suggestion: Looks like it belongs to Unit102DPSK (VLAN match)  │
│                                                                  │
│  [Copy to Unit102DPSK]  [Copy to Different Pool ▼]  [Ignore]    │
└─────────────────────────────────────────────────────────────────┘
```

### Copy-to-Source Action

```python
@router.post("/{orchestrator_id}/orphans/{passphrase_id}/copy-to-source")
async def copy_orphan_to_source(
    orchestrator_id: int,
    passphrase_id: str,
    request: CopyToSourceRequest,  # { "target_pool_id": "pool-123" }
    ...
):
    """
    Copy an orphan passphrase from site-wide to a per-unit source pool.
    This establishes it as a proper source → site-wide sync relationship.
    """
    # 1. Get the orphan passphrase from site-wide
    site_pp = await r1_client.dpsk.get_passphrase(
        pool_id=orchestrator.site_wide_pool_id,
        passphrase_id=passphrase_id
    )

    # 2. Create passphrase in the target source pool
    source_pp = await r1_client.dpsk.create_passphrase(
        pool_id=request.target_pool_id,
        user_name=site_pp['userName'],
        passphrase=site_pp['passphrase'],
        vlan_id=site_pp['vlanId'],
        # ...
    )

    # 3. Create mapping record (now it's a proper sync relationship)
    mapping = PassphraseMapping(
        orchestrator_id=orchestrator_id,
        source_pool_id=request.target_pool_id,
        source_passphrase_id=source_pp['id'],
        target_passphrase_id=passphrase_id,
        sync_status="synced",
        vlan_id=site_pp['vlanId']
    )
    db.add(mapping)
    db.commit()

    return {"status": "success", "source_passphrase_id": source_pp['id']}
```

### VLAN-Based Pool Suggestion

```python
def suggest_source_pool(orphan_pp: Dict, source_pools: List) -> Optional[str]:
    """
    Guess which source pool an orphan passphrase might belong to
    based on VLAN ID matching.
    """
    vlan_id = orphan_pp.get('vlanId')
    if not vlan_id:
        return None

    # Look for a source pool where other passphrases have the same VLAN
    for pool in source_pools:
        pool_vlans = get_vlans_in_pool(pool.pool_id)
        if vlan_id in pool_vlans:
            return pool.pool_id

    return None
```

---

## Generic Scheduler Service

The scheduler is a **standalone, reusable service** - not specific to DPSK Orchestration. The orchestrator is simply the first consumer.

### Design Principles
1. **Decoupled**: Scheduler knows nothing about DPSK, orchestrators, or any specific feature
2. **Registration-based**: Features register jobs with the scheduler at startup
3. **Persistent**: Jobs survive app restarts (stored in database)
4. **Observable**: Status, history, and health monitoring
5. **Configurable**: Support interval, cron, and one-time triggers

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Generic Scheduler Service                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────────┐   │
│  │  Job Registry    │    │  APScheduler     │    │  Job Store           │   │
│  │                  │    │  (AsyncIO)       │    │  (SQLAlchemy)        │   │
│  │  - Register job  │───▶│                  │◀───│                      │   │
│  │  - Unregister    │    │  - Interval      │    │  - Persist jobs      │   │
│  │  - Update        │    │  - Cron          │    │  - Survive restart   │   │
│  └──────────────────┘    │  - One-time      │    └──────────────────────┘   │
│                          └────────┬─────────┘                               │
│                                   │                                         │
│                          ┌────────▼─────────┐                               │
│                          │  Job Executor    │                               │
│                          │  - Run callable  │                               │
│                          │  - Log results   │                               │
│                          │  - Handle errors │                               │
│                          └──────────────────┘                               │
└─────────────────────────────────────────────────────────────────────────────┘

Consumers register jobs:
┌─────────────────────┐
│ DPSK Orchestrator   │──── scheduler.register("dpsk_sync_123", interval=30min, ...)
├─────────────────────┤
│ Future Feature A    │──── scheduler.register("feature_a_job", cron="0 * * * *", ...)
├─────────────────────┤
│ Future Feature B    │──── scheduler.register("cleanup_job", interval=24h, ...)
└─────────────────────┘
```

### Database Models

```python
# api/models/scheduler.py

class ScheduledJob(Base):
    """Persistent scheduled job configuration"""
    __tablename__ = "scheduled_jobs"

    id = Column(String, primary_key=True)              # Unique job ID
    name = Column(String, nullable=False)              # Human-readable name
    description = Column(String, nullable=True)

    # Job target (what to call)
    callable_path = Column(String, nullable=False)     # "api.routers.orchestrator.sync_engine:run_sync"
    callable_kwargs = Column(JSON, default={})         # Arguments to pass

    # Trigger configuration
    trigger_type = Column(String, nullable=False)      # "interval", "cron", "date"
    trigger_config = Column(JSON, nullable=False)      # {"minutes": 30} or {"hour": 0, "minute": 0}

    # State
    enabled = Column(Boolean, default=True)
    paused = Column(Boolean, default=False)

    # Ownership (optional - for multi-tenant isolation)
    owner_type = Column(String, nullable=True)         # "orchestrator", "feature_a", etc.
    owner_id = Column(String, nullable=True)           # ID of the owning entity

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)


class ScheduledJobRun(Base):
    """Execution history for scheduled jobs"""
    __tablename__ = "scheduled_job_runs"

    id = Column(Integer, primary_key=True)
    job_id = Column(String, ForeignKey("scheduled_jobs.id"))

    # Execution details
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)

    # Result
    status = Column(String)                            # "success", "failed", "timeout"
    result = Column(JSON, nullable=True)               # Return value (if any)
    error = Column(Text, nullable=True)                # Error message/traceback

    job = relationship("ScheduledJob", backref="runs")
```

### Scheduler Service

```python
# api/scheduler/service.py

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
import importlib
import logging

logger = logging.getLogger(__name__)

class SchedulerService:
    """
    Generic scheduler service for running periodic tasks.
    Features register jobs; scheduler executes them.
    """

    def __init__(self, database_url: str):
        self.scheduler = AsyncIOScheduler(
            jobstores={
                'default': SQLAlchemyJobStore(url=database_url)
            },
            job_defaults={
                'coalesce': True,           # Combine missed runs into one
                'max_instances': 1,          # Don't overlap same job
                'misfire_grace_time': 300    # 5 min grace for missed jobs
            }
        )
        self._db = None

    async def start(self, db_session_factory):
        """Start the scheduler and load persisted jobs"""
        self._db = db_session_factory
        self.scheduler.start()
        await self._load_persisted_jobs()
        logger.info("Scheduler service started")

    async def shutdown(self):
        """Gracefully shutdown the scheduler"""
        self.scheduler.shutdown(wait=True)
        logger.info("Scheduler service stopped")

    async def _load_persisted_jobs(self):
        """Load enabled jobs from database on startup"""
        db = self._db()
        try:
            jobs = db.query(ScheduledJob).filter_by(enabled=True, paused=False).all()
            for job in jobs:
                self._add_job_to_scheduler(job)
            logger.info(f"Loaded {len(jobs)} scheduled jobs")
        finally:
            db.close()

    def _add_job_to_scheduler(self, job: ScheduledJob):
        """Add a job to APScheduler from our model"""
        trigger = self._create_trigger(job.trigger_type, job.trigger_config)
        callable_fn = self._import_callable(job.callable_path)

        self.scheduler.add_job(
            self._execute_job,
            trigger=trigger,
            id=job.id,
            replace_existing=True,
            kwargs={
                'job_id': job.id,
                'callable_fn': callable_fn,
                'callable_kwargs': job.callable_kwargs
            }
        )

    async def _execute_job(self, job_id: str, callable_fn, callable_kwargs: dict):
        """Execute a job and record the result"""
        db = self._db()
        run = ScheduledJobRun(job_id=job_id, started_at=datetime.utcnow())
        db.add(run)
        db.commit()

        try:
            # Execute the callable
            if asyncio.iscoroutinefunction(callable_fn):
                result = await callable_fn(**callable_kwargs)
            else:
                result = callable_fn(**callable_kwargs)

            # Record success
            run.status = "success"
            run.result = result if isinstance(result, dict) else {"result": str(result)}
            run.completed_at = datetime.utcnow()
            run.duration_seconds = (run.completed_at - run.started_at).total_seconds()

            # Update job's last_run_at
            job = db.query(ScheduledJob).get(job_id)
            if job:
                job.last_run_at = run.completed_at

            db.commit()
            logger.info(f"Job {job_id} completed successfully")

        except Exception as e:
            run.status = "failed"
            run.error = str(e)
            run.completed_at = datetime.utcnow()
            run.duration_seconds = (run.completed_at - run.started_at).total_seconds()
            db.commit()
            logger.error(f"Job {job_id} failed: {e}")

        finally:
            db.close()

    def _create_trigger(self, trigger_type: str, config: dict):
        """Create APScheduler trigger from config"""
        if trigger_type == "interval":
            return IntervalTrigger(**config)  # e.g., {"minutes": 30}
        elif trigger_type == "cron":
            return CronTrigger(**config)      # e.g., {"hour": 0, "minute": 0}
        elif trigger_type == "date":
            return DateTrigger(**config)      # e.g., {"run_date": "2024-01-01 00:00:00"}
        else:
            raise ValueError(f"Unknown trigger type: {trigger_type}")

    def _import_callable(self, path: str):
        """Import a callable from a dotted path"""
        module_path, fn_name = path.rsplit(":", 1)
        module = importlib.import_module(module_path)
        return getattr(module, fn_name)

    # === Public API for consumers ===

    async def register_job(
        self,
        job_id: str,
        name: str,
        callable_path: str,
        trigger_type: str,
        trigger_config: dict,
        callable_kwargs: dict = None,
        owner_type: str = None,
        owner_id: str = None,
        description: str = None
    ) -> ScheduledJob:
        """
        Register a new scheduled job.

        Args:
            job_id: Unique identifier (e.g., "orchestrator_123_sync")
            name: Human-readable name
            callable_path: Import path to function (e.g., "api.module:function")
            trigger_type: "interval", "cron", or "date"
            trigger_config: Trigger-specific config (e.g., {"minutes": 30})
            callable_kwargs: Arguments to pass to the callable
            owner_type: Type of owning feature (for filtering)
            owner_id: ID of owning entity (for filtering)

        Returns:
            The created ScheduledJob
        """
        db = self._db()
        try:
            job = ScheduledJob(
                id=job_id,
                name=name,
                description=description,
                callable_path=callable_path,
                callable_kwargs=callable_kwargs or {},
                trigger_type=trigger_type,
                trigger_config=trigger_config,
                owner_type=owner_type,
                owner_id=owner_id,
                enabled=True
            )
            db.add(job)
            db.commit()

            self._add_job_to_scheduler(job)
            logger.info(f"Registered job: {job_id}")

            return job
        finally:
            db.close()

    async def unregister_job(self, job_id: str):
        """Remove a scheduled job"""
        db = self._db()
        try:
            job = db.query(ScheduledJob).get(job_id)
            if job:
                self.scheduler.remove_job(job_id)
                db.delete(job)
                db.commit()
                logger.info(f"Unregistered job: {job_id}")
        finally:
            db.close()

    async def pause_job(self, job_id: str):
        """Pause a job without removing it"""
        self.scheduler.pause_job(job_id)
        db = self._db()
        try:
            job = db.query(ScheduledJob).get(job_id)
            if job:
                job.paused = True
                db.commit()
        finally:
            db.close()

    async def resume_job(self, job_id: str):
        """Resume a paused job"""
        self.scheduler.resume_job(job_id)
        db = self._db()
        try:
            job = db.query(ScheduledJob).get(job_id)
            if job:
                job.paused = False
                db.commit()
        finally:
            db.close()

    async def trigger_job_now(self, job_id: str):
        """Manually trigger a job to run immediately"""
        job = self.scheduler.get_job(job_id)
        if job:
            job.modify(next_run_time=datetime.utcnow())

    async def get_job_status(self, job_id: str) -> dict:
        """Get status of a job"""
        db = self._db()
        try:
            job = db.query(ScheduledJob).get(job_id)
            if not job:
                return None

            apscheduler_job = self.scheduler.get_job(job_id)

            return {
                "id": job.id,
                "name": job.name,
                "enabled": job.enabled,
                "paused": job.paused,
                "last_run_at": job.last_run_at,
                "next_run_at": apscheduler_job.next_run_time if apscheduler_job else None,
                "trigger_type": job.trigger_type,
                "trigger_config": job.trigger_config
            }
        finally:
            db.close()

    async def list_jobs(self, owner_type: str = None, owner_id: str = None) -> list:
        """List all jobs, optionally filtered by owner"""
        db = self._db()
        try:
            query = db.query(ScheduledJob)
            if owner_type:
                query = query.filter_by(owner_type=owner_type)
            if owner_id:
                query = query.filter_by(owner_id=owner_id)
            return query.all()
        finally:
            db.close()

    async def get_job_history(self, job_id: str, limit: int = 50) -> list:
        """Get execution history for a job"""
        db = self._db()
        try:
            return db.query(ScheduledJobRun)\
                .filter_by(job_id=job_id)\
                .order_by(ScheduledJobRun.started_at.desc())\
                .limit(limit)\
                .all()
        finally:
            db.close()


# Global instance
scheduler_service: SchedulerService = None

async def get_scheduler() -> SchedulerService:
    """Get the global scheduler service"""
    global scheduler_service
    if scheduler_service is None:
        raise RuntimeError("Scheduler not initialized")
    return scheduler_service
```

### Integration with FastAPI

```python
# api/main.py

from api.scheduler.service import SchedulerService, scheduler_service
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifespan: start/stop scheduler"""
    global scheduler_service

    # Startup
    scheduler_service = SchedulerService(settings.DATABASE_URL)
    await scheduler_service.start(SessionLocal)

    yield

    # Shutdown
    await scheduler_service.shutdown()

app = FastAPI(lifespan=lifespan)
```

### DPSK Orchestrator Integration

Now the orchestrator uses the generic scheduler:

```python
# api/routers/orchestrator/orchestrator_router.py

from api.scheduler.service import get_scheduler

async def create_orchestrator(...):
    # 1. Create orchestrator record
    orchestrator = DPSKOrchestrator(...)
    db.add(orchestrator)
    db.commit()

    # 2. Register sync job with scheduler
    scheduler = await get_scheduler()
    await scheduler.register_job(
        job_id=f"orchestrator_{orchestrator.id}_sync",
        name=f"DPSK Sync: {orchestrator.name}",
        callable_path="api.routers.orchestrator.sync_engine:run_scheduled_sync",
        trigger_type="interval",
        trigger_config={"minutes": orchestrator.sync_interval_minutes},
        callable_kwargs={"orchestrator_id": orchestrator.id},
        owner_type="orchestrator",
        owner_id=str(orchestrator.id),
        description=f"Periodic sync for {orchestrator.name}"
    )

    # 3. Create webhook in RuckusONE...

    return orchestrator


async def delete_orchestrator(orchestrator_id: int):
    # 1. Unregister job from scheduler
    scheduler = await get_scheduler()
    await scheduler.unregister_job(f"orchestrator_{orchestrator_id}_sync")

    # 2. Delete webhook from RuckusONE...
    # 3. Delete orchestrator record...
```

### Scheduler Admin API (Optional)

```python
# api/routers/scheduler_router.py

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])

@router.get("/jobs")
async def list_scheduled_jobs(
    owner_type: str = None,
    current_user: User = Depends(get_current_admin_user)
):
    """List all scheduled jobs (admin only)"""
    scheduler = await get_scheduler()
    return await scheduler.list_jobs(owner_type=owner_type)

@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str, ...):
    """Get job status and next run time"""
    pass

@router.post("/jobs/{job_id}/trigger")
async def trigger_job(job_id: str, ...):
    """Manually trigger a job to run now"""
    pass

@router.post("/jobs/{job_id}/pause")
async def pause_job(job_id: str, ...):
    """Pause a scheduled job"""
    pass

@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: str, ...):
    """Resume a paused job"""
    pass

@router.get("/jobs/{job_id}/history")
async def get_job_history(job_id: str, limit: int = 50, ...):
    """Get execution history for a job"""
    pass
```

---

## Dependencies

**New Python packages**:
```
apscheduler>=3.10.0    # For scheduled sync jobs
```

**Database migration**:
```bash
alembic revision --autogenerate -m "Add scheduler and DPSK orchestrator tables"
alembic upgrade head
```
