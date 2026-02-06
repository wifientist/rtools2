"""
V2 Phase Executor Base Class

Provides the foundation for all V2 phase executors with:
- Typed input/output contracts
- Validation (dry-run) support
- Activity registration for bulk tracking
- Resource tracking
- Stateless execution (all state comes via inputs)

Usage:
    @register_phase("create_dpsk_pool")
    class CreateDPSKPoolPhase(PhaseExecutor):
        class Inputs(BaseModel):
            unit_id: str
            pool_name: str
            identity_group_id: str

        class Outputs(BaseModel):
            unit_id: str
            dpsk_pool_id: str
            reused: bool = False

        async def execute(self, inputs: Inputs) -> Outputs:
            ...

        async def validate(self, inputs: Inputs) -> PhaseValidation:
            ...
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Type, ClassVar, Callable, TypeVar, Tuple
from pydantic import BaseModel

T = TypeVar('T')
R = TypeVar('R')

from workflow.v2.models import (
    PhaseContract,
    PhaseInput,
    PhaseOutput,
    ActivityResult,
    ResourceAction,
)

logger = logging.getLogger(__name__)


class PhaseValidation(BaseModel):
    """Result of validating a phase for a specific unit."""
    valid: bool = True
    will_create: bool = False
    will_reuse: bool = False
    existing_resource_id: Optional[str] = None
    estimated_api_calls: int = 0
    actions: List[ResourceAction] = []
    notes: List[str] = []
    warnings: List[str] = []
    errors: List[str] = []


class PhaseContext:
    """
    Execution context provided to phase executors.

    Contains shared resources (R1 client, trackers, etc.)
    but NOT unit-specific data (that comes via inputs).
    """

    def __init__(
        self,
        job_id: str,
        r1_client: Any,
        venue_id: str,
        tenant_id: str,
        activity_tracker: Any = None,
        state_manager: Any = None,
        event_publisher: Any = None,
        options: Dict[str, Any] = None,
        unit_id: str = None,
    ):
        self.job_id = job_id
        self.r1_client = r1_client
        self.venue_id = venue_id
        self.tenant_id = tenant_id
        self.activity_tracker = activity_tracker
        self.state_manager = state_manager
        self.event_publisher = event_publisher
        self.options = options or {}
        self.unit_id = unit_id  # Set when executing for a specific unit


class PhaseExecutor(ABC):
    """
    Base class for all V2 phase executors.

    Phases are:
    - Stateless: all state comes from inputs and context
    - Typed: explicit input/output models via inner classes
    - Atomic: operates on a single unit at a time (for per_unit=True phases)
    - Self-validating: can check inputs and report what will happen

    Subclasses MUST define:
    - Inner class `Inputs(BaseModel)` - what the phase needs
    - Inner class `Outputs(BaseModel)` - what the phase produces
    - `async def execute(self, inputs: Inputs) -> Outputs`
    - `async def validate(self, inputs: Inputs) -> PhaseValidation`

    Subclasses MAY override:
    - `phase_id` (class attribute) - set by @register_phase decorator
    - `phase_name` (class attribute) - human-readable name
    """

    # Set by @register_phase decorator or explicitly
    phase_id: ClassVar[str] = ""
    phase_name: ClassVar[str] = ""

    # Inner classes - override in subclass
    class Inputs(BaseModel):
        """Override with phase-specific input fields."""
        pass

    class Outputs(BaseModel):
        """Override with phase-specific output fields."""
        pass

    def __init__(self, context: PhaseContext):
        """
        Initialize with execution context.

        Args:
            context: Shared resources (R1 client, venue, tenant, etc.)
                    Does NOT contain unit-specific data.
        """
        self.context = context
        self.r1_client = context.r1_client
        self.venue_id = context.venue_id
        self.tenant_id = context.tenant_id
        self.job_id = context.job_id

    @abstractmethod
    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """
        Execute the phase for a single unit (or globally).

        Args:
            inputs: Typed inputs from previous phases or validation

        Returns:
            Typed outputs for downstream phases
        """
        pass

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """
        Validate inputs and check existing resources (dry-run).

        Default implementation just confirms inputs are valid.
        Override for resource existence checks.

        Args:
            inputs: Typed inputs

        Returns:
            PhaseValidation with what will be created/reused
        """
        return PhaseValidation(
            valid=True,
            will_create=True,
            estimated_api_calls=1
        )

    # =========================================================================
    # Helper Methods
    # =========================================================================

    async def register_activity(self, activity_id: str) -> None:
        """Register an R1 activity for centralized bulk tracking."""
        if self.context.activity_tracker:
            await self.context.activity_tracker.register(
                activity_id=activity_id,
                job_id=self.job_id,
                unit_id=self.context.unit_id,
                phase_id=self.phase_id
            )

    async def wait_for_activity(self, activity_id: str) -> ActivityResult:
        """Wait for a registered R1 activity to complete."""
        if not self.context.activity_tracker:
            raise RuntimeError("No activity tracker available")
        return await self.context.activity_tracker.wait(activity_id)

    async def fire_and_wait(self, activity_id: str) -> ActivityResult:
        """Register an activity and immediately wait for it."""
        await self.register_activity(activity_id)
        return await self.wait_for_activity(activity_id)

    async def track_resource(self, resource_type: str, resource_data: Dict[str, Any]) -> None:
        """Track a created resource for cleanup reference."""
        if self.context.state_manager:
            await self.context.state_manager.track_created_resource(
                self.job_id, resource_type, resource_data
            )

    async def emit(self, message: str, level: str = "info", details: Dict = None) -> None:
        """Emit a status message for frontend display."""
        if self.context.event_publisher:
            await self.context.event_publisher.message(
                self.job_id, message, level, details
            )
        log_method = getattr(logger, level if level in ('info', 'warning', 'error') else 'info')
        log_method(f"  [{self.phase_id}] {message}")

    @classmethod
    def get_contract(cls) -> PhaseContract:
        """
        Build PhaseContract from the Inputs/Outputs inner classes.
        Used by the Brain to validate workflow wiring.
        """
        inputs = []
        for field_name, field_info in cls.Inputs.model_fields.items():
            inputs.append(PhaseInput(
                name=field_name,
                type=str(field_info.annotation) if field_info.annotation else "Any",
                required=field_info.is_required(),
                description=field_info.description or "",
            ))

        outputs = []
        for field_name, field_info in cls.Outputs.model_fields.items():
            outputs.append(PhaseOutput(
                name=field_name,
                type=str(field_info.annotation) if field_info.annotation else "Any",
                description=field_info.description or "",
            ))

        return PhaseContract(inputs=inputs, outputs=outputs)

    # =========================================================================
    # Intra-Phase Parallelism Helpers
    # =========================================================================

    async def parallel_map(
        self,
        items: List[T],
        fn: Callable[[T], R],
        max_concurrent: int = 10,
        item_name: str = "item",
        emit_progress: bool = True,
        progress_interval: int = 10,
    ) -> 'ParallelMapResult[R]':
        """
        Execute an async function across many items in parallel with semaphore control.

        This enables intra-phase parallelism for phases that need to process
        many items (e.g., creating 500 passphrases in a single pool).

        Args:
            items: List of items to process
            fn: Async function to call for each item. Should return result or raise.
            max_concurrent: Maximum concurrent operations (default 10)
            item_name: Name for progress messages (e.g., "passphrase", "identity")
            emit_progress: Whether to emit progress updates (default True)
            progress_interval: Emit progress every N items (default 10)

        Returns:
            ParallelMapResult with successes, failures, and summary

        Example:
            async def create_passphrase(pp):
                result = await self.r1_client.dpsk.create_passphrase(...)
                return {'id': result['id'], 'username': pp['username']}

            results = await self.parallel_map(
                passphrases,
                create_passphrase,
                max_concurrent=10,
                item_name="passphrase"
            )
            # results.succeeded = [{'id': ..., 'username': ...}, ...]
            # results.failed = [{'item': pp, 'error': 'reason'}, ...]
        """
        if not items:
            return ParallelMapResult(
                total=0, succeeded=[], failed=[], item_name=item_name
            )

        total = len(items)
        semaphore = asyncio.Semaphore(max_concurrent)
        completed = 0
        succeeded: List[R] = []
        failed: List[Dict[str, Any]] = []
        lock = asyncio.Lock()

        async def process_one(item: T, index: int) -> None:
            nonlocal completed
            async with semaphore:
                try:
                    result = await fn(item)
                    async with lock:
                        succeeded.append(result)
                        completed += 1
                        if emit_progress and completed % progress_interval == 0:
                            await self.emit(
                                f"Progress: {completed}/{total} {item_name}s "
                                f"({len(failed)} failed)"
                            )
                except Exception as e:
                    error_msg = str(e)
                    # Treat "not found" as success for idempotency
                    if 'not found' in error_msg.lower():
                        async with lock:
                            succeeded.append(None)  # type: ignore
                            completed += 1
                    else:
                        async with lock:
                            failed.append({
                                'item': item,
                                'index': index,
                                'error': error_msg
                            })
                            completed += 1
                            logger.warning(
                                f"[{self.phase_id}] Failed {item_name} {index}: {error_msg}"
                            )

        # Launch all tasks
        tasks = [process_one(item, i) for i, item in enumerate(items)]
        await asyncio.gather(*tasks)

        # Final progress emit
        if emit_progress:
            level = "success" if not failed else "warning"
            await self.emit(
                f"Completed: {len(succeeded)} {item_name}s succeeded, "
                f"{len(failed)} failed",
                level
            )

        return ParallelMapResult(
            total=total,
            succeeded=succeeded,
            failed=failed,
            item_name=item_name
        )

    async def parallel_batch(
        self,
        items: List[T],
        fn: Callable[[List[T]], R],
        batch_size: int = 10,
        max_concurrent: int = 5,
        item_name: str = "item",
    ) -> 'ParallelMapResult[R]':
        """
        Process items in batches, with batches running in parallel.

        Useful when the API supports batch operations (e.g., bulk identity creation).

        Args:
            items: List of items to process
            fn: Async function that processes a batch. Takes list, returns result.
            batch_size: Number of items per batch (default 10)
            max_concurrent: Maximum concurrent batches (default 5)
            item_name: Name for progress messages

        Returns:
            ParallelMapResult with batch results
        """
        if not items:
            return ParallelMapResult(
                total=0, succeeded=[], failed=[], item_name=item_name
            )

        # Split into batches
        batches = [
            items[i:i + batch_size]
            for i in range(0, len(items), batch_size)
        ]

        async def process_batch(batch: List[T]) -> R:
            return await fn(batch)

        return await self.parallel_map(
            batches,
            process_batch,
            max_concurrent=max_concurrent,
            item_name=f"{item_name} batch",
            progress_interval=1,  # Emit for each batch
        )


class ParallelMapResult(BaseModel):
    """Result of a parallel_map operation."""
    total: int
    succeeded: List[Any]
    failed: List[Dict[str, Any]]
    item_name: str = "item"

    @property
    def success_count(self) -> int:
        return len(self.succeeded)

    @property
    def failure_count(self) -> int:
        return len(self.failed)

    @property
    def all_succeeded(self) -> bool:
        return len(self.failed) == 0

    def summary(self) -> str:
        return f"{self.success_count}/{self.total} {self.item_name}s succeeded"
