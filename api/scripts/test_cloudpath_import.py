#!/usr/bin/env python3
"""
Test Cloudpath DPSK Import Workflow

Usage:
    python test_cloudpath_import.py <controller_id> <venue_id> [options]

Examples:
    python test_cloudpath_import.py 1 "venue-123"
    python test_cloudpath_import.py 1 "venue-123" --group-by-vlan
    python test_cloudpath_import.py 1 "venue-123" --watch
"""

import sys
import json
import argparse
import requests
from pathlib import Path
from typing import Dict, Any

# API Configuration
API_BASE_URL = "http://localhost:8080/api"
TEST_DATA_FILE = Path(__file__).parent.parent / "test_cloudpath_dpsks.json"


def authenticate(username: str, password: str) -> str:
    """
    Authenticate and get access token

    Args:
        username: Username
        password: Password

    Returns:
        Access token
    """
    print(f"🔐 Authenticating as {username}...")

    response = requests.post(
        f"{API_BASE_URL}/auth/login",
        json={"username": username, "password": password}
    )

    if response.status_code != 200:
        print(f"❌ Authentication failed: {response.status_code}")
        print(response.text)
        sys.exit(1)

    data = response.json()
    token = data.get("access_token")

    print(f"✅ Authenticated successfully")
    return token


def load_test_data() -> list:
    """Load test DPSK data from JSON file"""
    print(f"📁 Loading test data from {TEST_DATA_FILE}...")

    if not TEST_DATA_FILE.exists():
        print(f"❌ Test data file not found: {TEST_DATA_FILE}")
        sys.exit(1)

    with open(TEST_DATA_FILE, 'r') as f:
        data = json.load(f)

    print(f"✅ Loaded {len(data)} test DPSKs")
    return data


def start_import(
    token: str,
    controller_id: int,
    venue_id: str,
    dpsk_data: list,
    group_by_vlan: bool = False,
    tenant_id: str = None
) -> Dict[str, Any]:
    """
    Start Cloudpath DPSK import workflow

    Args:
        token: Auth token
        controller_id: Controller ID
        venue_id: Venue ID
        dpsk_data: List of DPSK objects
        group_by_vlan: Group by VLAN or single pool
        tenant_id: Optional tenant ID for MSP

    Returns:
        Import response
    """
    print(f"\n🚀 Starting import workflow...")
    print(f"   Controller: {controller_id}")
    print(f"   Venue: {venue_id}")
    print(f"   DPSKs: {len(dpsk_data)}")
    print(f"   Strategy: {'Group by VLAN' if group_by_vlan else 'Single pool'}")

    payload = {
        "controller_id": controller_id,
        "venue_id": venue_id,
        "dpsk_data": dpsk_data,
        "options": {
            "just_copy_dpsks": True,
            "group_by_vlan": group_by_vlan
        }
    }

    if tenant_id:
        payload["tenant_id"] = tenant_id

    response = requests.post(
        f"{API_BASE_URL}/cloudpath-dpsk/import",
        headers={"Authorization": f"Bearer {token}"},
        json=payload
    )

    if response.status_code != 200:
        print(f"❌ Import failed: {response.status_code}")
        print(response.text)
        sys.exit(1)

    data = response.json()
    print(f"\n✅ Import started successfully!")
    print(f"   Job ID: {data['job_id']}")
    print(f"   Status: {data['status']}")
    print(f"   Estimated duration: {data.get('estimated_duration_seconds', 300)}s")

    return data


def get_job_status(token: str, job_id: str) -> Dict[str, Any]:
    """Get job status"""
    response = requests.get(
        f"{API_BASE_URL}/cloudpath-dpsk/jobs/{job_id}/status",
        headers={"Authorization": f"Bearer {token}"}
    )

    if response.status_code != 200:
        print(f"❌ Failed to get status: {response.status_code}")
        return None

    return response.json()


def print_status_summary(status: Dict[str, Any]):
    """Print job status summary"""
    print(f"\n📊 Job Status:")
    print(f"   Status: {status['status']}")

    progress = status.get('progress', {})
    print(f"   Progress: {progress.get('completed', 0)}/{progress.get('total_tasks', 0)} tasks ({progress.get('percent', 0):.1f}%)")

    if status.get('current_phase'):
        phase = status['current_phase']
        print(f"   Current Phase: {phase['name']} ({phase['tasks_completed']}/{phase['tasks_total']} tasks)")

    resources = status.get('created_resources', {})
    if resources:
        print(f"\n📦 Created Resources:")
        for res_type, items in resources.items():
            print(f"   {res_type}: {len(items)}")

    errors = status.get('errors', [])
    if errors:
        print(f"\n⚠️  Errors ({len(errors)}):")
        for error in errors[:5]:
            print(f"   - {error}")


def main():
    parser = argparse.ArgumentParser(
        description='Test Cloudpath DPSK import workflow',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single pool import
  python test_cloudpath_import.py 1 "venue-123"

  # Group by VLAN
  python test_cloudpath_import.py 1 "venue-123" --group-by-vlan

  # Watch progress
  python test_cloudpath_import.py 1 "venue-123" --watch

  # Custom credentials
  python test_cloudpath_import.py 1 "venue-123" -u admin -p mypass
        """
    )

    parser.add_argument('controller_id', type=int, help='RuckusONE controller ID')
    parser.add_argument('venue_id', help='Venue ID')

    parser.add_argument('-u', '--username', default='admin@rtools.local',
                        help='Username (default: admin@rtools.local)')
    parser.add_argument('-p', '--password', default='admin',
                        help='Password (default: admin)')
    parser.add_argument('-t', '--tenant-id', help='Tenant/EC ID (for MSP)')
    parser.add_argument('--group-by-vlan', action='store_true',
                        help='Group DPSKs by VLAN instead of single pool')
    parser.add_argument('--watch', action='store_true',
                        help='Watch job progress (check status once)')
    parser.add_argument('--api-url', default='http://localhost:8080/api',
                        help='API base URL')

    args = parser.parse_args()

    # Update API URL if provided
    global API_BASE_URL
    API_BASE_URL = args.api_url

    # Authenticate
    token = authenticate(args.username, args.password)

    # Load test data
    dpsk_data = load_test_data()

    # Start import
    import_result = start_import(
        token=token,
        controller_id=args.controller_id,
        venue_id=args.venue_id,
        dpsk_data=dpsk_data,
        group_by_vlan=args.group_by_vlan,
        tenant_id=args.tenant_id
    )

    job_id = import_result['job_id']

    # Print instructions
    print(f"\n📝 Next Steps:")
    print(f"   1. Monitor with terminal viewer:")
    print(f"      python scripts/watch_workflow.py {job_id}")
    print(f"")
    print(f"   2. Or check status manually:")
    print(f"      curl -H 'Authorization: Bearer {token[:20]}...' \\")
    print(f"           {API_BASE_URL}/cloudpath-dpsk/jobs/{job_id}/status")

    # Optionally watch progress
    if args.watch:
        import time
        print(f"\n👀 Checking job status in 3 seconds...")
        time.sleep(3)

        status = get_job_status(token, job_id)
        if status:
            print_status_summary(status)

            print(f"\n💡 For real-time monitoring, run:")
            print(f"   python scripts/watch_workflow.py {job_id}")


if __name__ == '__main__':
    main()
