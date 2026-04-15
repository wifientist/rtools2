"""
Entitlements Service for RuckusONE API
Handles license and entitlement operations
"""
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class EntitlementsService:
    def __init__(self, client):
        self.client = client  # back-reference to main R1Client

    def get_msp_compliance_summary(self):
        """
        Fetch MSP-wide license compliance via /entitlements/compliances/query.

        Returns a shaped dict ready for the frontend:
            {
                "license_type": "APSW",
                "total_paid": int,            # total active paid licenses in pool
                "used": int,                  # licenses currently consumed
                "available": int,             # headroom (licenseGap)
                "expiring_soon": int,         # next batch of paid licenses to expire
                "next_expiration_date": str,  # ISO date of next expiration (or None)
                "consolidated_sku_enabled": bool,
                "device_breakdown": [
                    {"device_type": "WIFI", "installed": 48406, "used": 48406},
                    ...
                ],
                "self_licenses": int,         # MSP's own direct licenses (mostly 0)
                "raw": {...}                  # full mspEcSummary for anything not mapped
            }

        Uses {"filters": {"complianceType": "MSP_SUMMARY"}} — complianceType is
        marked deprecated in the spec but is the only filter that returns real
        data. Empty filters / licenseType-only filters return compliances:[null].

        Only applicable to MSP controllers; returns a minimal placeholder for
        non-MSP clients rather than raising so the UI can gracefully degrade.

        Note: sync. Call via asyncio.to_thread from async contexts.
        """
        if self.client.ec_type != "MSP":
            return {
                "license_type": None,
                "error": "non-MSP client",
            }

        body = {"filters": {"complianceType": "MSP_SUMMARY"}}

        try:
            resp = self.client.post("/entitlements/compliances/query", payload=body)
        except Exception as exc:
            logger.exception("[compliance_summary] request raised")
            return {"error": f"{type(exc).__name__}: {exc}"}

        if not resp.ok:
            logger.warning(
                f"[compliance_summary] HTTP {resp.status_code}: {resp.text[:200]}"
            )
            return {"error": f"HTTP {resp.status_code}"}

        payload = resp.json() or {}
        compliances = payload.get("compliances") or []
        # Filter out null entries — some filter combinations return [null].
        compliances = [c for c in compliances if c]

        if not compliances:
            logger.info("[compliance_summary] empty compliances array")
            return {"error": "no compliance data returned"}

        # The endpoint returns one entry per licenseType. Today that's just
        # APSW, but handle multiple gracefully by preferring APSW if present.
        entry = next(
            (c for c in compliances if c.get("licenseType") == "APSW"),
            compliances[0],
        )

        msp_summary = entry.get("mspEcSummary") or {}
        self_summary = entry.get("self") or {}

        device_breakdown = []
        for dev in msp_summary.get("deviceCompliances") or []:
            device_breakdown.append(
                {
                    "device_type": dev.get("deviceType"),
                    "installed": int(dev.get("installedDeviceCount") or 0),
                    "used": int(dev.get("usedLicenseCount") or 0),
                }
            )

        result = {
            "license_type": entry.get("licenseType"),
            "tenant_name": msp_summary.get("tenantName"),
            "total_paid": int(msp_summary.get("totalActivePaidLicenseCount") or 0),
            "used": int(msp_summary.get("licensesUsed") or 0),
            "available": int(msp_summary.get("licenseGap") or 0),
            "expiring_soon": int(
                msp_summary.get("nextTotalPaidExpiringLicenseCount") or 0
            ),
            "next_expiration_date": msp_summary.get("nextPaidExpirationDate"),
            "consolidated_sku_enabled": bool(
                msp_summary.get("consolidatedSkuEnabled")
            ),
            "extended_trial_enabled": bool(msp_summary.get("extendedTrialEnabled")),
            "device_breakdown": device_breakdown,
            "self_licenses": int(
                self_summary.get("totalActivePaidAssignedLicenseCount") or 0
            ),
        }

        logger.info(
            f"[compliance_summary] total_paid={result['total_paid']} "
            f"used={result['used']} available={result['available']} "
            f"expiring_soon={result['expiring_soon']}"
        )
        return result

    async def check_license_availability(
        self,
        license_type: str = "APSW",
        quantity: int = None,
        effective_date: str = None,
        expiration_date: str = None,
        usage_type: str = "SELF",
        tenant_id: str = None
    ):
        """
        Check license availability for AP software licenses

        Args:
            license_type: Type of license (default: "APSW" for AP Software)
            quantity: Number of licenses needed (required for MAX_PERIOD operator)
            effective_date: Start date (format: YYYY-MM-DD)
            expiration_date: End date (format: YYYY-MM-DD, required for MAX_QUANTITY operator)
            usage_type: Usage type - "SELF", "ASSIGNED", or "UNKNOWN"
            tenant_id: Optional tenant ID (required for MSP)

        Returns:
            License availability report with quantity and dates
        """

        # Determine operator based on what we're checking
        if quantity is not None:
            # Check MAX_PERIOD: given a quantity, how long can we use it?
            operator = "MAX_PERIOD"
            # Default expiration date not needed for MAX_PERIOD
        else:
            # Check MAX_QUANTITY: given a date range, how many licenses?
            operator = "MAX_QUANTITY"
            # Ensure expiration_date is provided
            if not expiration_date:
                # Default to 1 year from effective date
                if effective_date:
                    eff_date = datetime.strptime(effective_date, "%Y-%m-%d")
                else:
                    eff_date = datetime.now()
                exp_date = eff_date + timedelta(days=365)
                expiration_date = exp_date.strftime("%Y-%m-%d")

        # Default effective date to today
        if not effective_date:
            effective_date = datetime.now().strftime("%Y-%m-%d")

        # Build request payload
        payload = {
            "effectiveDate": effective_date,
            "operator": operator,
            "filters": {
                "usageType": usage_type,
                "licenseType": license_type
            }
        }

        # Add conditional fields based on operator
        if operator == "MAX_QUANTITY":
            payload["expirationDate"] = expiration_date
        elif operator == "MAX_PERIOD":
            payload["quantity"] = quantity

        logger.debug(f"Checking license availability with operator {operator}")
        logger.debug(f"Request Payload: {payload}")

        # Make API call
        if self.client.ec_type == "MSP" and tenant_id:
            logger.debug(f"Making MSP request with tenant_id override: {tenant_id}")
            response = self.client.post(
                "/entitlements/availabilityReports/query",
                payload=payload,
                override_tenant_id=tenant_id
            )
        else:
            logger.debug(f"Making EC request (no tenant_id override)")
            response = self.client.post(
                "/entitlements/availabilityReports/query",
                payload=payload
            )

        logger.debug(f"Response Status Code: {response.status_code}")

        result = response.json()
        logger.debug(f"License availability response: {result}")

        return result

    async def get_license_utilization(self, tenant_id: str = None):
        """
        Get license utilization (allocated vs used) for AP software licenses

        Args:
            tenant_id: Optional tenant ID (required for MSP)

        Returns:
            Dict with license utilization data including allocated and used counts
        """
        logger.debug(f"get_license_utilization called - tenant_id: {tenant_id}")

        # Build request payload for utilization query
        payload = {
            "filters": {
                "licenseType": ["APSW"],
                "isTrial": False,
                "status": ["VALID"],
                "isAssignedLicense": False,
                "usageType": "SELF"
            }
        }

        logger.debug(f"Utilization Request Payload: {payload}")

        # Make API call
        if self.client.ec_type == "MSP" and tenant_id:
            logger.debug(f"Making MSP utilization request with tenant_id override: {tenant_id}")
            response = self.client.post(
                "/entitlements/utilizations/query",
                payload=payload,
                override_tenant_id=tenant_id
            )
        else:
            logger.debug(f"Making EC utilization request (no tenant_id override)")
            response = self.client.post(
                "/entitlements/utilizations/query",
                payload=payload
            )

        logger.debug(f"Response Status Code: {response.status_code}")

        result = response.json()
        logger.debug(f"License utilization response: {result}")

        return result

    async def get_available_ap_licenses(self, tenant_id: str = None):
        """
        Get the current count of available AP software licenses

        Args:
            tenant_id: Optional tenant ID (required for MSP)

        Returns:
            Dict with 'available', 'total', and 'used' license counts
        """
        logger.debug(f"get_available_ap_licenses called - tenant_id: {tenant_id}")

        try:
            # Get utilization data
            result = await self.get_license_utilization(tenant_id=tenant_id)

            # Extract available licenses from utilization data
            # The response should have data array with license info
            if isinstance(result, dict) and 'data' in result:
                data_list = result.get('data', [])
                logger.debug(f"Found {len(data_list)} license entries")

                # Sum up available licenses from all entries
                # The API provides: quantity (total), usedQuantity (in use), remainingQuantity (available)
                total_quantity = 0
                total_used = 0
                total_remaining = 0

                for entry in data_list:
                    logger.debug(f"License entry: {entry}")
                    quantity = entry.get('quantity', 0)
                    used = entry.get('usedQuantity', 0)
                    remaining = entry.get('remainingQuantity', 0)

                    total_quantity += quantity
                    total_used += used
                    total_remaining += remaining

                # Use remainingQuantity if available, otherwise calculate as quantity - usedQuantity
                if total_remaining > 0:
                    available = total_remaining
                    logger.debug(f"Using remainingQuantity: {available} (Total: {total_quantity}, Used: {total_used})")
                else:
                    available = total_quantity - total_used
                    logger.debug(f"Calculated available: {available} (Total: {total_quantity}, Used: {total_used})")

                return {
                    'available': available,
                    'total': total_quantity,
                    'used': total_used
                }
            else:
                logger.warning(f"Unexpected result format, returning 0")
                return {'available': 0, 'total': 0, 'used': 0}
        except Exception as e:
            logger.exception(f"Error in get_available_ap_licenses: {str(e)}")
            raise
