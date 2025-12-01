"""
Entitlements Service for RuckusONE API
Handles license and entitlement operations
"""


class EntitlementsService:
    def __init__(self, client):
        self.client = client  # back-reference to main R1Client

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
        import logging
        from datetime import datetime, timedelta

        logger = logging.getLogger(__name__)

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

        print(f"🎫 Checking license availability with operator {operator}")
        print(f"📤 Request Payload: {payload}")

        # Make API call
        if self.client.ec_type == "MSP" and tenant_id:
            print(f"🌐 Making MSP request with tenant_id override: {tenant_id}")
            response = self.client.post(
                "/entitlements/availabilityReports/query",
                payload=payload,
                override_tenant_id=tenant_id
            )
        else:
            print(f"🌐 Making EC request (no tenant_id override)")
            response = self.client.post(
                "/entitlements/availabilityReports/query",
                payload=payload
            )

        print(f"📊 Response Status Code: {response.status_code}")
        print(f"📊 Response Headers: {dict(response.headers)}")

        result = response.json()
        print(f"📥 Full API Response: {result}")

        # Log structure details
        if isinstance(result, dict):
            print(f"📋 Response keys: {result.keys()}")
            if 'data' in result:
                print(f"📋 Data section: {result['data']}")
                if isinstance(result['data'], dict):
                    print(f"📋 Data keys: {result['data'].keys()}")

        return result

    async def get_license_utilization(self, tenant_id: str = None):
        """
        Get license utilization (allocated vs used) for AP software licenses

        Args:
            tenant_id: Optional tenant ID (required for MSP)

        Returns:
            Dict with license utilization data including allocated and used counts
        """
        print(f"🎫 get_license_utilization called - tenant_id: {tenant_id}")

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

        print(f"📤 Utilization Request Payload: {payload}")

        # Make API call
        if self.client.ec_type == "MSP" and tenant_id:
            print(f"🌐 Making MSP utilization request with tenant_id override: {tenant_id}")
            response = self.client.post(
                "/entitlements/utilizations/query",
                payload=payload,
                override_tenant_id=tenant_id
            )
        else:
            print(f"🌐 Making EC utilization request (no tenant_id override)")
            response = self.client.post(
                "/entitlements/utilizations/query",
                payload=payload
            )

        print(f"📊 Response Status Code: {response.status_code}")

        result = response.json()
        print(f"📥 Full Utilization Response: {result}")

        return result

    async def get_available_ap_licenses(self, tenant_id: str = None):
        """
        Get the current count of available AP software licenses

        Args:
            tenant_id: Optional tenant ID (required for MSP)

        Returns:
            Dict with 'available', 'total', and 'used' license counts
        """
        print(f"🎫 get_available_ap_licenses called - tenant_id: {tenant_id}")

        try:
            # Get utilization data
            result = await self.get_license_utilization(tenant_id=tenant_id)

            print(f"📊 License utilization result type: {type(result)}")

            # Extract available licenses from utilization data
            # The response should have data array with license info
            if isinstance(result, dict) and 'data' in result:
                data_list = result.get('data', [])
                print(f"📋 Found {len(data_list)} license entries")

                # Sum up available licenses from all entries
                # The API provides: quantity (total), usedQuantity (in use), remainingQuantity (available)
                total_quantity = 0
                total_used = 0
                total_remaining = 0

                for entry in data_list:
                    print(f"📋 License entry: {entry}")
                    quantity = entry.get('quantity', 0)
                    used = entry.get('usedQuantity', 0)
                    remaining = entry.get('remainingQuantity', 0)

                    total_quantity += quantity
                    total_used += used
                    total_remaining += remaining

                # Use remainingQuantity if available, otherwise calculate as quantity - usedQuantity
                if total_remaining > 0:
                    available = total_remaining
                    print(f"✅ Using remainingQuantity: {available} (Total: {total_quantity}, Used: {total_used})")
                else:
                    available = total_quantity - total_used
                    print(f"✅ Calculated available: {available} (Total: {total_quantity}, Used: {total_used})")

                return {
                    'available': available,
                    'total': total_quantity,
                    'used': total_used
                }
            else:
                print(f"⚠️ Unexpected result format, returning 0")
                return {'available': 0, 'total': 0, 'used': 0}
        except Exception as e:
            print(f"❌ Error in get_available_ap_licenses: {str(e)}")
            import traceback
            traceback.print_exc()
            raise
