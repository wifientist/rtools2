"""
Per-Unit SSID Configuration Router

Automates the creation of per-unit SSIDs in RuckusONE by:
1. Creating SSIDs for each unit
2. Creating AP Groups for each unit
3. Assigning APs to their unit's AP Group
4. Assigning SSIDs to their corresponding AP Groups
"""

from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
import logging

from dependencies import get_db, get_current_user
from models.user import User
from models.controller import Controller
from clients.r1_client import create_r1_client_from_controller

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/per-unit-ssid",
)


class UnitConfig(BaseModel):
    """Configuration for a single unit"""
    unit_number: str = Field(..., description="Unit number (e.g., '101', '102')")
    ap_identifiers: List[str] = Field(..., description="List of AP serial numbers or names in this unit")
    ssid_name: str = Field(..., description="SSID name for this unit")
    ssid_password: str = Field(..., description="Unique password for this unit's SSID")
    security_type: str = Field(default="WPA3", description="Security type: WPA2, WPA3, or WPA2/WPA3")
    default_vlan: str = Field(default="1", description="Default VLAN ID for this SSID (e.g., '1', '10', '100')")


class PerUnitSSIDRequest(BaseModel):
    """Request to configure per-unit SSIDs"""
    controller_id: int = Field(..., description="RuckusONE controller ID")
    tenant_id: Optional[str] = Field(None, description="Tenant/EC ID (required for MSP)")
    venue_id: str = Field(..., description="Venue ID where APs are located")
    units: List[UnitConfig] = Field(..., description="List of unit configurations")
    ap_group_prefix: str = Field(default="APGroup-", description="Prefix for AP group names")
    dry_run: bool = Field(default=False, description="If true, validate only without making changes")


class UnitResult(BaseModel):
    """Result for a single unit configuration"""
    unit_number: str
    status: str  # success, error, skipped
    message: str
    details: Optional[Dict[str, Any]] = None


class PerUnitSSIDResponse(BaseModel):
    """Response from per-unit SSID configuration"""
    status: str
    message: str
    total_units: int
    successful_units: int
    failed_units: int
    unit_results: List[UnitResult]


class VenueAuditRequest(BaseModel):
    """Request to audit a venue's network configuration"""
    controller_id: int = Field(..., description="RuckusONE controller ID")
    tenant_id: Optional[str] = Field(None, description="Tenant/EC ID (required for MSP)")
    venue_id: str = Field(..., description="Venue ID to audit")


class VenueAuditResponse(BaseModel):
    """Response from venue audit"""
    venue_id: str
    venue_name: str
    total_ap_groups: int
    total_aps: int
    total_ssids: int
    ap_groups: List[Dict[str, Any]]


def validate_controller_access(controller_id: int, user: User, db: Session) -> Controller:
    """Validate user has access to controller"""
    controller = db.query(Controller).filter(
        Controller.id == controller_id,
        Controller.user_id == user.id
    ).first()

    if not controller:
        controller_exists = db.query(Controller).filter(Controller.id == controller_id).first()
        if not controller_exists:
            raise HTTPException(status_code=404, detail=f"Controller {controller_id} not found")
        else:
            raise HTTPException(status_code=403, detail=f"Access denied to controller {controller_id}")

    return controller


@router.post("/configure", response_model=PerUnitSSIDResponse)
async def configure_per_unit_ssids(
    request: PerUnitSSIDRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Configure per-unit SSIDs in RuckusONE

    This endpoint automates the SmartZone → RuckusONE migration pattern for per-unit SSIDs:
    - In SmartZone: Used WLAN Groups to assign different SSIDs to different APs
    - In RuckusONE: Uses AP Groups + SSID assignments to achieve the same result

    Process:
    1. Create SSID for each unit (if it doesn't exist)
    2. Create AP Group for each unit (if it doesn't exist)
    3. Find and assign APs to their unit's AP Group
    4. Assign SSID to the AP Group

    Args:
        request: Configuration request with units, APs, SSIDs, and passwords

    Returns:
        Results showing success/failure for each unit
    """
    logger.info(f"🏢 Per-unit SSID configuration request - controller: {request.controller_id}, venue: {request.venue_id}, units: {len(request.units)}")

    # Validate controller access
    controller = validate_controller_access(request.controller_id, current_user, db)

    if controller.controller_type != "RuckusONE":
        raise HTTPException(
            status_code=400,
            detail=f"Controller must be RuckusONE, got {controller.controller_type}"
        )

    # Create R1 client
    r1_client = create_r1_client_from_controller(controller.id, db)

    # Determine tenant_id
    tenant_id = request.tenant_id or controller.r1_tenant_id

    if controller.controller_subtype == "MSP" and not tenant_id:
        raise HTTPException(
            status_code=400,
            detail="tenant_id is required for MSP controllers"
        )

    logger.info(f"✅ Controller validated - type: {controller.controller_type}, tenant: {tenant_id}")

    if request.dry_run:
        logger.info("🔍 DRY RUN MODE - No changes will be made")

    # STEP 1: Create all SSIDs in parallel (no dependencies)
    print(f"\n🚀 STEP 1: Creating {len(request.units)} SSIDs...")
    ssid_map = {}  # unit_number -> ssid_id

    for unit in request.units:
        print(f"  📶 [{unit.unit_number}] Checking/creating SSID: {unit.ssid_name}")

        if request.dry_run:
            ssid_map[unit.unit_number] = f"dry-run-{unit.unit_number}"
            continue

        try:
            # Check if SSID exists
            existing_ssid = await r1_client.networks.find_wifi_network_by_name(tenant_id, request.venue_id, unit.ssid_name)

            if existing_ssid:
                print(f"  ✓ [{unit.unit_number}] SSID '{unit.ssid_name}' already exists (ID: {existing_ssid.get('id')})")
                ssid_map[unit.unit_number] = existing_ssid.get('id')
            else:
                # Create SSID
                print(f"  🔨 [{unit.unit_number}] Creating SSID '{unit.ssid_name}'...")
                ssid_result = await r1_client.networks.create_wifi_network(
                    tenant_id=tenant_id,
                    venue_id=request.venue_id,
                    name=unit.ssid_name,
                    ssid=unit.ssid_name,
                    passphrase=unit.ssid_password,
                    security_type=unit.security_type,
                    vlan_id=int(unit.default_vlan),
                    description=f"Per-unit SSID for unit {unit.unit_number}",
                    wait_for_completion=True
                )

                ssid_id = ssid_result.get('id') if ssid_result else None
                if ssid_id:
                    print(f"  ✅ [{unit.unit_number}] Created SSID '{unit.ssid_name}' (ID: {ssid_id})")
                    ssid_map[unit.unit_number] = ssid_id
                else:
                    print(f"  ❌ [{unit.unit_number}] Failed to create SSID '{unit.ssid_name}' - no ID returned")
        except Exception as e:
            print(f"  ❌ [{unit.unit_number}] SSID creation error: {str(e)}")

    # STEP 2: Activate all SSIDs on the Venue (required before AP Group activation)
    print(f"\n🚀 STEP 2: Activating {len(request.units)} SSIDs on venue...")

    for unit in request.units:
        ssid_id = ssid_map.get(unit.unit_number)
        if not ssid_id:
            print(f"  ⚠️  [{unit.unit_number}] Skipping - no SSID ID")
            continue

        if request.dry_run:
            continue

        try:
            print(f"  🔗 [{unit.unit_number}] Activating SSID '{unit.ssid_name}' on venue...")
            await r1_client.venues.activate_ssid_on_venue(
                tenant_id=tenant_id,
                venue_id=request.venue_id,
                wifi_network_id=ssid_id,
                wait_for_completion=True
            )
            print(f"  ✅ [{unit.unit_number}] SSID activated on venue")
        except Exception as e:
            print(f"  ❌ [{unit.unit_number}] Failed to activate SSID on venue: {str(e)}")

    # STEP 3: Create all AP Groups (no dependencies)
    print(f"\n🚀 STEP 3: Creating {len(request.units)} AP Groups...")
    ap_group_map = {}  # unit_number -> ap_group_id

    for unit in request.units:
        ap_group_name = f"{request.ap_group_prefix}{unit.unit_number}"
        print(f"  👥 [{unit.unit_number}] Checking/creating AP Group: {ap_group_name}")

        if request.dry_run:
            ap_group_map[unit.unit_number] = f"dry-run-group-{unit.unit_number}"
            continue

        try:
            # Check if AP Group exists
            existing_group = await r1_client.venues.find_ap_group_by_name(tenant_id, request.venue_id, ap_group_name)

            if existing_group:
                print(f"  ✓ [{unit.unit_number}] AP Group '{ap_group_name}' already exists (ID: {existing_group.get('id')})")
                ap_group_map[unit.unit_number] = existing_group.get('id')
            else:
                # Create AP Group
                print(f"  🔨 [{unit.unit_number}] Creating AP Group '{ap_group_name}'...")
                group_result = await r1_client.venues.create_ap_group(
                    tenant_id=tenant_id,
                    venue_id=request.venue_id,
                    name=ap_group_name,
                    description=f"AP Group for unit {unit.unit_number}",
                    wait_for_completion=True
                )

                ap_group_id = group_result.get('id') if group_result else None
                if ap_group_id:
                    print(f"  ✅ [{unit.unit_number}] Created AP Group '{ap_group_name}' (ID: {ap_group_id})")
                    ap_group_map[unit.unit_number] = ap_group_id
                else:
                    print(f"  ❌ [{unit.unit_number}] Failed to create AP Group '{ap_group_name}' - no ID returned")
        except Exception as e:
            print(f"  ❌ [{unit.unit_number}] AP Group creation error: {str(e)}")

    # STEP 4: Process each unit (AP assignment + SSID filtering to AP Group)
    print(f"\n🚀 STEP 4: Processing {len(request.units)} units (AP assignment + SSID filtering to AP Group)...")
    unit_results = []
    successful_units = 0
    failed_units = 0

    for unit in request.units:
        print(f"\n📦 [{unit.unit_number}] Processing unit...")

        try:
            unit_result = await process_unit_final_steps(
                r1_client=r1_client,
                tenant_id=tenant_id,
                venue_id=request.venue_id,
                unit=unit,
                ssid_id=ssid_map.get(unit.unit_number),
                ap_group_id=ap_group_map.get(unit.unit_number),
                ap_group_prefix=request.ap_group_prefix,
                dry_run=request.dry_run
            )

            unit_results.append(unit_result)

            if unit_result.status == "success":
                successful_units += 1
            else:
                failed_units += 1

        except Exception as e:
            print(f"❌ [{unit.unit_number}] Error processing unit: {str(e)}")
            unit_results.append(UnitResult(
                unit_number=unit.unit_number,
                status="error",
                message=f"Error: {str(e)}"
            ))
            failed_units += 1

    # Build summary
    status = "completed" if failed_units == 0 else "partial" if successful_units > 0 else "failed"
    message = f"Processed {len(request.units)} units: {successful_units} successful, {failed_units} failed"

    if request.dry_run:
        message = f"[DRY RUN] {message} - No changes were made"

    return PerUnitSSIDResponse(
        status=status,
        message=message,
        total_units=len(request.units),
        successful_units=successful_units,
        failed_units=failed_units,
        unit_results=unit_results
    )


@router.post("/audit", response_model=VenueAuditResponse)
async def audit_venue(
    request: VenueAuditRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Audit a venue's network configuration

    Returns a comprehensive view of:
    - All AP Groups in the venue
    - APs assigned to each group
    - SSIDs activated on each group

    This is useful for understanding the current state before making changes.
    """
    logger.info(f"🔍 Venue audit request - controller: {request.controller_id}, venue: {request.venue_id}")

    # Validate controller access
    controller = validate_controller_access(request.controller_id, current_user, db)

    if controller.controller_type != "RuckusONE":
        raise HTTPException(
            status_code=400,
            detail=f"Controller must be RuckusONE, got {controller.controller_type}"
        )

    # Create R1 client
    r1_client = create_r1_client_from_controller(controller.id, db)

    # Determine tenant_id
    tenant_id = request.tenant_id or controller.r1_tenant_id

    if controller.controller_subtype == "MSP" and not tenant_id:
        raise HTTPException(
            status_code=400,
            detail="tenant_id is required for MSP controllers"
        )

    logger.info(f"✅ Controller validated - type: {controller.controller_type}, tenant: {tenant_id}")

    try:
        # Get comprehensive venue network summary
        summary = await r1_client.venues.get_venue_network_summary(tenant_id, request.venue_id)

        print(f"📊 BACKEND: Summary contains {len(summary['ap_groups'])} AP Groups")
        print(f"📊 BACKEND: AP Group names: {[g['ap_group_name'] for g in summary['ap_groups']]}")

        # Build response
        response = VenueAuditResponse(
            venue_id=request.venue_id,
            venue_name=summary['venue'].get('name', 'Unknown'),
            total_ap_groups=summary['total_ap_groups'],
            total_aps=summary['total_aps'],
            total_ssids=summary['total_ssids'],
            ap_groups=summary['ap_groups']
        )

        print(f"📤 BACKEND: Returning response with {len(response.ap_groups)} AP Groups")
        return response

    except Exception as e:
        logger.error(f"❌ Audit failed: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to audit venue: {str(e)}"
        )


async def process_unit_final_steps(
    r1_client,
    tenant_id: str,
    venue_id: str,
    unit: UnitConfig,
    ssid_id: str,
    ap_group_id: str,
    ap_group_prefix: str,
    dry_run: bool
) -> UnitResult:
    """
    Process final steps for a unit after SSID, Venue activation, and AP Group are created

    Steps:
    1. Find APs by identifiers (serial or name)
    2. Assign APs to AP Group
    3a. Activate SSID on AP Group (makes SSID available to the group)
    3b. Configure SSID settings (radio types, VLAN) to filter to specific AP Group

    Args:
        ssid_id: ID of the SSID (already created and activated on venue)
        ap_group_id: ID of the AP Group (already created)
    """
    details = {
        'ssid_id': ssid_id,
        'ap_group_id': ap_group_id
    }
    ap_group_name = f"{ap_group_prefix}{unit.unit_number}"

    try:
        # Validate we have the required IDs
        if not ssid_id:
            raise Exception(f"SSID ID is missing for unit {unit.unit_number}")
        if not ap_group_id:
            raise Exception(f"AP Group ID is missing for unit {unit.unit_number}")

        # Step 1: Find APs (if any were specified)
        matched_aps = []
        if unit.ap_identifiers and len(unit.ap_identifiers) > 0:
            print(f"  📡 [{unit.unit_number}] Fetching APs in venue {venue_id}...")
            aps_response = await r1_client.venues.get_aps_by_tenant_venue(tenant_id, venue_id)
            all_aps = aps_response.get('data', [])
            print(f"  📡 [{unit.unit_number}] Found {len(all_aps)} total APs in venue")

            # Find APs that match our identifiers (by serial or name)
            for ap_identifier in unit.ap_identifiers:
                for ap in all_aps:
                    if (ap.get('serialNumber') == ap_identifier or
                        ap.get('name') == ap_identifier or
                        ap_identifier in ap.get('name', '')):  # Partial name match
                        matched_aps.append(ap)
                        print(f"  ✓ [{unit.unit_number}] Matched AP: {ap.get('name')} ({ap.get('serialNumber')})")
                        break

            if len(matched_aps) != len(unit.ap_identifiers):
                print(f"  ⚠️  [{unit.unit_number}] Only matched {len(matched_aps)}/{len(unit.ap_identifiers)} APs")
        else:
            print(f"  ℹ️  [{unit.unit_number}] No APs specified - skipping AP assignment")

        details['matched_aps'] = len(matched_aps)
        details['ap_names'] = [ap.get('name') for ap in matched_aps]

        if dry_run:
            if len(matched_aps) > 0:
                message = f"[DRY RUN] [{unit.unit_number}] Would configure {len(matched_aps)} APs with SSID '{unit.ssid_name}'"
            else:
                message = f"[DRY RUN] [{unit.unit_number}] Would activate SSID '{unit.ssid_name}' on AP Group '{ap_group_name}' (no APs to assign)"
            return UnitResult(
                unit_number=unit.unit_number,
                status="success",
                message=message,
                details=details
            )

        # Step 2: Assign APs to AP Group (if any APs were found)
        if len(matched_aps) > 0:
            print(f"  🔗 [{unit.unit_number}] Assigning {len(matched_aps)} APs to group {ap_group_name}")
            assigned_count = 0
            for ap in matched_aps:
                try:
                    print(f"    ⏳ [{unit.unit_number}] Assigning {ap.get('serialNumber')}...")
                    await r1_client.venues.assign_ap_to_group(
                        tenant_id=tenant_id,
                        venue_id=venue_id,
                        ap_group_id=ap_group_id,
                        ap_serial_number=ap.get('serialNumber'),
                        wait_for_completion=True
                    )
                    assigned_count += 1
                    print(f"    ✓ [{unit.unit_number}] Assigned {ap.get('serialNumber')} ({assigned_count}/{len(matched_aps)})")
                except Exception as e:
                    print(f"  ⚠️  [{unit.unit_number}] Failed to assign AP {ap.get('serialNumber')}: {str(e)}")
            details['aps_assigned'] = assigned_count
        else:
            print(f"  ⏭️  [{unit.unit_number}] No APs to assign")
            details['aps_assigned'] = 0

        # Step 3: Activate SSID on AP Group with radio types and VLAN settings
        print(f"  📡 [{unit.unit_number}] Activating SSID '{unit.ssid_name}' on AP Group '{ap_group_name}' with VLAN {unit.default_vlan}...")
        try:
            await r1_client.venues.activate_ssid_on_ap_group(
                tenant_id=tenant_id,
                venue_id=venue_id,
                wifi_network_id=ssid_id,
                ap_group_id=ap_group_id,
                radio_types=["2.4-GHz", "5-GHz", "6-GHz"],  # All radios
                vlan_id=unit.default_vlan,
                wait_for_completion=True
            )
            details['ssid_activated_on_group'] = True
            print(f"  ✅ [{unit.unit_number}] SSID activated on AP Group with radio/VLAN settings")
        except Exception as e:
            print(f"  ❌ [{unit.unit_number}] Failed to activate SSID on AP Group: {str(e)}")
            raise Exception(f"SSID activation on AP Group failed: {str(e)}")

        # Build success message
        if len(matched_aps) > 0:
            message = f"✅ [{unit.unit_number}] Configured {len(matched_aps)} APs with SSID '{unit.ssid_name}' (VLAN {unit.default_vlan})"
        else:
            message = f"✅ [{unit.unit_number}] Activated SSID '{unit.ssid_name}' (VLAN {unit.default_vlan}) on AP Group '{ap_group_name}'"

        return UnitResult(
            unit_number=unit.unit_number,
            status="success",
            message=message,
            details=details
        )

    except Exception as e:
        print(f"  ❌ [{unit.unit_number}] Error: {str(e)}")
        return UnitResult(
            unit_number=unit.unit_number,
            status="error",
            message=str(e),
            details=details
        )
