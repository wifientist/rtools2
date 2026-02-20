#!/usr/bin/env python3
"""
R1 API Research & Debugging Tool

Tests:
  A: Pre-bind AP group → activate with isAllApGroups=false
  B: Deactivate + reactivate with isAllApGroups=false
  C: Activities query filter exploration (status, product, useCase, sort)
  D: Venue SSID Landscape — shows what the SSID gate's reconciliation sees
  E: Activities fromTime/toTime — validates ActivityTracker's bulk polling pattern
  F: Activities by ID filter — validates query_activities_bulk pattern
  G: Reordered 3-step config — try settings (step 3) before bind (step 2)
  H: Pending activities audit — find stale INPROGRESS requests per network
  I: GET vs QUERY discrepancy — proves R1 returns different data for same network
  J: POST /networkActivations — single-step direct activation to specific AP group

Interactive mode (default):
    python scripts/test_direct_activation.py

CLI mode:
    python scripts/test_direct_activation.py --controller-id 1 --test landscape
    python scripts/test_direct_activation.py --controller-id 1 --test activities-time
    python scripts/test_direct_activation.py --controller-id 1 --test activities-id
    python scripts/test_direct_activation.py --controller-id 1 --test pending-audit
    python scripts/test_direct_activation.py --controller-id 1 --test get-vs-query
    python scripts/test_direct_activation.py \\
        --controller-id 1 --venue-id X --network-id Y --ap-group-id Z --execute
"""

import asyncio
import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# =============================================================================
# DB / R1 Client helpers
# =============================================================================

def get_db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    db_url = os.environ.get("DATABASE_URL", "sqlite:///./rtools.db")
    engine = create_engine(db_url)
    return sessionmaker(bind=engine)()


def list_controllers(db):
    from models.controller import Controller
    controllers = db.query(Controller).filter(
        Controller.controller_type == "RuckusONE"
    ).all()
    return controllers


def get_r1_client(controller_id: int):
    from models.controller import Controller
    from r1api.client import R1Client

    db = get_db_session()
    controller = db.query(Controller).filter(Controller.id == controller_id).first()
    if not controller:
        print(f"Controller {controller_id} not found")
        sys.exit(1)

    client = R1Client(
        tenant_id=controller.r1_tenant_id,
        client_id=controller.get_r1_client_id(),
        shared_secret=controller.get_r1_shared_secret(),
        region=controller.r1_region or "NA",
        ec_type=controller.controller_subtype,
    )
    tenant_id = controller.r1_tenant_id
    db.close()
    return client, tenant_id


# =============================================================================
# Interactive picker
# =============================================================================

def pick(items, label_fn, prompt="Pick one"):
    """Show a numbered list and let user pick."""
    if not items:
        print("  (none found)")
        return None

    for i, item in enumerate(items, 1):
        print(f"  [{i}] {label_fn(item)}")

    while True:
        choice = input(f"\n{prompt} [1-{len(items)}]: ").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(items):
                return items[idx]
        except ValueError:
            pass
        print(f"  Invalid choice, try again.")


async def interactive_pick_resources(client, tenant_id):
    """Walk user through picking venue, AP group, and network."""

    # --- Venues ---
    print("\nFetching venues...")
    if client.ec_type == "MSP":
        tenants_resp = client.get("/tenants")
        tenants_json = tenants_resp.json() if tenants_resp.status_code == 200 else []
        tenants = tenants_json.get('data', tenants_json) if isinstance(tenants_json, dict) else tenants_json
        # Pick tenant first
        if not tenants:
            print("No tenants found")
            return None, None, None, None

        print(f"\nFound {len(tenants)} tenants:")
        tenant_obj = pick(
            tenants,
            lambda t: f"{t.get('name', 'unnamed')} ({t.get('id', '?')})",
            "Pick tenant"
        )
        if not tenant_obj:
            return None, None, None, None
        picked_tenant_id = tenant_obj['id']

        venues_resp = client.get(f"/venues", override_tenant_id=picked_tenant_id)
    else:
        picked_tenant_id = tenant_id
        venues_resp = client.get("/venues")

    venues_json = venues_resp.json() if venues_resp.status_code == 200 else []
    venues = venues_json.get('data', venues_json) if isinstance(venues_json, dict) else venues_json
    if not venues:
        print("No venues found")
        return None, None, None, None

    print(f"\nFound {len(venues)} venues:")
    venue = pick(
        venues,
        lambda v: f"{v.get('name', 'unnamed')} ({v.get('id', '?')})",
        "Pick venue"
    )
    if not venue:
        return None, None, None, None
    venue_id = venue['id']

    # --- AP Groups ---
    print(f"\nFetching AP groups for venue '{venue.get('name')}'...")
    if client.ec_type == "MSP":
        groups_resp = client.get(
            f"/venues/{venue_id}/apGroups", override_tenant_id=picked_tenant_id
        )
    else:
        groups_resp = client.get(f"/venues/{venue_id}/apGroups")

    groups_json = groups_resp.json() if groups_resp.status_code == 200 else []
    groups = groups_json.get('data', groups_json) if isinstance(groups_json, dict) else groups_json
    if not groups:
        print("No AP groups found")
        return None, None, None, None

    print(f"\nFound {len(groups)} AP groups:")
    group = pick(
        groups,
        lambda g: f"{g.get('name', 'unnamed')} ({g.get('id', '?')})",
        "Pick AP group"
    )
    if not group:
        return None, None, None, None
    ap_group_id = group['id']

    # --- WiFi Networks (uses POST /wifiNetworks/query) ---
    print(f"\nFetching WiFi networks...")
    query_body = {
        "fields": ["name", "ssid", "id", "venueApGroups", "nwSubType"],
        "sortField": "name",
        "sortOrder": "ASC",
        "page": 1,
        "pageSize": 500,
    }
    if client.ec_type == "MSP":
        nets_resp = client.post("/wifiNetworks/query", payload=query_body, override_tenant_id=picked_tenant_id)
    else:
        nets_resp = client.post("/wifiNetworks/query", payload=query_body)

    nets_json = nets_resp.json() if nets_resp.status_code == 200 else {}
    all_nets = nets_json.get('data', []) if isinstance(nets_json, dict) else nets_json

    # Categorize: activated on this venue vs not
    activated = []
    not_activated = []
    for net in all_nets:
        venue_ap_groups = net.get('venueApGroups', [])
        on_this_venue = any(v.get('venueId') == venue_id for v in venue_ap_groups)
        if on_this_venue:
            activated.append(net)
        else:
            not_activated.append(net)

    print(f"\n  {len(activated)} networks activated on this venue")
    print(f"  {len(not_activated)} networks NOT activated on this venue")

    print(f"\nWhich network to test with?")
    print(f"  [1] Pick from NOT-activated (test pre-bind hypothesis)")
    print(f"  [2] Pick from ALREADY-activated (test deactivate+reactivate)")
    mode_choice = input("\nChoice [1-2]: ").strip()

    if mode_choice == "2":
        test_mode = "deactivate_reactivate"
        pool = activated
        pool_label = "activated"
    else:
        test_mode = "direct_bind"
        pool = not_activated
        pool_label = "not-activated"

    if not pool:
        print(f"  No {pool_label} networks found")
        return None, None, None, None

    print(f"\n{pool_label.title()} networks:")
    net = pick(
        pool,
        lambda n: f"{n.get('ssid', n.get('name', 'unnamed'))} - {n.get('name', '')} ({n.get('id', '?')})",
        "Pick network"
    )
    if not net:
        return None, None, None, None
    network_id = net['id']

    return picked_tenant_id, venue_id, ap_group_id, network_id, test_mode


# =============================================================================
# API call helper
# =============================================================================

async def r1_put(client, tenant_id, url, payload=None, label=""):
    """Execute a PUT and handle response. Returns (success, result)."""
    print(f"\n  [{label}] PUT {url}")
    if payload:
        print(f"  Payload: {json.dumps(payload, indent=4)}")

    try:
        if client.ec_type == "MSP":
            response = client.put(url, payload=payload, override_tenant_id=tenant_id)
        else:
            response = client.put(url, payload=payload)

        print(f"  Status: {response.status_code}")
        result = response.json() if response.content else {}
        if result:
            print(f"  Response: {json.dumps(result, indent=4)}")

        if response.status_code >= 400:
            print(f"  FAILED")
            return False, result

        # Wait for async task
        request_id = result.get('requestId')
        if response.status_code == 202 and request_id:
            print(f"  Waiting for task {request_id}...")
            try:
                await client.await_task_completion(request_id, override_tenant_id=tenant_id)
                print(f"  Task complete!")
            except Exception as e:
                print(f"  Task FAILED: {e}")
                return False, {"error": str(e)}

        return True, result

    except Exception as e:
        print(f"  ERROR: {e}")
        return False, {"error": str(e)}


async def r1_delete(client, tenant_id, url, label=""):
    """Execute a DELETE. Returns (success, result)."""
    print(f"\n  [{label}] DELETE {url}")

    try:
        if client.ec_type == "MSP":
            response = client.delete(url, override_tenant_id=tenant_id)
        else:
            response = client.delete(url)

        print(f"  Status: {response.status_code}")
        result = response.json() if response.content else {}
        if result:
            print(f"  Response: {json.dumps(result, indent=4)}")

        if response.status_code >= 400:
            print(f"  FAILED")
            return False, result

        request_id = result.get('requestId')
        if response.status_code == 202 and request_id:
            print(f"  Waiting for task {request_id}...")
            try:
                await client.await_task_completion(request_id, override_tenant_id=tenant_id)
                print(f"  Task complete!")
            except Exception as e:
                print(f"  Task FAILED: {e}")
                return False, {"error": str(e)}

        return True, result

    except Exception as e:
        print(f"  ERROR: {e}")
        return False, {"error": str(e)}


# =============================================================================
# Test: Direct bind → activate with isAllApGroups=false
# =============================================================================

async def test_direct_bind(client, tenant_id, venue_id, network_id, ap_group_id):
    """Hypothesis: pre-bind AP group, then activate directly."""

    print("\n" + "=" * 70)
    print("TEST A: Pre-bind AP group, then activate with isAllApGroups=false")
    print("=" * 70)

    # Step 1: Bind AP group to network (before any activation)
    ok, _ = await r1_put(
        client, tenant_id,
        f"/venues/{venue_id}/wifiNetworks/{network_id}/apGroups/{ap_group_id}",
        label="Step 1: Pre-bind AP group",
    )
    if not ok:
        print("\n>>> Step 1 FAILED: R1 won't let us pre-bind. Venue activation required first.")
        return False

    # Step 2: Activate with isAllApGroups=false
    ok, _ = await r1_put(
        client, tenant_id,
        f"/venues/{venue_id}/wifiNetworks/{network_id}/settings",
        payload={
            "apGroups": [{
                "apGroupId": ap_group_id,
                "radioTypes": ["2.4-GHz", "5-GHz"],
                "radio": "Both",
            }],
            "scheduler": {"type": "ALWAYS_ON"},
            "isAllApGroups": False,
            "allApGroupsRadio": "Both",
            "allApGroupsRadioTypes": ["2.4-GHz", "5-GHz"],
            "venueId": venue_id,
            "networkId": network_id,
        },
        label="Step 2: Activate (isAllApGroups=false)",
    )
    if not ok:
        print("\n>>> Step 2 FAILED: Direct activation rejected even after pre-bind.")
        return False

    # Step 3: Configure AP group settings
    ok, _ = await r1_put(
        client, tenant_id,
        f"/venues/{venue_id}/wifiNetworks/{network_id}/apGroups/{ap_group_id}/settings",
        payload={
            "apGroupId": ap_group_id,
            "radioTypes": ["2.4-GHz", "5-GHz"],
            "radio": "Both",
        },
        label="Step 3: Configure AP group settings",
    )
    if not ok:
        print("\n>>> Step 3 FAILED: Could not configure AP group settings.")
        return False

    print("\n>>> ALL STEPS PASSED: Direct activation works!")
    return True


# =============================================================================
# Test: Deactivate from venue, then reactivate with isAllApGroups=false
# =============================================================================

async def test_deactivate_reactivate(client, tenant_id, venue_id, network_id, ap_group_id):
    """Hypothesis: deactivate keeps AP group binding, reactivate with isAllApGroups=false."""

    print("\n" + "=" * 70)
    print("TEST B: Deactivate SSID from venue, check if AP group binding persists,")
    print("        then reactivate with isAllApGroups=false")
    print("=" * 70)

    print("\n  WARNING: This will DEACTIVATE the selected SSID from the venue!")
    confirm = input("  Continue? [y/N]: ").strip().lower()
    if confirm != 'y':
        print("  Aborted.")
        return None

    # Step 1: Deactivate from venue
    ok, _ = await r1_delete(
        client, tenant_id,
        f"/venues/{venue_id}/wifiNetworks/{network_id}",
        label="Step 1: Deactivate SSID from venue",
    )
    if not ok:
        print("\n>>> Step 1 FAILED: Could not deactivate.")
        return False

    print("\n  Pausing 3s for R1 to process deactivation...")
    await asyncio.sleep(3)

    # Step 2: Try to bind AP group (maybe it's still bound?)
    ok_bind, _ = await r1_put(
        client, tenant_id,
        f"/venues/{venue_id}/wifiNetworks/{network_id}/apGroups/{ap_group_id}",
        label="Step 2: Bind AP group (post-deactivation)",
    )
    # Don't bail on failure - it might not be needed if binding persists

    # Step 3: Activate with isAllApGroups=false
    ok, _ = await r1_put(
        client, tenant_id,
        f"/venues/{venue_id}/wifiNetworks/{network_id}/settings",
        payload={
            "apGroups": [{
                "apGroupId": ap_group_id,
                "radioTypes": ["2.4-GHz", "5-GHz"],
                "radio": "Both",
            }],
            "scheduler": {"type": "ALWAYS_ON"},
            "isAllApGroups": False,
            "allApGroupsRadio": "Both",
            "allApGroupsRadioTypes": ["2.4-GHz", "5-GHz"],
            "venueId": venue_id,
            "networkId": network_id,
        },
        label="Step 3: Reactivate (isAllApGroups=false)",
    )
    if not ok:
        print("\n>>> Step 3 FAILED: Direct reactivation rejected.")
        print("    Attempting recovery: re-activate venue-wide...")
        # Recovery: activate venue-wide to restore original state
        await r1_put(
            client, tenant_id,
            f"/venues/{venue_id}/wifiNetworks/{network_id}",
            label="Recovery: Re-activate venue-wide",
        )
        return False

    # Step 4: Configure AP group settings
    ok, _ = await r1_put(
        client, tenant_id,
        f"/venues/{venue_id}/wifiNetworks/{network_id}/apGroups/{ap_group_id}/settings",
        payload={
            "apGroupId": ap_group_id,
            "radioTypes": ["2.4-GHz", "5-GHz"],
            "radio": "Both",
        },
        label="Step 4: Configure AP group settings",
    )
    if not ok:
        print("\n>>> Step 4 FAILED: Could not configure AP group settings.")
        return False

    print("\n>>> ALL STEPS PASSED: Deactivate + direct reactivate works!")
    return True


# =============================================================================
# Test: Reordered 3-step — settings (step 3) before bind (step 2)
# =============================================================================

async def test_reordered_3step(client, tenant_id, venue_id, network_id, ap_group_id):
    """
    Hypothesis: Can we apply AP group settings BEFORE binding the AP group?

    Normal order:  step 1 (isAllApGroups=false) → step 2 (bind group) → step 3 (settings)
    Test order:    step 1 (isAllApGroups=false) → step 3 (settings) → step 2 (bind group)

    If this works, we might be able to reduce venue-wide exposure time by
    pre-configuring settings before the binding commit.

    REQUIRES: SSID already activated on the venue (venue-wide / isAllApGroups=true).
    """
    radio_types = ["2.4-GHz", "5-GHz", "6-GHz"]

    print("\n" + "=" * 70)
    print("TEST G: Reordered 3-Step (settings before bind)")
    print("=" * 70)
    print(f"\n  Normal:  step1 (isAllApGroups=false) → step2 (bind) → step3 (settings)")
    print(f"  Testing: step1 (isAllApGroups=false) → step3 (settings) → step2 (bind)")
    print(f"\n  Network: {network_id}")
    print(f"  Venue:   {venue_id}")
    print(f"  Group:   {ap_group_id}")

    # Verify the SSID is on the venue using BULK query (not individual GET,
    # which returns stale data — the same R1 bug that broke deactivate-and-requeue)
    print("\n  Checking SSID venue state via bulk query...")
    query_body = {
        "fields": ["name", "ssid", "id", "venueApGroups"],
        "filters": {"id": [network_id]},
        "page": 1,
        "pageSize": 1,
    }
    if client.ec_type == "MSP":
        net_resp = client.post("/wifiNetworks/query", payload=query_body, override_tenant_id=tenant_id)
    else:
        net_resp = client.post("/wifiNetworks/query", payload=query_body)

    if net_resp.status_code != 200:
        print(f"  ERROR: Bulk query failed: {net_resp.status_code}")
        return None

    nets = net_resp.json().get('data', [])
    if not nets:
        print(f"  ERROR: Network {network_id} not found in bulk query")
        return None

    net_data = nets[0]
    venue_ap_groups = net_data.get('venueApGroups', [])
    on_venue = False
    is_venue_wide = False
    for vag in venue_ap_groups:
        if vag.get('venueId') == venue_id:
            on_venue = True
            is_venue_wide = vag.get('isAllApGroups', False)
            break

    if not on_venue:
        print(f"  WARNING: Bulk query says SSID is NOT on this venue.")
        print(f"  (Individual GET is known to disagree — proceed with caution)")
        confirm = input("  Continue anyway? [y/N]: ").strip().lower()
        if confirm != 'y':
            return None
    else:
        print(f"  SSID is on venue. isAllApGroups={is_venue_wide}")
        if not is_venue_wide:
            print(f"  WARNING: SSID is already on a specific AP group, not venue-wide.")
            print(f"  This test is designed for venue-wide SSIDs.")
            confirm = input("  Continue anyway? [y/N]: ").strip().lower()
            if confirm != 'y':
                return None

    confirm = input(f"\n  Ready to execute reordered 3-step? [y/N]: ").strip().lower()
    if confirm != 'y':
        print("  Aborted.")
        return None

    # Step 1/3: Set isAllApGroups=false (same as normal order)
    settings_payload = {
        "dual5gEnabled": False,
        "tripleBandEnabled": False,
        "allApGroupsRadio": "Both",
        "isAllApGroups": False,
        "allApGroupsRadioTypes": radio_types,
        "scheduler": None,
        "allApGroupsVlanId": None,
        "oweTransWlanId": None,
        "isEnforced": False,
        "networkId": network_id,
        "apGroups": [{
            "apGroupId": ap_group_id,
            "radioTypes": radio_types,
            "radio": "Both"
        }],
        "venueId": venue_id
    }

    ok, _ = await r1_put(
        client, tenant_id,
        f"/venues/{venue_id}/wifiNetworks/{network_id}/settings",
        payload=settings_payload,
        label="Step 1/3: isAllApGroups=false (normal)",
    )
    if not ok:
        print("\n>>> Step 1/3 FAILED")
        return False

    # REORDERED: Step 3 BEFORE Step 2
    # Try to configure AP group settings before the group is bound
    ok_step3, _ = await r1_put(
        client, tenant_id,
        f"/venues/{venue_id}/wifiNetworks/{network_id}/apGroups/{ap_group_id}/settings",
        payload={
            "apGroupId": ap_group_id,
            "radioTypes": radio_types,
            "radio": "Both",
        },
        label="Step 3/3 EARLY: AP group settings (BEFORE bind)",
    )
    if not ok_step3:
        print("\n>>> Step 3 (early) FAILED — settings rejected before bind.")
        print("    Falling back to normal order: step 2 then step 3...")

        # Recovery: do step 2 then step 3 in normal order
        ok2, _ = await r1_put(
            client, tenant_id,
            f"/venues/{venue_id}/wifiNetworks/{network_id}/apGroups/{ap_group_id}",
            label="Recovery Step 2/3: Bind AP group",
        )
        if ok2:
            await r1_put(
                client, tenant_id,
                f"/venues/{venue_id}/wifiNetworks/{network_id}/apGroups/{ap_group_id}/settings",
                payload={
                    "apGroupId": ap_group_id,
                    "radioTypes": radio_types,
                    "radio": "Both",
                },
                label="Recovery Step 3/3: AP group settings",
            )
        return False

    # If step 3 worked early, now do step 2 (bind)
    ok_step2, _ = await r1_put(
        client, tenant_id,
        f"/venues/{venue_id}/wifiNetworks/{network_id}/apGroups/{ap_group_id}",
        label="Step 2/3 LATE: Bind AP group (AFTER settings)",
    )
    if not ok_step2:
        print("\n>>> Step 2 (late bind) FAILED after early settings.")
        return False

    print("\n>>> ALL STEPS PASSED: Reordered 3-step works! (settings before bind)")
    return True


# =============================================================================
# Test: Pending Activities Audit — find stale INPROGRESS requests
# =============================================================================

async def test_pending_activities_audit(client, tenant_id):
    """
    Audit all INPROGRESS activities for WIFI product.

    Finds:
    - All currently pending/in-progress WIFI activities
    - Groups them by useCase and descriptionData (to identify per-network duplicates)
    - Highlights networks with MULTIPLE pending requests (the R1 bug)
    - Offers to deactivate a specific network to test if that clears stale activities

    This helps diagnose the cascading orphan problem where R1 has multiple
    pending activation requests for the same network that never complete.
    """
    from datetime import datetime, timezone

    print("\n" + "=" * 70)
    print("TEST H: Pending Activities Audit (stale INPROGRESS requests)")
    print("=" * 70)

    # Step 1: Fetch ALL in-progress WIFI activities
    print("\n  Fetching INPROGRESS WIFI activities...")
    all_activities = []
    page = 1
    while True:
        payload = {
            "filters": {
                "status": ["INPROGRESS"],
                "product": ["WIFI"],
            },
            "fields": [
                "startDatetime", "endDatetime", "status", "product",
                "useCase", "descriptionTemplate", "descriptionData",
                "requestId",
            ],
            "page": page,
            "pageSize": 500,
            "sortField": "startDatetime",
            "sortOrder": "ASC",
        }
        if client.ec_type == "MSP":
            resp = client.post("/activities/query", payload=payload, override_tenant_id=tenant_id)
        else:
            resp = client.post("/activities/query", payload=payload)

        if resp.status_code != 200:
            print(f"  ERROR: {resp.status_code} - {resp.text[:200]}")
            return

        data = resp.json()
        activities = data.get('data', [])
        total = data.get('totalCount', 0)
        all_activities.extend(activities)
        print(f"  Page {page}: {len(activities)} activities (total: {total})")

        if len(all_activities) >= total or not activities:
            break
        page += 1

    if not all_activities:
        print("\n  No INPROGRESS WIFI activities found. All clear!")
        return

    print(f"\n  Found {len(all_activities)} INPROGRESS WIFI activities")

    # Debug: show raw descriptionData structure from first activity
    sample_dd = all_activities[0].get('descriptionData')
    if isinstance(sample_dd, list):
        print(f"\n  NOTE: descriptionData is a list (len={len(sample_dd)})")
        if sample_dd:
            print(f"  Sample element: {json.dumps(sample_dd[0], indent=2)[:300]}")
    elif isinstance(sample_dd, dict):
        print(f"\n  descriptionData keys: {list(sample_dd.keys())}")
    else:
        print(f"\n  descriptionData type: {type(sample_dd).__name__} = {sample_dd}")

    # Step 2: Show all activities with details
    print(f"\n  {'─' * 70}")
    print(f"  {'#':>3}  {'UseCase':<35} {'Age':>8}  RequestId")
    print(f"  {'─' * 70}")

    now = datetime.now(timezone.utc)

    # Helper to normalize descriptionData (R1 returns array of NameValuePairs)
    def _normalize_desc(act):
        dd = act.get('descriptionData', {}) or {}
        if isinstance(dd, list):
            result = {}
            for item in dd:
                if not isinstance(item, dict):
                    continue
                # R1 NameValuePair: {"name": "wifiNetworkName", "value": "..."}
                if 'name' in item:
                    result[item['name']] = item.get('value', '')
                # Alternate format: {"key": "...", "value": "..."}
                elif 'key' in item:
                    result[item['key']] = item.get('value', '')
            return result if result else {}
        if not isinstance(dd, dict):
            dd = {}
        return dd

    for i, act in enumerate(all_activities, 1):
        use_case = act.get('useCase', '?')
        request_id = act.get('requestId', '?')
        start_str = act.get('startDatetime', '')
        desc_data = _normalize_desc(act)

        # Calculate age
        age_str = "?"
        if start_str:
            try:
                start = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                age = now - start
                if age.total_seconds() > 86400:
                    age_str = f"{age.days}d{age.seconds // 3600}h"
                elif age.total_seconds() > 3600:
                    age_str = f"{age.seconds // 3600}h{(age.seconds % 3600) // 60}m"
                else:
                    age_str = f"{age.seconds // 60}m{age.seconds % 60}s"
            except (ValueError, TypeError):
                pass

        print(f"  {i:3}  {use_case:<35} {age_str:>8}  {request_id[:16]}...")

        # Show all descriptionData fields on sub-line
        if desc_data:
            parts = [f"{k}={v}" for k, v in desc_data.items()]
            print(f"       └─ {', '.join(parts)}")
        # Also show descriptionTemplate if available (has full human-readable text)
        desc_tpl = act.get('descriptionTemplate', '')
        if desc_tpl and not desc_data:
            print(f"       └─ template: {desc_tpl[:120]}")

    # Step 3: Group by network (if descriptionData has wifiNetworkName/Id)
    network_activities = {}  # keyed by network_id
    network_names = {}  # network_id -> name (for display)
    has_network_id = {}  # track which keys are real network IDs vs requestId fallbacks
    for act in all_activities:
        desc_data = _normalize_desc(act)
        net_name = desc_data.get('wifiNetworkName', '')
        net_id = desc_data.get('wifiNetworkId', '')
        key = net_id or f"req:{act.get('requestId', 'unknown')}"
        has_network_id[key] = bool(net_id)

        if net_name and key:
            network_names[key] = net_name

        if key not in network_activities:
            network_activities[key] = []
        network_activities[key].append(act)

    # Find networks with MULTIPLE pending requests
    multi = {k: v for k, v in network_activities.items() if len(v) > 1}

    if multi:
        print(f"\n  {'=' * 65}")
        print(f"  WARNING: {len(multi)} networks have MULTIPLE pending requests!")
        print(f"  {'=' * 65}")
        for net_key, acts in sorted(multi.items(), key=lambda x: -len(x[1])):
            display = network_names.get(net_key, net_key)
            if display != net_key:
                display = f"{display} ({net_key[:16]}...)"
            print(f"\n  {display}: {len(acts)} pending requests")
            for act in acts:
                use_case = act.get('useCase', '?')
                age_str = "?"
                start_str = act.get('startDatetime', '')
                if start_str:
                    try:
                        start = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                        age = now - start
                        age_str = f"{int(age.total_seconds())}s"
                    except (ValueError, TypeError):
                        pass
                print(f"    - {use_case} (age: {age_str}, req: {act.get('requestId', '?')[:16]}...)")
    else:
        print(f"\n  No networks with multiple pending requests. Each network has at most 1.")

    # Step 4: Summary
    use_case_counts = {}
    for act in all_activities:
        uc = act.get('useCase', 'unknown')
        use_case_counts[uc] = use_case_counts.get(uc, 0) + 1

    print(f"\n  Summary by useCase:")
    for uc, count in sorted(use_case_counts.items(), key=lambda x: -x[1]):
        print(f"    {count:3}x {uc}")

    # Step 5: Offer to deactivate a network to test clearing
    print(f"\n  {'─' * 65}")
    print(f"  Options:")
    print(f"    [1] Pick a network to deactivate (test if it clears stale activities)")
    print(f"    [2] Re-check activities after waiting")
    print(f"    [q] Done")

    choice = input("\n  Choice: ").strip().lower()

    if choice == "1":
        # Need venue_id for deactivation — ask for it
        print("\n  To deactivate, I need the venue ID.")
        print("  Fetching venues...")
        if client.ec_type == "MSP":
            venues_resp = client.get("/venues", override_tenant_id=tenant_id)
        else:
            venues_resp = client.get("/venues")

        venues_json = venues_resp.json() if venues_resp.status_code == 200 else []
        venues = venues_json.get('data', venues_json) if isinstance(venues_json, dict) else venues_json

        venue = pick(
            venues,
            lambda v: f"{v.get('name', 'unnamed')} ({v.get('id', '?')})",
            "Pick venue"
        )
        if not venue:
            return
        venue_id = venue['id']

        # Pick from networks with pending activities
        net_keys = list(network_activities.keys())
        print(f"\n  Networks with pending activities:")
        net_choice = pick(
            net_keys,
            lambda k: f"{network_names.get(k, k)} ({len(network_activities[k])} pending)",
            "Pick network to deactivate"
        )
        if not net_choice:
            return

        # Resolve to a real network ID
        display_name = network_names.get(net_choice, net_choice)

        if has_network_id.get(net_choice):
            # Key IS the network ID
            net_id = net_choice
        else:
            # Key is a req: fallback — we don't have the real network ID
            print(f"\n  WARNING: Could not extract network ID from activity metadata.")
            print(f"  The activity requestId is: {net_choice.replace('req:', '')}")
            net_id = input("  Enter the actual network ID to deactivate (or 'q' to cancel): ").strip()
            if not net_id or net_id.lower() == 'q':
                return

        print(f"\n  Will deactivate network '{display_name}' (ID: {net_id}) from venue {venue_id}")
        confirm = input("  This will REMOVE the SSID from the venue. Continue? [y/N]: ").strip().lower()
        if confirm != 'y':
            print("  Aborted.")
            return

        ok, _ = await r1_delete(
            client, tenant_id,
            f"/venues/{venue_id}/wifiNetworks/{net_id}",
            label="Deactivate SSID from venue",
        )

        if ok:
            print("\n  Deactivation sent. Waiting 5s then re-checking activities...")
            await asyncio.sleep(5)

            # Re-query to see if activities cleared
            recheck_payload = {
                "filters": {
                    "status": ["INPROGRESS"],
                    "product": ["WIFI"],
                },
                "fields": ["status", "useCase", "descriptionData", "requestId"],
                "page": 1,
                "pageSize": 500,
                "sortField": "startDatetime",
                "sortOrder": "ASC",
            }
            if client.ec_type == "MSP":
                resp2 = client.post("/activities/query", payload=recheck_payload, override_tenant_id=tenant_id)
            else:
                resp2 = client.post("/activities/query", payload=recheck_payload)

            if resp2.status_code == 200:
                data2 = resp2.json()
                remaining = data2.get('totalCount', '?')
                print(f"\n  After deactivation: {remaining} INPROGRESS WIFI activities remain (was {len(all_activities)})")
            else:
                print(f"  Re-check failed: {resp2.status_code}")

    elif choice == "2":
        wait_secs = input("  Wait how many seconds? [30]: ").strip()
        wait_secs = int(wait_secs) if wait_secs.isdigit() else 30
        print(f"  Waiting {wait_secs}s...")
        await asyncio.sleep(wait_secs)

        recheck_payload = {
            "filters": {
                "status": ["INPROGRESS"],
                "product": ["WIFI"],
            },
            "fields": ["status", "useCase", "requestId"],
            "page": 1,
            "pageSize": 500,
        }
        if client.ec_type == "MSP":
            resp2 = client.post("/activities/query", payload=recheck_payload, override_tenant_id=tenant_id)
        else:
            resp2 = client.post("/activities/query", payload=recheck_payload)

        if resp2.status_code == 200:
            data2 = resp2.json()
            remaining = data2.get('totalCount', '?')
            print(f"\n  After wait: {remaining} INPROGRESS WIFI activities (was {len(all_activities)})")
        else:
            print(f"  Re-check failed: {resp2.status_code}")


# =============================================================================
# Test: GET vs QUERY discrepancy — R1 API consistency bug
# =============================================================================

async def test_get_vs_query(client, tenant_id):
    """
    Proves the R1 API returns DIFFERENT data for the same WiFi network
    depending on which endpoint you use:

      GET  /wifiNetworks/{id}        → individual entity fetch
      POST /wifiNetworks/query       → bulk search/query

    The discrepancy is in the `venueApGroups` field. The individual GET often
    returns stale or empty venueApGroups, while the bulk query returns accurate
    data showing the SSID correctly activated on a venue.

    This bug caused our deactivate-and-requeue feature to never fire: the GET
    said "SSID not on venue" so we skipped deactivation, while the bulk query
    (used by reconcile) correctly showed orphaned SSIDs on All AP Groups.

    Output is designed to be copy-pasted as evidence for a bug report.
    """
    from datetime import datetime, timezone

    print("\n" + "=" * 70)
    print("TEST I: GET vs QUERY Discrepancy (R1 API Consistency Bug)")
    print("=" * 70)
    print()
    print("  This test calls BOTH endpoints for the same network(s) and")
    print("  compares the venueApGroups field to show the discrepancy.")
    print()

    # Mode selection
    print("  How do you want to pick networks?")
    print("    [1] Pick from venue landscape (interactive)")
    print("    [2] Test ALL venue-wide SSIDs for a venue (bulk comparison)")
    mode = input("\n  Choice [1-2]: ").strip()

    # Pick venue first
    print("\n  Fetching venues...")
    if client.ec_type == "MSP":
        venues_resp = client.get("/venues", override_tenant_id=tenant_id)
    else:
        venues_resp = client.get("/venues")

    venues_json = venues_resp.json() if venues_resp.status_code == 200 else []
    venues = venues_json.get('data', venues_json) if isinstance(venues_json, dict) else venues_json

    venue = pick(
        venues,
        lambda v: f"{v.get('name', 'unnamed')} ({v.get('id', '?')})",
        "Pick venue"
    )
    if not venue:
        return
    venue_id = venue['id']
    venue_name = venue.get('name', 'unnamed')

    # Fetch all networks via bulk query
    print(f"\n  Fetching all WiFi networks via POST /wifiNetworks/query ...")
    query_body = {
        "fields": ["name", "ssid", "id", "venueApGroups", "nwSubType"],
        "sortField": "name",
        "sortOrder": "ASC",
        "page": 1,
        "pageSize": 500,
    }
    if client.ec_type == "MSP":
        query_resp = client.post("/wifiNetworks/query", payload=query_body, override_tenant_id=tenant_id)
    else:
        query_resp = client.post("/wifiNetworks/query", payload=query_body)

    if query_resp.status_code != 200:
        print(f"  ERROR: Bulk query failed: {query_resp.status_code}")
        return

    query_data = query_resp.json()
    all_nets = query_data.get('data', [])
    total_count = query_data.get('totalCount', len(all_nets))

    # Handle pagination
    while len(all_nets) < total_count:
        query_body['page'] += 1
        if client.ec_type == "MSP":
            page_resp = client.post("/wifiNetworks/query", payload=query_body, override_tenant_id=tenant_id)
        else:
            page_resp = client.post("/wifiNetworks/query", payload=query_body)
        page_data = page_resp.json() if page_resp.status_code == 200 else {}
        page_nets = page_data.get('data', [])
        if not page_nets:
            break
        all_nets.extend(page_nets)

    print(f"  Got {len(all_nets)} networks total")

    # Find networks on this venue
    venue_nets = []
    for net in all_nets:
        for vag in net.get('venueApGroups', []):
            if vag.get('venueId') == venue_id:
                venue_nets.append({
                    'id': net['id'],
                    'ssid': net.get('ssid', net.get('name', '?')),
                    'name': net.get('name', '?'),
                    'is_all': vag.get('isAllApGroups', False),
                    'query_vag': vag,
                    'query_all_vags': net.get('venueApGroups', []),
                })
                break

    venue_wide = [n for n in venue_nets if n['is_all']]
    specific = [n for n in venue_nets if not n['is_all']]
    print(f"  On venue '{venue_name}': {len(venue_wide)} venue-wide, {len(specific)} specific")

    # Pick which networks to test
    if mode == "2":
        # Test ALL venue-wide SSIDs
        targets = venue_wide
        if not targets:
            print("  No venue-wide SSIDs to test")
            return
        print(f"\n  Will test ALL {len(targets)} venue-wide SSIDs")
    else:
        # Interactive pick
        print(f"\n  All networks on venue '{venue_name}':")
        net_choice = pick(
            venue_nets,
            lambda n: f"{'[VW]' if n['is_all'] else '[SG]'} {n['ssid']:<40} ({n['id'][:12]}...)",
            "Pick network to compare"
        )
        if not net_choice:
            return
        targets = [net_choice]

    # Run the comparison
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    matches = 0
    mismatches = 0

    print(f"\n{'=' * 70}")
    print(f"  GET vs QUERY Comparison — {timestamp}")
    print(f"  Venue: {venue_name} ({venue_id})")
    print(f"  Networks to test: {len(targets)}")
    print(f"{'=' * 70}")

    # Build lookup from the bulk query we already have (no extra API calls)
    bulk_by_id = {}
    for net in all_nets:
        bulk_by_id[net['id']] = net

    def _vag_summary(vag):
        """One-line summary of a venueApGroup entry."""
        if not vag:
            return "(NOT on venue)"
        is_all = vag.get('isAllApGroups', False)
        ap_count = len(vag.get('apGroupIds', []))
        if is_all:
            return f"isAllApGroups=True, apGroups={ap_count}"
        else:
            return f"isAllApGroups=False, apGroups={ap_count}"

    for i, target in enumerate(targets, 1):
        net_id = target['id']
        ssid = target['ssid']

        # ── Source 1: From the bulk query we already fetched (no API call) ──
        q_net = bulk_by_id.get(net_id, {})
        q_vags = q_net.get('venueApGroups', [])
        q_venue_vag = None
        for vag in q_vags:
            if vag.get('venueId') == venue_id:
                q_venue_vag = vag
                break

        # ── Source 2: GET /wifiNetworks/{id} (individual — actual API call) ──
        if client.ec_type == "MSP":
            g_resp = client.get(f"/wifiNetworks/{net_id}", override_tenant_id=tenant_id)
        else:
            g_resp = client.get(f"/wifiNetworks/{net_id}")

        if g_resp.status_code != 200:
            print(f"  [{i}/{len(targets)}] {ssid:<40} GET ERROR {g_resp.status_code}")
            mismatches += 1
            continue

        g_net = g_resp.json()
        g_vags = g_net.get('venueApGroups', [])
        g_venue_vag = None
        for vag in g_vags:
            if vag.get('venueId') == venue_id:
                g_venue_vag = vag
                break

        # ── Comparison ──
        q_is_all = q_venue_vag.get('isAllApGroups', None) if q_venue_vag else None
        g_is_all = g_venue_vag.get('isAllApGroups', None) if g_venue_vag else None
        q_on_venue = q_venue_vag is not None
        g_on_venue = g_venue_vag is not None

        is_match = (q_on_venue == g_on_venue and q_is_all == g_is_all)

        if is_match:
            matches += 1
            print(f"  [{i}/{len(targets)}] {ssid:<40} MATCH  QUERY: {_vag_summary(q_venue_vag)}")
        else:
            mismatches += 1
            print(f"  [{i}/{len(targets)}] {ssid:<40} ** MISMATCH **")
            print(f"       QUERY: {_vag_summary(q_venue_vag)}")
            print(f"       GET:   {_vag_summary(g_venue_vag)}")
            print(f"       ┌── QUERY venueApGroups for this venue ──")
            if q_venue_vag:
                # Show compact version: omit apGroupIds list, just show count
                q_compact = {k: v for k, v in q_venue_vag.items() if k != 'apGroupIds'}
                q_compact['apGroupIds'] = f"[{len(q_venue_vag.get('apGroupIds', []))} entries]"
                print(f"       │ {json.dumps(q_compact)}")
            else:
                print(f"       │ null")
            print(f"       ├── GET venueApGroups (full response) ──")
            print(f"       │ venueApGroups count: {len(g_vags)}")
            if g_vags:
                for gv in g_vags:
                    gv_compact = {k: v for k, v in gv.items() if k != 'apGroupIds'}
                    gv_compact['apGroupIds'] = f"[{len(gv.get('apGroupIds', []))} entries]"
                    print(f"       │   {json.dumps(gv_compact)}")
            else:
                print(f"       │   [] (empty)")
            print(f"       └──")

    # ── Summary ──
    print(f"\n{'=' * 70}")
    print(f"  SUMMARY")
    print(f"{'=' * 70}")
    print(f"  Networks tested: {len(targets)}")
    print(f"  Matches:         {matches}")
    print(f"  Mismatches:      {mismatches}")
    if mismatches > 0:
        print(f"\n  *** {mismatches} DISCREPANCIES FOUND ***")
        print(f"  The GET endpoint returns different venueApGroups data than the")
        print(f"  QUERY endpoint for the same network ID. This is an R1 API bug.")
        print(f"  The QUERY endpoint (POST /wifiNetworks/query) returns accurate data.")
        print(f"  The GET endpoint (GET /wifiNetworks/{{id}}) returns stale/missing data.")
    else:
        print(f"\n  All endpoints agree. No discrepancy detected at this moment.")
        print(f"  (The bug may be intermittent or timing-dependent.)")
    print(f"{'=' * 70}")


# =============================================================================
# Test J: POST /networkActivations — single-step direct activation
# =============================================================================

async def r1_post(client, tenant_id, url, payload=None, label=""):
    """Execute a POST and handle response. Returns (success, result)."""
    print(f"\n  [{label}] POST {url}")
    if payload:
        print(f"  Payload: {json.dumps(payload, indent=4)}")

    try:
        if client.ec_type == "MSP":
            response = client.post(url, payload=payload, override_tenant_id=tenant_id)
        else:
            response = client.post(url, payload=payload)

        print(f"  Status: {response.status_code}")
        result = response.json() if response.content else {}
        if result:
            print(f"  Response: {json.dumps(result, indent=4)}")

        if response.status_code >= 400:
            print(f"  FAILED")
            return False, result

        # Wait for async task
        request_id = result.get('requestId')
        if response.status_code == 202 and request_id:
            print(f"  Waiting for task {request_id}...")
            try:
                await client.await_task_completion(request_id, override_tenant_id=tenant_id)
                print(f"  Task complete!")
            except Exception as e:
                print(f"  Task FAILED: {e}")
                return False, {"error": str(e)}

        return True, result

    except Exception as e:
        print(f"  ERROR: {e}")
        return False, {"error": str(e)}


async def test_network_activations(client, tenant_id):
    """
    Test the deprecated POST /networkActivations endpoint.

    This endpoint may allow single-step activation directly to a specific
    AP group, bypassing the current 3-step process:
      Current: activate venue-wide → move to AP group → configure settings
      New:     POST /networkActivations with apGroups=[{apGroupId: ...}]

    If this works, it eliminates:
      - The "All AP Groups" intermediate state
      - The 15-SSID venue-wide slot pressure
      - The entire orphan cascade problem
    """
    print("\n" + "=" * 70)
    print("TEST J: POST /networkActivations (single-step direct activation)")
    print("=" * 70)
    print()
    print("  This tests a deprecated but still-active endpoint that may allow")
    print("  activating an SSID directly to a specific AP group in one call.")
    print()

    # Step 1: Pick venue
    print("  Fetching venues...")
    if client.ec_type == "MSP":
        venues_resp = client.get("/venues", override_tenant_id=tenant_id)
    else:
        venues_resp = client.get("/venues")

    venues_json = venues_resp.json() if venues_resp.status_code == 200 else []
    venues = venues_json.get('data', venues_json) if isinstance(venues_json, dict) else venues_json

    venue = pick(
        venues,
        lambda v: f"{v.get('name', 'unnamed')} ({v.get('id', '?')})",
        "Pick venue"
    )
    if not venue:
        return
    venue_id = venue['id']
    venue_name = venue.get('name', 'unnamed')

    # Step 2: Fetch all networks via query (to get venueApGroups + vlan)
    print(f"\n  Fetching WiFi networks...")
    query_body = {
        "fields": ["name", "ssid", "id", "venueApGroups", "vlan", "nwSubType"],
        "sortField": "name",
        "sortOrder": "ASC",
        "page": 1,
        "pageSize": 500,
    }
    if client.ec_type == "MSP":
        q_resp = client.post("/wifiNetworks/query", payload=query_body, override_tenant_id=tenant_id)
    else:
        q_resp = client.post("/wifiNetworks/query", payload=query_body)

    if q_resp.status_code != 200:
        print(f"  ERROR: {q_resp.status_code}")
        return

    all_nets = q_resp.json().get('data', [])

    # Find networks on this venue
    venue_nets = []
    for net in all_nets:
        for vag in net.get('venueApGroups', []):
            if vag.get('venueId') == venue_id:
                venue_nets.append({
                    'id': net['id'],
                    'name': net.get('name', '?'),
                    'ssid': net.get('ssid', net.get('name', '?')),
                    'vlan': net.get('vlan', 1),
                    'is_all': vag.get('isAllApGroups', False),
                    'ap_group_ids': vag.get('apGroupIds', []),
                })
                break

    # Also find networks NOT on this venue (candidates for fresh activation)
    venue_net_ids = {n['id'] for n in venue_nets}
    unactivated = [n for n in all_nets if n['id'] not in venue_net_ids]

    venue_wide = [n for n in venue_nets if n['is_all']]
    specific = [n for n in venue_nets if not n['is_all']]

    print(f"  Venue '{venue_name}': {len(venue_wide)} venue-wide, {len(specific)} specific, {len(unactivated)} not on venue")

    # Step 3: Pick network
    print(f"\n  Which network to test?")
    print(f"    [1] Pick a venue-wide SSID (already activated, move to specific)")
    print(f"    [2] Pick an unactivated SSID (fresh activation to specific)")
    net_mode = input("\n  Choice [1-2]: ").strip()

    if net_mode == "1":
        if not venue_wide:
            print("  No venue-wide SSIDs found")
            return
        network = pick(
            venue_wide,
            lambda n: f"[VW] {n['ssid']:<40} vlan={n['vlan']}  ({n['id'][:12]}...)",
            "Pick venue-wide network"
        )
    elif net_mode == "2":
        if not unactivated:
            print("  All networks are already on this venue")
            return
        network = pick(
            [{'id': n['id'], 'name': n.get('name', '?'), 'ssid': n.get('ssid', '?'),
              'vlan': n.get('vlan', 1), 'is_all': False, 'ap_group_ids': []}
             for n in unactivated],
            lambda n: f"{n['ssid']:<40} vlan={n['vlan']}  ({n['id'][:12]}...)",
            "Pick unactivated network"
        )
    else:
        print("  Invalid choice")
        return

    if not network:
        return

    # Step 4: Pick target AP group
    print(f"\n  Fetching AP groups for venue...")
    ap_groups_body = {
        "fields": ["id", "name", "venueId", "description"],
        "sortField": "name",
        "sortOrder": "ASC",
        "pageSize": 500,
        "filters": {"venueId": [venue_id]},
    }
    if client.ec_type == "MSP":
        ag_resp = client.post("/venues/apGroups/query", payload=ap_groups_body, override_tenant_id=tenant_id)
    else:
        ag_resp = client.post("/venues/apGroups/query", payload=ap_groups_body)

    if ag_resp.status_code != 200:
        print(f"  ERROR fetching AP groups: {ag_resp.status_code}")
        return

    ap_groups = [g for g in ag_resp.json().get('data', []) if g.get('name')]

    ap_group = pick(
        ap_groups,
        lambda g: f"{g.get('name', '?'):<40} ({g['id'][:12]}...)",
        "Pick target AP group"
    )
    if not ap_group:
        return

    # Step 5: Build payload
    network_id = network['id']
    network_vlan = network['vlan']
    ap_group_id = ap_group['id']
    ap_group_name = ap_group.get('name', '')

    print(f"\n  {'=' * 60}")
    print(f"  Test J: POST /networkActivations")
    print(f"  {'=' * 60}")
    print(f"  Network:  {network['ssid']} ({network_id})")
    print(f"  VLAN:     {network_vlan}")
    print(f"  Venue:    {venue_name} ({venue_id})")
    print(f"  AP Group: {ap_group_name} ({ap_group_id})")
    print(f"  {'=' * 60}")
    print(f"\n  This will POST /networkActivations with isAllApGroups=false")
    print(f"  and the specific AP group. If it works, no 3-step needed!")

    confirm = input(f"\n  Execute? [y/N]: ").strip().lower()
    if confirm != 'y':
        print("  Aborted.")
        return

    payload = {
        "venueId": venue_id,
        "networkId": network_id,
        "isAllApGroups": False,
        "apGroups": [
            {
                "apGroupId": ap_group_id,
                "apGroupName": ap_group_name,
                "radio": "Both",
                "radioTypes": ["2.4-GHz", "5-GHz"],
                "vlanId": network_vlan,
            }
        ],
        "scheduler": {"type": "ALWAYS_ON"},
    }

    ok, result = await r1_post(
        client, tenant_id,
        "/networkActivations",
        payload=payload,
        label="Single-step activation",
    )

    if not ok:
        print(f"\n  FAILED. The endpoint may not accept this format.")
        print(f"  Check the response above for details.")
        return

    # Step 6: Verify — query the network to see where it ended up
    print(f"\n  Verifying activation via POST /wifiNetworks/query...")
    await asyncio.sleep(3)

    verify_body = {
        "filters": {"id": [network_id]},
        "fields": ["name", "ssid", "id", "venueApGroups"],
        "page": 1,
        "pageSize": 1,
    }
    if client.ec_type == "MSP":
        v_resp = client.post("/wifiNetworks/query", payload=verify_body, override_tenant_id=tenant_id)
    else:
        v_resp = client.post("/wifiNetworks/query", payload=verify_body)

    if v_resp.status_code == 200:
        v_data = v_resp.json().get('data', [])
        if v_data:
            v_net = v_data[0]
            v_vags = v_net.get('venueApGroups', [])
            print(f"\n  Post-activation venueApGroups:")
            for vag in v_vags:
                if vag.get('venueId') == venue_id:
                    is_all = vag.get('isAllApGroups', False)
                    ap_ids = vag.get('apGroupIds', [])
                    print(f"    isAllApGroups: {is_all}")
                    print(f"    apGroupIds: {len(ap_ids)} groups")
                    if ap_group_id in ap_ids:
                        print(f"    Target AP group {ap_group_id}: FOUND ✓")
                    else:
                        print(f"    Target AP group {ap_group_id}: NOT FOUND")
                    if is_all:
                        print(f"\n  Result: SSID is on All AP Groups (venue-wide)")
                        print(f"  The endpoint may have ignored isAllApGroups=false")
                    elif ap_group_id in ap_ids and len(ap_ids) == 1:
                        print(f"\n  *** SUCCESS: SSID activated directly on specific AP group! ***")
                        print(f"  No 3-step process needed!")
                    else:
                        print(f"\n  Result: SSID on {len(ap_ids)} AP groups")
                    break
            else:
                print(f"    Not found on venue {venue_id}")
    else:
        print(f"  Verify failed: {v_resp.status_code}")

    print(f"\n{'=' * 70}")


# =============================================================================
# Test: Activities query with time-based filtering
# =============================================================================

async def test_activities_query(client, tenant_id):
    """
    Explore the POST /activities/query endpoint with time-based filters.

    Round 1 taught us:
    - sortField "createdAt" causes bad SQL (column doesn't exist)
    - String filters need to be arrays: ["WIFI"] not "WIFI"
    - R1 SQL columns: start_datetime, updated_datetime, end_datetime, status, product, use_case
    """

    print("\n" + "=" * 70)
    print("TEST C: Activities Query — Time-Based Filtering (Round 2)")
    print("=" * 70)

    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    five_min_ago = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    one_hour_ago = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # =========================================================================
    # Test 1: Minimal — just page/pageSize, no sort, no filters
    # =========================================================================
    print("\n--- Test 1: Minimal (page + pageSize only) ---")
    await _run_activities_query(client, tenant_id, {
        "page": 1,
        "pageSize": 5,
    })

    # =========================================================================
    # Test 2: sortField = "startDatetime" (camelCase, matches API response field)
    # =========================================================================
    print("\n--- Test 2: sortField=startDatetime ---")
    await _run_activities_query(client, tenant_id, {
        "page": 1,
        "pageSize": 5,
        "sortField": "startDatetime",
        "sortOrder": "DESC",
    })

    # =========================================================================
    # Test 3: sortField = "start_datetime" (snake_case, matches SQL column)
    # =========================================================================
    print("\n--- Test 3: sortField=start_datetime ---")
    await _run_activities_query(client, tenant_id, {
        "page": 1,
        "pageSize": 5,
        "sortField": "start_datetime",
        "sortOrder": "DESC",
    })

    # =========================================================================
    # Test 4: sortField = "updatedDatetime"
    # =========================================================================
    print("\n--- Test 4: sortField=updatedDatetime ---")
    await _run_activities_query(client, tenant_id, {
        "page": 1,
        "pageSize": 5,
        "sortField": "updatedDatetime",
        "sortOrder": "DESC",
    })

    # =========================================================================
    # Test 5: Status as ARRAY (R1 expects List not String)
    # =========================================================================
    print("\n--- Test 5: status=[\"INPROGRESS\"] (array) ---")
    await _run_activities_query(client, tenant_id, {
        "filters": {
            "status": ["INPROGRESS"],
        },
        "page": 1,
        "pageSize": 20,
    })

    # =========================================================================
    # Test 6: Status as array with working sort
    # (uses whichever sort worked from tests 2-4)
    # =========================================================================
    print("\n--- Test 6: status=[\"SUCCESS\",\"FAIL\"] (multiple statuses) ---")
    await _run_activities_query(client, tenant_id, {
        "filters": {
            "status": ["SUCCESS", "FAIL"],
        },
        "page": 1,
        "pageSize": 5,
        "sortField": "startDatetime",
        "sortOrder": "DESC",
    })

    # =========================================================================
    # Test 7: product as ARRAY
    # =========================================================================
    print("\n--- Test 7: product=[\"WIFI\"] (array) ---")
    await _run_activities_query(client, tenant_id, {
        "filters": {
            "product": ["WIFI"],
        },
        "page": 1,
        "pageSize": 5,
        "sortField": "startDatetime",
        "sortOrder": "DESC",
    })

    # =========================================================================
    # Test 8: startDatetime as array (time filter)
    # =========================================================================
    print(f"\n--- Test 8: startDatetime=[\"{one_hour_ago}\"] (array) ---")
    await _run_activities_query(client, tenant_id, {
        "filters": {
            "startDatetime": [one_hour_ago],
        },
        "page": 1,
        "pageSize": 20,
        "sortField": "startDatetime",
        "sortOrder": "DESC",
    })

    # =========================================================================
    # Test 9: Combined — product + status (both arrays)
    # =========================================================================
    print("\n--- Test 9: product=[\"WIFI\"] + status=[\"INPROGRESS\",\"SUCCESS\"] ---")
    await _run_activities_query(client, tenant_id, {
        "filters": {
            "product": ["WIFI"],
            "status": ["INPROGRESS", "SUCCESS"],
        },
        "page": 1,
        "pageSize": 10,
        "sortField": "startDatetime",
        "sortOrder": "DESC",
    })

    # =========================================================================
    # Test 10: useCase filter (array)
    # =========================================================================
    print("\n--- Test 10: useCase=[\"UpdateVenueWifiNetworkSettings\"] ---")
    await _run_activities_query(client, tenant_id, {
        "filters": {
            "useCase": ["UpdateVenueWifiNetworkSettings"],
        },
        "page": 1,
        "pageSize": 5,
        "sortField": "startDatetime",
        "sortOrder": "DESC",
    })

    print("\n" + "=" * 70)
    print("Activities query exploration complete!")
    print("=" * 70)


# =============================================================================
# Test: Venue SSID Landscape — What the SSID gate sees
# =============================================================================

async def test_venue_ssid_landscape(client, tenant_id):
    """
    Show the venue SSID activation landscape.

    This is exactly what the SSID gate's reconciliation loop queries:
    - POST /wifiNetworks/query with venueApGroups
    - Count SSIDs on "All AP Groups" vs specific AP groups per venue

    Use this after a workflow run to verify gating worked correctly.
    """

    print("\n" + "=" * 70)
    print("TEST D: Venue SSID Landscape (what the SSID gate sees)")
    print("=" * 70)

    # Pick venue first
    print("\nFetching venues...")
    if client.ec_type == "MSP":
        venues_resp = client.get("/venues", override_tenant_id=tenant_id)
    else:
        venues_resp = client.get("/venues")

    venues_json = venues_resp.json() if venues_resp.status_code == 200 else []
    venues = venues_json.get('data', venues_json) if isinstance(venues_json, dict) else venues_json

    if not venues:
        print("No venues found")
        return

    print(f"\nFound {len(venues)} venues:")
    venue = pick(
        venues,
        lambda v: f"{v.get('name', 'unnamed')} ({v.get('id', '?')})",
        "Pick venue"
    )
    if not venue:
        return
    venue_id = venue['id']
    venue_name = venue.get('name', 'unnamed')

    # Fetch all networks with venueApGroups
    print(f"\nFetching all WiFi networks...")
    query_body = {
        "fields": ["name", "ssid", "id", "venueApGroups", "nwSubType"],
        "sortField": "name",
        "sortOrder": "ASC",
        "page": 1,
        "pageSize": 500,
    }
    if client.ec_type == "MSP":
        nets_resp = client.post("/wifiNetworks/query", payload=query_body, override_tenant_id=tenant_id)
    else:
        nets_resp = client.post("/wifiNetworks/query", payload=query_body)

    nets_json = nets_resp.json() if nets_resp.status_code == 200 else {}
    all_nets = nets_json.get('data', []) if isinstance(nets_json, dict) else nets_json
    total_count = nets_json.get('totalCount', len(all_nets))

    # Handle pagination
    if total_count > len(all_nets):
        page_size = len(all_nets) or 500
        pages_needed = (total_count + page_size - 1) // page_size
        for page_num in range(2, pages_needed + 1):
            query_body['page'] = page_num
            if client.ec_type == "MSP":
                page_resp = client.post("/wifiNetworks/query", payload=query_body, override_tenant_id=tenant_id)
            else:
                page_resp = client.post("/wifiNetworks/query", payload=query_body)
            page_json = page_resp.json() if page_resp.status_code == 200 else {}
            all_nets.extend(page_json.get('data', []))

    print(f"  Total networks: {len(all_nets)} (totalCount={total_count})")

    # Categorize for this venue
    venue_wide = []      # isAllApGroups=true
    specific_group = []  # isAllApGroups=false (on specific AP groups)
    not_on_venue = []    # Not activated on this venue

    for net in all_nets:
        ssid = net.get('ssid', net.get('name', '?'))
        net_id = net.get('id', '?')
        nw_type = net.get('nwSubType', '?')
        venue_ap_groups = net.get('venueApGroups', [])

        found_venue = False
        for vag in venue_ap_groups:
            if vag.get('venueId') != venue_id:
                continue
            found_venue = True
            is_all = vag.get('isAllApGroups', False)
            ap_groups = vag.get('apGroups', [])

            entry = {
                'ssid': ssid,
                'id': net_id,
                'type': nw_type,
                'is_all': is_all,
                'ap_groups': ap_groups,
                'ap_group_names': [
                    g.get('apGroupName', g.get('apGroupId', '?'))
                    for g in ap_groups
                ],
            }

            if is_all:
                venue_wide.append(entry)
            else:
                specific_group.append(entry)
            break

        if not found_venue:
            not_on_venue.append({'ssid': ssid, 'id': net_id, 'type': nw_type})

    # Print results
    LIMIT = 15
    print(f"\n{'=' * 70}")
    print(f"  Venue: {venue_name} ({venue_id})")
    print(f"  SSID Limit per AP Group: {LIMIT}")
    print(f"{'=' * 70}")

    print(f"\n  ON 'ALL AP GROUPS' (venue-wide): {len(venue_wide)}/{LIMIT}")
    print(f"  {'─' * 60}")
    if venue_wide:
        for i, entry in enumerate(venue_wide, 1):
            print(f"    {i:3}. {entry['ssid']:<40} [{entry['type']}] {entry['id'][:12]}...")
    else:
        print(f"    (none)")

    print(f"\n  ON SPECIFIC AP GROUPS: {len(specific_group)}")
    print(f"  {'─' * 60}")
    if specific_group:
        for i, entry in enumerate(specific_group, 1):
            groups_str = ", ".join(entry['ap_group_names'][:3])
            if len(entry['ap_group_names']) > 3:
                groups_str += f" +{len(entry['ap_group_names']) - 3} more"
            print(
                f"    {i:3}. {entry['ssid']:<40} [{entry['type']}] "
                f"→ {groups_str}"
            )
    else:
        print(f"    (none)")

    print(f"\n  NOT ACTIVATED on venue: {len(not_on_venue)}")
    print(f"  {'─' * 60}")
    if not_on_venue:
        for i, entry in enumerate(not_on_venue[:20], 1):
            print(f"    {i:3}. {entry['ssid']:<40} [{entry['type']}]")
        if len(not_on_venue) > 20:
            print(f"    ... and {len(not_on_venue) - 20} more")
    else:
        print(f"    (none)")

    # Gate calculation
    headroom = LIMIT - len(venue_wide) - 1  # SSID_SAFETY_BUFFER = 1
    print(f"\n  {'=' * 60}")
    print(f"  SSID GATE CALCULATION:")
    print(f"    venue_wide_count     = {len(venue_wide)}")
    print(f"    SSID_LIMIT           = {LIMIT}")
    print(f"    SSID_SAFETY_BUFFER   = 1")
    print(f"    → venue_wide_limit   = max(1, {LIMIT} - {len(venue_wide)} - 1) = {max(1, headroom)}")
    print(f"    → concurrent slots   = {max(1, headroom)}")
    print(f"  {'=' * 60}")

    print(f"\n  Summary:")
    print(f"    {len(venue_wide)} venue-wide + {len(specific_group)} specific + {len(not_on_venue)} inactive = {len(all_nets)} total")


# =============================================================================
# Test: Activities with fromTime/toTime (what ActivityTracker uses)
# =============================================================================

async def test_activities_time_window(client, tenant_id):
    """
    Test the exact query pattern used by ActivityTracker.

    ActivityTracker uses:
    - filters.fromTime / filters.toTime (ISO timestamps)
    - sortField: "startDatetime"
    - sortOrder: "DESC"
    - fields: specific list

    This verifies the bulk polling pattern works correctly.
    """
    from datetime import datetime, timedelta, timezone

    print("\n" + "=" * 70)
    print("TEST E: Activities — fromTime/toTime Window (ActivityTracker pattern)")
    print("=" * 70)

    now = datetime.now(timezone.utc)

    # Test 1: Exact ActivityTracker payload (last 5 minutes)
    from_time = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    to_time = (now + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"\n--- Test E1: ActivityTracker pattern (last 5 min) ---")
    await _run_activities_query(client, tenant_id, {
        "fields": [
            "startDatetime", "endDatetime", "status", "product",
            "admin", "descriptionTemplate", "descriptionData", "severity",
        ],
        "page": 1,
        "pageSize": 500,
        "sortField": "startDatetime",
        "sortOrder": "DESC",
        "filters": {
            "fromTime": from_time,
            "toTime": to_time,
        },
    })

    # Test 2: Larger window (last hour)
    from_time_1h = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"\n--- Test E2: Last 1 hour window ---")
    await _run_activities_query(client, tenant_id, {
        "fields": [
            "startDatetime", "endDatetime", "status", "product",
        ],
        "page": 1,
        "pageSize": 500,
        "sortField": "startDatetime",
        "sortOrder": "DESC",
        "filters": {
            "fromTime": from_time_1h,
            "toTime": to_time,
        },
    })

    # Test 3: Combined — fromTime + product filter
    print(f"\n--- Test E3: fromTime + product=[\"WIFI\"] ---")
    await _run_activities_query(client, tenant_id, {
        "fields": [
            "startDatetime", "endDatetime", "status", "product",
            "descriptionTemplate", "descriptionData",
        ],
        "page": 1,
        "pageSize": 20,
        "sortField": "startDatetime",
        "sortOrder": "DESC",
        "filters": {
            "fromTime": from_time_1h,
            "toTime": to_time,
            "product": ["WIFI"],
        },
    })

    # Test 4: Combined — fromTime + status filter
    print(f"\n--- Test E4: fromTime + status=[\"INPROGRESS\"] ---")
    await _run_activities_query(client, tenant_id, {
        "fields": [
            "startDatetime", "endDatetime", "status", "product",
            "descriptionTemplate",
        ],
        "page": 1,
        "pageSize": 20,
        "sortField": "startDatetime",
        "sortOrder": "DESC",
        "filters": {
            "fromTime": from_time_1h,
            "toTime": to_time,
            "status": ["INPROGRESS"],
        },
    })

    # Test 5: Large window (last 24h) to test pagination
    from_time_24h = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"\n--- Test E5: Last 24h (pagination check) ---")
    await _run_activities_query(client, tenant_id, {
        "fields": ["startDatetime", "status", "product"],
        "page": 1,
        "pageSize": 500,
        "sortField": "startDatetime",
        "sortOrder": "DESC",
        "filters": {
            "fromTime": from_time_24h,
            "toTime": to_time,
        },
    })

    print(f"\n{'=' * 70}")
    print("fromTime/toTime exploration complete!")
    print("=" * 70)


# =============================================================================
# Test: Activity ID filter (what query_activities_bulk uses)
# =============================================================================

async def test_activities_by_id(client, tenant_id):
    """
    Test filtering activities by ID — the pattern used by R1Client.query_activities_bulk.

    First fetches recent activities to get real IDs, then queries by ID filter.
    """
    print("\n" + "=" * 70)
    print("TEST F: Activities — Filter by ID (query_activities_bulk pattern)")
    print("=" * 70)

    # Step 1: Fetch a few recent activities to get real IDs
    print("\n--- Step 1: Fetching recent activities to get IDs ---")
    if client.ec_type == "MSP":
        response = client.post("/activities/query", payload={
            "page": 1, "pageSize": 5,
            "sortField": "startDatetime", "sortOrder": "DESC",
        }, override_tenant_id=tenant_id)
    else:
        response = client.post("/activities/query", payload={
            "page": 1, "pageSize": 5,
            "sortField": "startDatetime", "sortOrder": "DESC",
        })

    if response.status_code >= 400:
        print(f"  Failed to fetch activities: {response.status_code}")
        return

    data = response.json()
    activities = data.get('data', [])
    if not activities:
        print("  No activities found to test with")
        return

    # Get IDs and requestIds
    sample_ids = [a['id'] for a in activities[:3] if 'id' in a]
    sample_request_ids = [a['requestId'] for a in activities[:3] if 'requestId' in a]

    print(f"  Got {len(sample_ids)} activity IDs, {len(sample_request_ids)} requestIds")
    for a in activities[:3]:
        print(f"    id={a.get('id', '?')[:20]}... requestId={a.get('requestId', '?')[:20]}... status={a.get('status')}")

    # Test F1: Filter by id (array)
    if sample_ids:
        print(f"\n--- Test F1: filters.id = [{len(sample_ids)} IDs] ---")
        await _run_activities_query(client, tenant_id, {
            "filters": {"id": sample_ids},
            "pageSize": 10,
            "page": 1,
            "sortField": "startDatetime",
            "sortOrder": "DESC",
        })

    # Test F2: Filter by requestId (array) — may or may not be supported
    if sample_request_ids:
        print(f"\n--- Test F2: filters.requestId = [{len(sample_request_ids)} requestIds] ---")
        await _run_activities_query(client, tenant_id, {
            "filters": {"requestId": sample_request_ids},
            "pageSize": 10,
            "page": 1,
            "sortField": "startDatetime",
            "sortOrder": "DESC",
        })

    print(f"\n{'=' * 70}")
    print("Activity ID filter exploration complete!")
    print("=" * 70)


async def _run_activities_query(client, tenant_id, payload):
    """Execute a POST /activities/query and print results."""
    print(f"  POST /activities/query")
    print(f"  Payload: {json.dumps(payload, indent=4)}")

    try:
        if client.ec_type == "MSP":
            response = client.post("/activities/query", payload=payload, override_tenant_id=tenant_id)
        else:
            response = client.post("/activities/query", payload=payload)

        print(f"  Status: {response.status_code}")

        if response.status_code >= 400:
            print(f"  Error: {response.text[:500]}")
            return

        data = response.json()
        total = data.get('totalCount', '?')
        activities = data.get('data', [])
        print(f"  totalCount: {total}, returned: {len(activities)}")

        # Show summary of each activity
        for i, act in enumerate(activities[:10]):
            act_id = act.get('id', '?')[:12]
            status = act.get('status', '?')
            use_case = act.get('useCase', '?')
            start = act.get('startDatetime', '?')
            product = act.get('product', '?')
            print(f"    [{i+1}] {act_id}... status={status} useCase={use_case} product={product} start={start}")

        if len(activities) > 10:
            print(f"    ... and {len(activities) - 10} more")

        # Print available fields from first activity (for discovery)
        if activities:
            print(f"\n  Available fields in activity response:")
            for key in sorted(activities[0].keys()):
                val = activities[0][key]
                if isinstance(val, str) and len(val) > 80:
                    val = val[:80] + "..."
                elif isinstance(val, (list, dict)):
                    val = f"{type(val).__name__}({len(val)} items)"
                print(f"    - {key}: {val}")

    except Exception as e:
        print(f"  ERROR: {e}")


# =============================================================================
# Main
# =============================================================================

async def run_interactive():
    """Interactive mode: pick resources, then run test."""
    db = get_db_session()
    controllers = list_controllers(db)
    db.close()

    if not controllers:
        print("No RuckusONE controllers found in DB")
        return

    print("RuckusONE Controllers:")
    controller = pick(
        controllers,
        lambda c: f"[{c.id}] {c.name} ({c.controller_subtype or 'EC'}) - {c.r1_tenant_id}",
        "Pick controller"
    )
    if not controller:
        return

    client, tenant_id = get_r1_client(controller.id)
    print(f"\nConnected: {controller.name} (tenant: {tenant_id})")

    # Choose test mode
    print("\nWhat do you want to test?")
    print("  [1] Test A: Pre-bind AP group (direct activation)")
    print("  [2] Test B: Deactivate + reactivate")
    print("  [3] Test C: Activities query (filter exploration)")
    print("  [4] Test D: Venue SSID Landscape (what the SSID gate sees)")
    print("  [5] Test E: Activities fromTime/toTime (ActivityTracker pattern)")
    print("  [6] Test F: Activities by ID filter (query_activities_bulk pattern)")
    print("  [7] Test G: Reordered 3-step (settings before bind)")
    print("  [8] Test H: Pending activities audit (stale INPROGRESS)")
    print("  [9] Test I: GET vs QUERY discrepancy (R1 API bug proof)")
    print(" [10] Test J: POST /networkActivations (single-step activation!)")
    test_choice = input("\nChoice [1-10]: ").strip()

    if test_choice == "3":
        await test_activities_query(client, tenant_id)
        return
    elif test_choice == "4":
        await test_venue_ssid_landscape(client, tenant_id)
        return
    elif test_choice == "5":
        await test_activities_time_window(client, tenant_id)
        return
    elif test_choice == "6":
        await test_activities_by_id(client, tenant_id)
        return
    elif test_choice == "8":
        await test_pending_activities_audit(client, tenant_id)
        return
    elif test_choice == "9":
        await test_get_vs_query(client, tenant_id)
        return
    elif test_choice == "10":
        await test_network_activations(client, tenant_id)
        return

    result = await interactive_pick_resources(client, tenant_id)
    if result[0] is None:
        return

    picked_tenant_id, venue_id, ap_group_id, network_id, test_mode = result

    print(f"\n  tenant_id:   {picked_tenant_id}")
    print(f"  venue_id:    {venue_id}")
    print(f"  ap_group_id: {ap_group_id}")
    print(f"  network_id:  {network_id}")
    print(f"  test_mode:   {test_mode}")

    confirm = input(f"\nReady to execute? [y/N]: ").strip().lower()
    if confirm != 'y':
        print("Aborted.")
        return

    if test_choice == "7":
        result = await test_reordered_3step(
            client, picked_tenant_id, venue_id, network_id, ap_group_id
        )
    elif test_mode == "deactivate_reactivate":
        result = await test_deactivate_reactivate(
            client, picked_tenant_id, venue_id, network_id, ap_group_id
        )
    else:
        result = await test_direct_bind(
            client, picked_tenant_id, venue_id, network_id, ap_group_id
        )

    if result is True:
        print("\n*** HYPOTHESIS CONFIRMED ***")
    elif result is False:
        print("\n*** HYPOTHESIS REJECTED ***")


def main():
    parser = argparse.ArgumentParser(description="Test R1 API behaviors")
    parser.add_argument("--controller-id", type=int, help="DB controller ID")
    parser.add_argument("--venue-id", help="R1 venue ID")
    parser.add_argument("--network-id", help="WiFi network ID")
    parser.add_argument("--ap-group-id", help="AP group ID")
    parser.add_argument("--execute", action="store_true", help="Run the calls")
    parser.add_argument("--test", choices=[
                            "direct", "deactivate", "activities",
                            "landscape", "activities-time", "activities-id",
                            "reordered", "pending-audit", "get-vs-query",
                        ],
                        default="direct", help="Test mode")

    args = parser.parse_args()

    # Tests that only need controller (no venue/network/ap-group)
    if args.controller_id and args.test in ("activities", "activities-time", "activities-id", "landscape", "pending-audit", "get-vs-query"):
        client, tenant_id = get_r1_client(args.controller_id)
        if args.test == "activities":
            asyncio.run(test_activities_query(client, tenant_id))
        elif args.test == "landscape":
            asyncio.run(test_venue_ssid_landscape(client, tenant_id))
        elif args.test == "activities-time":
            asyncio.run(test_activities_time_window(client, tenant_id))
        elif args.test == "activities-id":
            asyncio.run(test_activities_by_id(client, tenant_id))
        elif args.test == "pending-audit":
            asyncio.run(test_pending_activities_audit(client, tenant_id))
        elif args.test == "get-vs-query":
            asyncio.run(test_get_vs_query(client, tenant_id))
        return

    # Interactive mode if no IDs provided
    if not all([args.controller_id, args.venue_id, args.network_id, args.ap_group_id]):
        asyncio.run(run_interactive())
        return

    # CLI mode
    client, tenant_id = get_r1_client(args.controller_id)

    if not args.execute:
        print("DRY RUN — add --execute to fire real API calls")
        print(f"  controller: {args.controller_id}")
        print(f"  venue:      {args.venue_id}")
        print(f"  network:    {args.network_id}")
        print(f"  ap_group:   {args.ap_group_id}")
        print(f"  test:       {args.test}")
        return

    if args.test == "deactivate":
        result = asyncio.run(test_deactivate_reactivate(
            client, tenant_id, args.venue_id, args.network_id, args.ap_group_id
        ))
    elif args.test == "reordered":
        result = asyncio.run(test_reordered_3step(
            client, tenant_id, args.venue_id, args.network_id, args.ap_group_id
        ))
    else:
        result = asyncio.run(test_direct_bind(
            client, tenant_id, args.venue_id, args.network_id, args.ap_group_id
        ))

    if result is True:
        print("\n*** HYPOTHESIS CONFIRMED ***")
    elif result is False:
        print("\n*** HYPOTHESIS REJECTED ***")


if __name__ == "__main__":
    main()
