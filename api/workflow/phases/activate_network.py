"""
V2 Phase: Activate Network on AP Group

Activates a WiFi network (SSID) on a specific AP Group using a 2-step process:
1. Activate SSID on venue (isAllApGroups=true) — required by R1
2. Move to specific AP group via 3-step process (settings, apGroups, apGroups/settings)

R1 requires venue-wide activation first before an SSID can be configured
on a specific AP group. Step 1 temporarily broadcasts the SSID venue-wide,
and step 2 immediately moves it to the target AP group only.

Gracefully handles "already activated" responses from R1.

When our internal activity tracking times out, we verify the actual R1 state.
If the SSID is confirmed still on "All AP Groups", we retry the 3-step config
because R1's activity reporting has known bugs where activities stay INPROGRESS
even though the underlying operation failed or was never processed.
"""

import logging
from pydantic import BaseModel

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation

logger = logging.getLogger(__name__)

# Max retries for 3-step config when R1 activity tracking times out
# but the SSID is confirmed still on venue-wide (isAllApGroups=true).
# Retries use direct polling (not activity tracker) to bypass R1's
# buggy activity reporting.
MAX_3STEP_RETRIES = 2


@register_phase("activate_network", "Activate Network on AP Group")
class ActivateNetworkPhase(PhaseExecutor):
    """
    Activate a WiFi network on a specific AP Group.

    2-step process:
    1. Activate on venue (isAllApGroups=true) — registers SSID on venue
    2. Move to specific AP group (3-step: settings, apGroups, apGroups/settings)

    Idempotent - handles already-activated gracefully.
    """

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str
        network_id: str
        ap_group_id: str
        ap_group_name: str = ""
        ssid_name: str = ""
        default_vlan: str = "1"
        already_activated: bool = False
        is_venue_wide: bool = False

    class Outputs(BaseModel):
        activated: bool = True
        already_active: bool = False

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Activate SSID on venue, then move to specific AP Group."""
        display_name = inputs.ssid_name or inputs.network_id

        # Fast path A: SSID is already activated on a specific AP group.
        # Skip entirely to avoid "bouncing" the config.
        if inputs.already_activated and not inputs.is_venue_wide:
            logger.info(
                f"[{inputs.unit_number}] SSID '{display_name}' "
                f"already on specific AP group (skipping)"
            )
            await self.emit(
                f"[{inputs.unit_number}] '{display_name}' already activated "
                f"on AP Group"
            )
            return self.Outputs(activated=True, already_active=True)

        # Use ActivityTracker for bulk polling if available
        use_activity_tracker = self.context.activity_tracker is not None

        # Fast path B: SSID is stuck on "All AP Groups" (venue-wide).
        # Skip Step 1 — it's already activated on the venue.
        # Go straight to Step 2 to move it to the specific AP group.
        # This avoids a wasted API call and frees venue-wide capacity faster.
        if inputs.already_activated and inputs.is_venue_wide:
            logger.info(
                f"[{inputs.unit_number}] SSID '{display_name}' "
                f"stuck on All AP Groups — skipping venue activation, "
                f"going straight to AP group config"
            )
            await self.emit(
                f"[{inputs.unit_number}] '{display_name}' already venue-wide, "
                f"moving to AP Group '{inputs.ap_group_name}'..."
            )
            # Fall through to Step 2 below (skip Step 1)
        else:
            # =================================================================
            # Step 1: Activate SSID on venue (isAllApGroups=true)
            # This is required by R1 before we can configure specific AP groups.
            # =================================================================
            await self.emit(
                f"[{inputs.unit_number}] Activating '{display_name}' on venue..."
            )

            try:
                result = await self.r1_client.venues.activate_ssid_on_venue(
                    tenant_id=self.tenant_id,
                    venue_id=self.venue_id,
                    wifi_network_id=inputs.network_id,
                    wait_for_completion=not use_activity_tracker,
                )

                # If using tracker, wait via centralized polling
                if use_activity_tracker and result:
                    request_id = result.get('requestId') if isinstance(result, dict) else None
                    if request_id:
                        activity_result = await self.fire_and_wait(request_id)
                        if not activity_result.success:
                            # R1 activity may stay INPROGRESS even though the
                            # SSID was actually activated. Verify actual state
                            # before declaring failure.
                            verified = await self._verify_ssid_on_venue(inputs)
                            if not verified:
                                raise RuntimeError(
                                    f"Venue activation failed: {activity_result.error}"
                                )
                            logger.warning(
                                f"[{inputs.unit_number}] Activity "
                                f"{request_id[:8]}... stuck/timed out but SSID "
                                f"IS activated on venue — proceeding "
                                f"(R1 activity reporting issue)"
                            )

                logger.info(
                    f"[{inputs.unit_number}] SSID '{display_name}' activated on venue"
                )

            except Exception as e:
                error_str = str(e).lower()
                # R1 returns an error if SSID is already activated - treat as success
                if (
                    'already activated' in error_str
                    or 'already exists' in error_str
                ):
                    logger.info(
                        f"[{inputs.unit_number}] SSID '{display_name}' "
                        f"already activated on venue (continuing to AP group config)"
                    )
                else:
                    raise

        # =====================================================================
        # Step 2: Move SSID to specific AP Group (3-step process)
        # Sets isAllApGroups=false and binds to the target AP group.
        #
        # Retry logic: R1's activity tracking has a known bug where activities
        # stay INPROGRESS indefinitely even though the operation failed or was
        # never processed. When our activity tracking times out:
        # 1. Verify the actual SSID state via a direct R1 query
        # 2. If already on specific AP group → success (R1 just mis-reported)
        # 3. If still on venue-wide → retry (R1 never processed it)
        # 4. If not on venue at all → fail (something unexpected happened)
        # =====================================================================
        vlan_id = int(inputs.default_vlan) if inputs.default_vlan else None

        for attempt in range(1, MAX_3STEP_RETRIES + 2):
            is_retry = attempt > 1
            attempt_label = f" (retry {attempt - 1}/{MAX_3STEP_RETRIES})" if is_retry else ""

            await self.emit(
                f"[{inputs.unit_number}] Moving '{display_name}' to "
                f"AP Group '{inputs.ap_group_name}'{attempt_label}..."
            )

            try:
                # On first attempt, use activity tracker for bulk polling.
                # On retries, use direct polling to bypass R1's buggy
                # activity tracking that may have stale INPROGRESS entries.
                callback = (
                    self.fire_and_wait
                    if use_activity_tracker and not is_retry
                    else None
                )

                await self.r1_client.venues.configure_ssid_for_specific_ap_group(
                    tenant_id=self.tenant_id,
                    venue_id=self.venue_id,
                    wifi_network_id=inputs.network_id,
                    ap_group_id=inputs.ap_group_id,
                    radio_types=["2.4-GHz", "5-GHz"],
                    vlan_id=vlan_id,
                    wait_for_completion=True,
                    wait_callback=callback,
                )

                logger.info(
                    f"[{inputs.unit_number}] SSID '{display_name}' configured on "
                    f"AP Group '{inputs.ap_group_name}'{attempt_label}"
                )
                await self.emit(
                    f"[{inputs.unit_number}] '{display_name}' activated on "
                    f"AP Group '{inputs.ap_group_name}'"
                    f"{attempt_label}",
                    "success",
                )
                return self.Outputs(activated=True, already_active=False)

            except Exception as e:
                error_str = str(e).lower()

                # Already activated → success
                if (
                    'already activated' in error_str
                    or 'already exists' in error_str
                ):
                    logger.info(
                        f"[{inputs.unit_number}] SSID '{display_name}' "
                        f"already configured on AP group (from API response)"
                    )
                    await self.emit(
                        f"[{inputs.unit_number}] '{display_name}' already activated"
                    )
                    return self.Outputs(activated=True, already_active=True)

                # Timeout or failure → verify actual state and maybe retry
                if 'timed out' in error_str or 'failed' in error_str:
                    state = await self._check_ssid_venue_state(inputs)

                    if state == 'specific':
                        # R1 activity lied — SSID IS on specific AP group
                        logger.warning(
                            f"[{inputs.unit_number}] Activity stuck but "
                            f"SSID IS on AP Group '{inputs.ap_group_name}' — "
                            f"treating as success (R1 activity bug)"
                        )
                        await self.emit(
                            f"[{inputs.unit_number}] '{display_name}' activated on "
                            f"AP Group '{inputs.ap_group_name}' "
                            f"(verified after timeout)",
                            "success",
                        )
                        return self.Outputs(activated=True, already_active=False)

                    if state == 'venue_wide' and attempt <= MAX_3STEP_RETRIES:
                        # Still on venue-wide — R1 never processed it. Retry.
                        logger.warning(
                            f"[{inputs.unit_number}] SSID still on All AP Groups "
                            f"after attempt {attempt} — retrying 3-step config "
                            f"({MAX_3STEP_RETRIES - attempt + 1} retries left)"
                        )
                        await self.emit(
                            f"[{inputs.unit_number}] '{display_name}' still on "
                            f"All AP Groups after timeout — retrying...",
                            "warning",
                        )
                        continue  # retry

                    # Out of retries or not on venue at all
                    if state == 'venue_wide':
                        logger.error(
                            f"[{inputs.unit_number}] SSID still on All AP Groups "
                            f"after {attempt} attempts — giving up"
                        )
                    elif state == 'not_found':
                        logger.error(
                            f"[{inputs.unit_number}] SSID not found on venue "
                            f"after 3-step config failure"
                        )

                raise

    async def _verify_ssid_on_venue(self, inputs: 'Inputs') -> bool:
        """
        Check if the SSID is actually activated on the venue by querying R1.

        R1 sometimes keeps activities at INPROGRESS even though the operation
        completed successfully. This catches those false failures by checking
        the network's actual venueApGroups state.
        """
        state = await self._check_ssid_venue_state(inputs)
        return state in ('venue_wide', 'specific')

    async def _check_ssid_venue_state(self, inputs: 'Inputs') -> str:
        """
        Check the actual SSID activation state on the venue.

        Queries R1 directly (not activity tracking) to determine where
        the SSID currently sits. Returns one of:
          'specific'   — on a specific AP group (3-step config complete)
          'venue_wide' — on All AP Groups (isAllApGroups=true, needs 3-step)
          'not_found'  — not activated on this venue at all

        This is the source of truth for retry decisions.
        """
        display_name = inputs.ssid_name or inputs.network_id
        try:
            network = await self.r1_client.networks.query_wifi_network_by_id(
                inputs.network_id, self.tenant_id
            )
            for vag in network.get('venueApGroups', []):
                if vag.get('venueId') == self.venue_id:
                    is_all = vag.get('isAllApGroups', False)
                    if is_all:
                        logger.info(
                            f"[{inputs.unit_number}] Verify: '{display_name}' "
                            f"on All AP Groups (venue-wide)"
                        )
                        return 'venue_wide'
                    else:
                        logger.info(
                            f"[{inputs.unit_number}] Verify: '{display_name}' "
                            f"on specific AP group"
                        )
                        return 'specific'
            logger.info(
                f"[{inputs.unit_number}] Verify: '{display_name}' "
                f"not found on venue"
            )
            return 'not_found'
        except Exception as e:
            logger.warning(
                f"[{inputs.unit_number}] Verify check failed: {e}"
            )
            return 'not_found'

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """Validate activation inputs."""
        return PhaseValidation(
            valid=True,
            will_create=True,
            estimated_api_calls=4,  # 1 venue activation + 3-step AP group config
            notes=[
                "Venue activation (isAllApGroups=true) then move to specific AP group"
            ],
        )
