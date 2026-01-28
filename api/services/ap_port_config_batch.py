"""
Batch/Parallel AP Port Configuration Service

Extends the core ap_port_config service with:
- Parallel processing across APs (semaphore-controlled)
- Fire-and-forget API calls with bulk polling
- Job persistence via workflow model
- SSE progress streaming
- Redis request ID tracking

Usage:
- For 1-10 APs: Use the standard configure_ap_ports() (sequential)
- For 10+ APs: Use configure_ap_ports_batch() (parallel with progress)
"""

import asyncio
import logging
import uuid
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime
from dataclasses import dataclass, field, asdict

from services.ap_port_config import (
    APPortRequest,
    APPortResult,
    PortConfig,
    PortMode,
    resolve_port_configs,
    is_uplink_port,
)
from r1api.models import (
    has_configurable_lan_ports,
    get_model_info,
    get_all_ports,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Batch Configuration Models
# ============================================================================

@dataclass
class BatchConfig:
    """Configuration for batch processing"""
    max_concurrent_aps: int = 20       # Max APs processed in parallel
    max_concurrent_api_calls: int = 20  # Max concurrent API calls (rate limit)
    poll_interval_seconds: int = 3      # Bulk polling interval
    max_poll_seconds: int = 120         # Max time to wait for all activities
    assume_success_on_timeout: bool = True  # Assume success if activity never appears

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BatchProgress:
    """Progress tracking for batch operations"""
    total_aps: int = 0
    processed_aps: int = 0
    pending_requests: int = 0
    completed_requests: int = 0
    failed_requests: int = 0
    current_phase: str = "initializing"

    # Detailed tracking
    aps_configured: List[str] = field(default_factory=list)
    aps_already_correct: List[str] = field(default_factory=list)
    aps_failed: List[str] = field(default_factory=list)
    aps_skipped: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PendingOperation:
    """Tracks a fire-and-forget operation awaiting completion"""
    request_id: str
    ap_serial: str
    ap_name: str
    operation_type: str  # 'disable_inheritance', 'activate_profile', 'set_vlan', 'disable_port'
    port_id: Optional[str] = None
    vlan: Optional[int] = None
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            'request_id': self.request_id,
            'ap_serial': self.ap_serial,
            'ap_name': self.ap_name,
            'operation_type': self.operation_type,
            'port_id': self.port_id,
            'vlan': self.vlan,
            'created_at': self.created_at.isoformat(),
        }


# ============================================================================
# Sync Helper for Parallel Execution
# ============================================================================

def _fetch_ap_port_settings_sync(
    r1_client,
    tenant_id: str,
    venue_id: str,
    serial_number: str,
    model: str,
) -> Dict[str, Any]:
    """
    Sync helper to fetch all port settings for an AP.
    Runs in thread pool via run_in_executor for true parallelism.
    """
    result = {
        'poeMode': None,
        'poeOut': False,
        'useVenueSettings': True,
        'ports': []
    }

    # Skip LAN port queries for models without LAN ports
    if model and not has_configurable_lan_ports(model):
        return result

    try:
        # Get AP-level specific settings
        if r1_client.ec_type == "MSP":
            response = r1_client.get(
                f"/venues/{venue_id}/aps/{serial_number}/lanPortSpecificSettings",
                override_tenant_id=tenant_id
            )
        else:
            response = r1_client.get(
                f"/venues/{venue_id}/aps/{serial_number}/lanPortSpecificSettings"
            )

        if response.status_code == 200:
            specific_settings = response.json()
            result['poeMode'] = specific_settings.get('poeMode')
            result['poeOut'] = specific_settings.get('poeOut', False)
            result['useVenueSettings'] = specific_settings.get('useVenueSettings', True)

        # Determine which ports to query based on model
        ports_to_query = get_all_ports(model) if model else ['LAN1', 'LAN2', 'LAN3', 'LAN4']
        if not ports_to_query:
            ports_to_query = ['LAN1', 'LAN2']

        # Query each port
        for port_id in ports_to_query:
            port_number = port_id.replace('LAN', '')
            try:
                if r1_client.ec_type == "MSP":
                    response = r1_client.get(
                        f"/venues/{venue_id}/aps/{serial_number}/lanPorts/{port_number}/settings",
                        override_tenant_id=tenant_id
                    )
                else:
                    response = r1_client.get(
                        f"/venues/{venue_id}/aps/{serial_number}/lanPorts/{port_number}/settings"
                    )

                if response.status_code == 200:
                    port_settings = response.json()
                    result['ports'].append({
                        'portId': port_id,
                        'enabled': port_settings.get('enabled', True),
                        'untagId': port_settings.get('overwriteUntagId'),
                        'type': port_settings.get('overwriteType', 'ACCESS'),
                        'vlanMembers': port_settings.get('overwriteVlanMembers', '')
                    })
            except Exception as e:
                logger.debug(f"Error getting port {port_id} settings for {serial_number}: {e}")

    except Exception as e:
        logger.warning(f"Error fetching settings for {serial_number}: {e}")

    return result


# ============================================================================
# Batch Port Configuration Service
# ============================================================================

class APPortConfigBatchService:
    """
    Batch/parallel AP port configuration with fire-and-forget pattern.

    Architecture:
    1. Phase 1 (Fetch): Parallel fetch of current port settings (idempotency check)
    2. Phase 2 (Configure): Fire-and-forget API calls with request ID collection
    3. Phase 3 (Poll): Bulk poll all pending activities for completion

    Throttling:
    - Semaphore controls max concurrent API calls
    - APs processed in batches to manage memory
    - Configurable rate limits
    """

    def __init__(
        self,
        r1_client,
        tenant_id: str,
        venue_id: str,
        batch_config: Optional[BatchConfig] = None,
        progress_callback: Optional[Callable[[BatchProgress], None]] = None,
        event_publisher = None,  # WorkflowEventPublisher for SSE
        job_id: Optional[str] = None,
    ):
        self.r1_client = r1_client
        self.tenant_id = tenant_id
        self.venue_id = venue_id
        self.config = batch_config or BatchConfig()
        self.progress_callback = progress_callback
        self.event_publisher = event_publisher
        self.job_id = job_id or str(uuid.uuid4())

        # Semaphore for API call rate limiting
        self._api_semaphore = asyncio.Semaphore(self.config.max_concurrent_api_calls)

        # Track pending operations
        self._pending_operations: List[PendingOperation] = []

        # Progress state
        self.progress = BatchProgress()

        # Results
        self.results = {
            'configured': [],
            'already_configured': [],
            'failed': [],
            'skipped': [],
        }

    async def _emit_progress(self, message: str = None):
        """Emit progress update via callback and/or SSE"""
        if self.progress_callback:
            await self.progress_callback(self.progress)

        if self.event_publisher and self.job_id:
            await self.event_publisher.progress_update(
                self.job_id,
                {
                    'progress': self.progress.to_dict(),
                    'message': message,
                }
            )

        if message:
            logger.info(f"[Batch {self.job_id[:8]}] {message}")

    async def _api_call_with_semaphore(self, coro):
        """Execute API call with semaphore rate limiting"""
        async with self._api_semaphore:
            return await coro

    async def _run_sync_in_executor(self, func, *args, **kwargs):
        """Run a sync function in thread pool for true parallelism"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: func(*args, **kwargs)
        )

    async def configure_batch(
        self,
        ap_configs: List[APPortRequest],
        all_aps: List[Dict[str, Any]],
        default_profile_id: str,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Configure ports on multiple APs in parallel with fire-and-forget.

        Args:
            ap_configs: List of AP port configurations
            all_aps: List of all APs in venue (for model lookup)
            default_profile_id: ID of Default ACCESS Port profile
            dry_run: If True, only compute what would change

        Returns:
            Results dict with configured, already_configured, failed, skipped lists
        """
        self.progress.total_aps = len(ap_configs)
        self.progress.current_phase = "preparing"
        await self._emit_progress(f"Starting batch configuration for {len(ap_configs)} APs")

        # Build AP lookup
        ap_lookup_by_name = {ap.get('name', '').lower(): ap for ap in all_aps}
        ap_lookup_by_serial = {ap.get('serialNumber', '').upper(): ap for ap in all_aps}

        # Phase 1: Fetch current settings and determine what needs to change
        self.progress.current_phase = "fetching_settings"
        await self._emit_progress("Phase 1: Fetching current port settings...")

        ap_work_items = await self._prepare_work_items(
            ap_configs, ap_lookup_by_name, ap_lookup_by_serial, default_profile_id
        )

        if dry_run:
            # For dry run, just return what would be configured
            self.progress.current_phase = "dry_run_complete"
            await self._emit_progress(f"Dry run complete: {len(self.results['configured'])} would configure")
            return self._build_final_results(dry_run=True)

        # Phase 2: Fire-and-forget configuration calls
        self.progress.current_phase = "configuring"
        await self._emit_progress("Phase 2: Configuring ports (fire-and-forget)...")

        await self._execute_configurations(ap_work_items, default_profile_id)

        # Phase 3: Bulk poll for completion
        if self._pending_operations:
            self.progress.current_phase = "polling"
            self.progress.pending_requests = len(self._pending_operations)
            await self._emit_progress(f"Phase 3: Waiting for {len(self._pending_operations)} operations to complete...")

            await self._bulk_poll_completion()

        self.progress.current_phase = "complete"
        await self._emit_progress("Batch configuration complete")

        return self._build_final_results(dry_run=False)

    async def _prepare_work_items(
        self,
        ap_configs: List[APPortRequest],
        ap_lookup_by_name: Dict,
        ap_lookup_by_serial: Dict,
        default_profile_id: str,
    ) -> List[Dict]:
        """
        Phase 1: Fetch current settings and prepare work items.
        Uses parallel fetching with semaphore control.
        """
        work_items = []

        async def fetch_ap_settings(ap_config: APPortRequest):
            """Fetch settings for a single AP"""
            identifier = ap_config.ap_identifier

            # Find AP
            ap = ap_lookup_by_name.get(identifier.lower()) or ap_lookup_by_serial.get(identifier.upper())
            if not ap:
                return {
                    'status': 'skipped',
                    'ap_config': ap_config,
                    'ap': None,
                    'reason': f"AP '{identifier}' not found in venue",
                }

            serial = ap.get('serialNumber')
            model = ap.get('model', '')
            ap_name = ap.get('name', serial)

            # Check if model has configurable ports
            if not has_configurable_lan_ports(model):
                return {
                    'status': 'skipped',
                    'ap_config': ap_config,
                    'ap': ap,
                    'reason': f"Model {model} has no configurable LAN ports",
                }

            # Get port configs from request
            port_configs = ap_config.get_port_configs()
            if not port_configs:
                return {
                    'status': 'skipped',
                    'ap_config': ap_config,
                    'ap': ap,
                    'reason': "No port configurations specified",
                }

            # Resolve port configs (uplink protection)
            ports_to_configure, protected_ports = resolve_port_configs(port_configs, model)

            if not ports_to_configure:
                return {
                    'status': 'skipped',
                    'ap_config': ap_config,
                    'ap': ap,
                    'reason': "No ports to configure after uplink protection",
                }

            # Fetch current settings using sync helper in thread pool (true parallelism)
            current_port_settings = {}
            try:
                # Run sync HTTP calls in thread pool for parallel execution
                port_settings_response = await self._run_sync_in_executor(
                    _fetch_ap_port_settings_sync,
                    self.r1_client,
                    self.tenant_id,
                    self.venue_id,
                    serial,
                    model,
                )

                ports_array = port_settings_response.get('ports', [])
                for port_data in ports_array:
                    port_id = port_data.get('portId', '')
                    if port_id:
                        vlan = port_data.get('untagId') or port_data.get('overwriteUntagId')
                        port_type = port_data.get('type') or port_data.get('overwriteType') or 'ACCESS'
                        current_port_settings[port_id] = {
                            'vlan': vlan,
                            'type': port_type,
                            'enabled': port_data.get('enabled', True)
                        }
            except Exception as e:
                logger.warning(f"Could not fetch settings for {ap_name}: {e}")

            # Determine what needs to change (idempotency check)
            ports_needing_changes = []
            ports_already_correct = []

            for port_config in ports_to_configure:
                port_id = port_config['port_id']
                current = current_port_settings.get(port_id, {})

                if port_config['action'] == 'configure':
                    target_vlan = port_config['vlan']
                    current_vlan = current.get('vlan')
                    current_type = current.get('type', '').upper() if current.get('type') else ''

                    try:
                        current_vlan_int = int(current_vlan) if current_vlan is not None else None
                    except (ValueError, TypeError):
                        current_vlan_int = None

                    if current_type == 'ACCESS' and current_vlan_int == target_vlan:
                        ports_already_correct.append(port_config)
                    else:
                        ports_needing_changes.append(port_config)

                elif port_config['action'] == 'disable':
                    if not current.get('enabled', True):
                        ports_already_correct.append(port_config)
                    else:
                        ports_needing_changes.append(port_config)

            if not ports_needing_changes:
                return {
                    'status': 'already_configured',
                    'ap_config': ap_config,
                    'ap': ap,
                    'ports_already_correct': ports_already_correct,
                }

            return {
                'status': 'needs_config',
                'ap_config': ap_config,
                'ap': ap,
                'ports_needing_changes': ports_needing_changes,
                'ports_already_correct': ports_already_correct,
                'protected_ports': protected_ports,
            }

        # Fetch all AP settings in parallel (controlled by semaphore)
        tasks = [fetch_ap_settings(config) for config in ap_configs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Error fetching settings: {result}")
                work_items.append({
                    'status': 'failed',
                    'ap_config': ap_configs[i],
                    'ap': None,
                    'reason': str(result),
                })
            else:
                work_items.append(result)

                # Update progress tracking
                if result['status'] == 'skipped':
                    self.progress.aps_skipped.append(result['ap_config'].ap_identifier)
                    self._add_result('skipped', result)
                elif result['status'] == 'already_configured':
                    self.progress.aps_already_correct.append(result['ap_config'].ap_identifier)
                    self._add_result('already_configured', result)
                elif result['status'] == 'failed':
                    self.progress.aps_failed.append(result['ap_config'].ap_identifier)
                    self._add_result('failed', result)

            self.progress.processed_aps += 1

        await self._emit_progress(
            f"Settings fetched: {len([w for w in work_items if w['status'] == 'needs_config'])} need changes"
        )

        return [w for w in work_items if w['status'] == 'needs_config']

    def _add_result(self, category: str, work_item: Dict):
        """Add a result to the appropriate category"""
        ap = work_item.get('ap', {})
        ap_config = work_item.get('ap_config')

        result = APPortResult(
            ap_identifier=ap_config.ap_identifier if ap_config else 'unknown',
            ap_id=ap.get('id') if ap else None,
            ap_serial=ap.get('serialNumber') if ap else None,
            ap_model=ap.get('model') if ap else None,
            status=category,
            skipped_reason=work_item.get('reason'),
            ports_already_correct=[
                {'port_id': p['port_id'], 'vlan': p.get('vlan')}
                for p in work_item.get('ports_already_correct', [])
            ],
        )

        self.results[category].append(result.to_dict())

    async def _execute_configurations(
        self,
        work_items: List[Dict],
        default_profile_id: str,
    ):
        """
        Phase 2: Execute port configurations using fire-and-forget pattern.
        Collects request IDs for later bulk polling.
        """

        async def configure_ap(work_item: Dict):
            """Configure a single AP's ports (fire-and-forget)"""
            ap = work_item['ap']
            ap_config = work_item['ap_config']
            ports_needing_changes = work_item['ports_needing_changes']

            serial = ap.get('serialNumber')
            ap_name = ap.get('name', serial)

            configured_ports = []
            local_request_ids = []

            try:
                # Step 1: Disable venue settings inheritance (fire-and-forget)
                result = await self._api_call_with_semaphore(
                    self.r1_client.venues.set_ap_lan_port_specific_settings(
                        tenant_id=self.tenant_id,
                        venue_id=self.venue_id,
                        serial_number=serial,
                        use_venue_settings=False,
                        wait_for_completion=False,  # Fire-and-forget!
                    )
                )

                if isinstance(result, dict) and result.get('requestId'):
                    request_id = result['requestId']
                    self._pending_operations.append(PendingOperation(
                        request_id=request_id,
                        ap_serial=serial,
                        ap_name=ap_name,
                        operation_type='disable_inheritance',
                    ))
                    local_request_ids.append(request_id)

                # Step 2: Configure each port
                for port_config in ports_needing_changes:
                    port_id = port_config['port_id']

                    if port_config['action'] == 'configure':
                        port_vlan = port_config['vlan']

                        # Activate ACCESS profile (fire-and-forget)
                        result = await self._api_call_with_semaphore(
                            self.r1_client.ethernet_port_profiles.activate_profile_on_ap_lan_port(
                                tenant_id=self.tenant_id,
                                venue_id=self.venue_id,
                                serial_number=serial,
                                port_id=port_id,
                                profile_id=default_profile_id,
                                wait_for_completion=False,
                            )
                        )

                        if isinstance(result, dict) and result.get('requestId'):
                            self._pending_operations.append(PendingOperation(
                                request_id=result['requestId'],
                                ap_serial=serial,
                                ap_name=ap_name,
                                operation_type='activate_profile',
                                port_id=port_id,
                            ))
                            local_request_ids.append(result['requestId'])

                        # Set VLAN (fire-and-forget)
                        result = await self._api_call_with_semaphore(
                            self.r1_client.venues.set_ap_lan_port_settings(
                                tenant_id=self.tenant_id,
                                venue_id=self.venue_id,
                                serial_number=serial,
                                port_id=port_id,
                                untagged_vlan=port_vlan,
                                wait_for_completion=False,
                            )
                        )

                        if isinstance(result, dict) and result.get('requestId'):
                            self._pending_operations.append(PendingOperation(
                                request_id=result['requestId'],
                                ap_serial=serial,
                                ap_name=ap_name,
                                operation_type='set_vlan',
                                port_id=port_id,
                                vlan=port_vlan,
                            ))
                            local_request_ids.append(result['requestId'])

                        configured_ports.append({
                            'port_id': port_id,
                            'vlan': port_vlan,
                            'action': 'configure',
                        })

                    elif port_config['action'] == 'disable':
                        # Disable port (fire-and-forget)
                        result = await self._api_call_with_semaphore(
                            self.r1_client.venues.set_ap_lan_port_enabled(
                                tenant_id=self.tenant_id,
                                venue_id=self.venue_id,
                                serial_number=serial,
                                port_id=port_id,
                                enabled=False,
                                wait_for_completion=False,
                            )
                        )

                        if isinstance(result, dict) and result.get('requestId'):
                            self._pending_operations.append(PendingOperation(
                                request_id=result['requestId'],
                                ap_serial=serial,
                                ap_name=ap_name,
                                operation_type='disable_port',
                                port_id=port_id,
                            ))
                            local_request_ids.append(result['requestId'])

                        configured_ports.append({
                            'port_id': port_id,
                            'action': 'disable',
                        })

                # Record as configured (pending confirmation)
                result_entry = APPortResult(
                    ap_identifier=ap_config.ap_identifier,
                    ap_id=ap.get('id'),
                    ap_serial=serial,
                    ap_model=ap.get('model'),
                    status='success',  # Will be updated if polling fails
                    success=True,
                    ports_configured=configured_ports,
                    ports_already_correct=[
                        {'port_id': p['port_id'], 'vlan': p.get('vlan')}
                        for p in work_item.get('ports_already_correct', [])
                    ],
                )

                self.results['configured'].append(result_entry.to_dict())
                self.progress.aps_configured.append(ap_config.ap_identifier)

                logger.debug(f"Fired {len(local_request_ids)} operations for {ap_name}")

            except Exception as e:
                logger.error(f"Error configuring {ap_name}: {e}")
                result_entry = APPortResult(
                    ap_identifier=ap_config.ap_identifier,
                    ap_serial=serial,
                    ap_model=ap.get('model'),
                    status='failed',
                    success=False,
                    errors=[str(e)],
                )
                self.results['failed'].append(result_entry.to_dict())
                self.progress.aps_failed.append(ap_config.ap_identifier)

        # Execute all AP configurations in parallel (controlled by semaphore)
        tasks = [configure_ap(item) for item in work_items]
        await asyncio.gather(*tasks, return_exceptions=True)

        await self._emit_progress(
            f"Fired {len(self._pending_operations)} operations for {len(work_items)} APs"
        )

    async def _bulk_poll_completion(self):
        """
        Phase 3: Bulk poll all pending operations for completion.
        Uses single bulk query to minimize API load.
        """
        if not self._pending_operations:
            return

        request_ids = [op.request_id for op in self._pending_operations]

        try:
            results = await self.r1_client.await_tasks_bulk_query(
                request_ids=request_ids,
                override_tenant_id=self.tenant_id,
                max_poll_seconds=self.config.max_poll_seconds,
                poll_interval=self.config.poll_interval_seconds,
                assume_success_on_timeout=self.config.assume_success_on_timeout,
                progress_callback=self._on_poll_progress,
            )

            # Process results
            failed_count = 0
            for op in self._pending_operations:
                result = results.get(op.request_id, {})
                status = result.get('status', 'UNKNOWN')

                if status == 'FAIL':
                    failed_count += 1
                    logger.warning(
                        f"Operation failed: {op.operation_type} on {op.ap_name} "
                        f"port {op.port_id}: {result.get('error', 'Unknown error')}"
                    )

            self.progress.completed_requests = len(request_ids) - failed_count
            self.progress.failed_requests = failed_count

            await self._emit_progress(
                f"Polling complete: {self.progress.completed_requests} succeeded, "
                f"{self.progress.failed_requests} failed"
            )

        except Exception as e:
            logger.error(f"Bulk polling failed: {e}")
            # If bulk polling fails entirely, log but don't fail the job
            # The fire-and-forget operations likely still succeeded
            await self._emit_progress(f"Warning: Polling failed ({e}), operations may have succeeded")

    async def _on_poll_progress(self, completed: int, total: int, results: Dict):
        """Callback for bulk polling progress"""
        self.progress.completed_requests = completed
        self.progress.pending_requests = total - completed
        await self._emit_progress(f"Polling: {completed}/{total} complete")

    def _build_final_results(self, dry_run: bool) -> Dict[str, Any]:
        """Build the final results dictionary"""
        return {
            'job_id': self.job_id,
            'dry_run': dry_run,
            'configured': self.results['configured'],
            'already_configured': self.results['already_configured'],
            'failed': self.results['failed'],
            'skipped': self.results['skipped'],
            'summary': {
                'total_requested': self.progress.total_aps,
                'configured': len(self.results['configured']),
                'already_configured': len(self.results['already_configured']),
                'failed': len(self.results['failed']),
                'skipped': len(self.results['skipped']),
            },
            'batch_config': self.config.to_dict(),
            'progress': self.progress.to_dict(),
        }


# ============================================================================
# High-level batch configuration function
# ============================================================================

async def configure_ap_ports_batch(
    r1_client,
    venue_id: str,
    tenant_id: str,
    ap_configs: List[APPortRequest],
    batch_config: Optional[BatchConfig] = None,
    dry_run: bool = False,
    progress_callback: Optional[Callable] = None,
    event_publisher = None,
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Configure LAN ports on multiple APs using parallel batch processing.

    This is the high-level entry point for batch operations.

    Args:
        r1_client: RuckusONE API client
        venue_id: Venue ID
        tenant_id: Tenant ID
        ap_configs: List of APPortRequest objects
        batch_config: Optional batch configuration (throttling, etc.)
        dry_run: If True, don't actually make changes
        progress_callback: Optional callback for progress updates
        event_publisher: Optional WorkflowEventPublisher for SSE
        job_id: Optional job ID for tracking

    Returns:
        Dict with results and progress information
    """
    # Fetch all APs in venue
    try:
        aps_response = await r1_client.venues.get_aps_by_tenant_venue(tenant_id, venue_id)
        all_aps = aps_response.get('data', [])
        logger.info(f"Found {len(all_aps)} total APs in venue")
    except Exception as e:
        logger.error(f"Failed to fetch APs: {e}")
        return {
            'error': str(e),
            'configured': [],
            'already_configured': [],
            'failed': [],
            'skipped': [],
            'dry_run': dry_run,
        }

    # Find default ACCESS profile
    default_profile = await r1_client.ethernet_port_profiles.find_default_access_profile(
        tenant_id=tenant_id
    )

    if not default_profile:
        return {
            'error': 'Could not find Default ACCESS Port profile',
            'configured': [],
            'already_configured': [],
            'failed': [],
            'skipped': [],
            'dry_run': dry_run,
        }

    default_profile_id = default_profile.get('id')
    logger.info(f"Using default ACCESS profile: {default_profile.get('name')}")

    # Create batch service and run
    service = APPortConfigBatchService(
        r1_client=r1_client,
        tenant_id=tenant_id,
        venue_id=venue_id,
        batch_config=batch_config,
        progress_callback=progress_callback,
        event_publisher=event_publisher,
        job_id=job_id,
    )

    return await service.configure_batch(
        ap_configs=ap_configs,
        all_aps=all_aps,
        default_profile_id=default_profile_id,
        dry_run=dry_run,
    )
