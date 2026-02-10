#!/usr/bin/env python3
"""
Diagnostic script to check workflow job state in Redis.

Usage:
    python scripts/diagnose_job.py <job_id>
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from workflow.v2.state_manager import WorkflowStateManager


async def diagnose_job(job_id: str):
    """Diagnose a stuck workflow job."""
    redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    state_manager = WorkflowStateManager(redis_url)
    await state_manager.connect()

    try:
        job = await state_manager.get_job(job_id)
        if not job:
            print(f"Job {job_id} not found")
            return

        print(f"\n{'='*60}")
        print(f"JOB DIAGNOSIS: {job_id}")
        print(f"{'='*60}")
        print(f"Status: {job.status}")
        print(f"Workflow: {job.workflow_name}")
        print(f"Created: {job.created_at}")
        print(f"Started: {job.started_at}")
        print(f"Errors: {job.errors}")

        # Count phase statuses
        print(f"\n{'='*60}")
        print("GLOBAL PHASE STATUS:")
        print(f"{'='*60}")
        for phase_id, status in job.global_phase_status.items():
            print(f"  {phase_id}: {status}")

        # Analyze unit statuses
        print(f"\n{'='*60}")
        print("UNIT STATUS SUMMARY:")
        print(f"{'='*60}")

        status_counts = {}
        for unit_id, mapping in job.unit_mappings.items():
            status = mapping.status.value if hasattr(mapping.status, 'value') else str(mapping.status)
            status_counts[status] = status_counts.get(status, 0) + 1

        for status, count in sorted(status_counts.items()):
            print(f"  {status}: {count}")

        # Find stuck/running units
        print(f"\n{'='*60}")
        print("RUNNING/IN-PROGRESS UNITS:")
        print(f"{'='*60}")

        for unit_id, mapping in job.unit_mappings.items():
            status = mapping.status.value if hasattr(mapping.status, 'value') else str(mapping.status)
            if status in ('running', 'in_progress', 'pending'):
                print(f"\n  Unit: {unit_id}")
                print(f"    Status: {status}")
                print(f"    Plan: {mapping.plan.model_dump() if hasattr(mapping.plan, 'model_dump') else mapping.plan}")

                # Check phase statuses for this unit
                if hasattr(mapping, 'phase_status') and mapping.phase_status:
                    print(f"    Phase statuses:")
                    for phase_id, phase_status in mapping.phase_status.items():
                        print(f"      {phase_id}: {phase_status}")

        # Check phase results for each unit
        print(f"\n{'='*60}")
        print("UNIT PHASE ANALYSIS:")
        print(f"{'='*60}")

        phase_status_by_phase = {}
        for unit_id, mapping in job.unit_mappings.items():
            if hasattr(mapping, 'phase_status') and mapping.phase_status:
                for phase_id, phase_status in mapping.phase_status.items():
                    if phase_id not in phase_status_by_phase:
                        phase_status_by_phase[phase_id] = {'completed': [], 'failed': [], 'running': [], 'pending': [], 'skipped': []}
                    status_key = phase_status.value if hasattr(phase_status, 'value') else str(phase_status)
                    if status_key in phase_status_by_phase[phase_id]:
                        phase_status_by_phase[phase_id][status_key].append(unit_id)
                    else:
                        phase_status_by_phase[phase_id][status_key] = [unit_id]

        for phase_id, statuses in sorted(phase_status_by_phase.items()):
            print(f"\n  {phase_id}:")
            for status, units in statuses.items():
                if units:
                    print(f"    {status}: {len(units)} units")
                    if status in ('running', 'failed') and len(units) <= 5:
                        print(f"      Units: {units}")

        # Check for units with failed phases
        print(f"\n{'='*60}")
        print("FAILED UNITS DETAIL:")
        print(f"{'='*60}")

        failed_count = 0
        for unit_id, mapping in job.unit_mappings.items():
            status = mapping.status.value if hasattr(mapping.status, 'value') else str(mapping.status)
            if status == 'failed':
                failed_count += 1
                if failed_count <= 5:  # Only show first 5
                    print(f"\n  Unit: {unit_id}")
                    if hasattr(mapping, 'error') and mapping.error:
                        print(f"    Error: {mapping.error}")
                    if hasattr(mapping, 'phase_results') and mapping.phase_results:
                        for phase_id, result in mapping.phase_results.items():
                            if hasattr(result, 'error') and result.error:
                                print(f"    Phase {phase_id} error: {result.error}")

        if failed_count > 5:
            print(f"\n  ... and {failed_count - 5} more failed units")

        # Check for activities
        print(f"\n{'='*60}")
        print("ACTIVITY TRACKING:")
        print(f"{'='*60}")

        # This would need ActivityTracker, but let's just show the structure
        print("  (Would need ActivityTracker to show pending activities)")

    finally:
        await state_manager.disconnect()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/diagnose_job.py <job_id>")
        sys.exit(1)

    job_id = sys.argv[1]
    asyncio.run(diagnose_job(job_id))
