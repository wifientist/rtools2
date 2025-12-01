import { useState, useEffect } from "react";
import { useAuth } from "@/context/AuthContext";
import { ArrowRight, Server, Target, AlertCircle } from "lucide-react";
import SmartZoneSelector from "@/components/SmartZoneSelector";
import SzApSelect from "@/components/SzApSelect";
import SingleEcSelector from "@/components/SingleEcSelector";
import SingleVenueSelector from "@/components/SingleVenueSelector";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

interface SzAP {
  mac: string;
  name: string;
  description?: string;
  serial: string;
  model?: string;
  zoneName?: string;
  location?: string;
  latitude?: number;
  longitude?: number;
}

function MigrateSzToR1() {
  const {
    activeControllerId,
    activeControllerName,
    activeControllerType,
    secondaryControllerId,
    secondaryControllerName,
    secondaryControllerType,
    secondaryControllerSubtype,
    controllers
  } = useAuth();

  // SmartZone (source) state
  const [selectedZoneId, setSelectedZoneId] = useState<string | null>(null);
  const [selectedZoneName, setSelectedZoneName] = useState<string | null>(null);

  // RuckusONE (destination) state - using secondary controller
  const [destEcId, setDestEcId] = useState<string | null>(null);
  const [destEcName, setDestEcName] = useState<string | null>(null);
  const [destVenueId, setDestVenueId] = useState<string | null>(null);
  const [destVenueName, setDestVenueName] = useState<string | null>(null);
  const [destApGroup, setDestApGroup] = useState<string>("Default");

  // AP selection state
  const [selectedAPs, setSelectedAPs] = useState<SzAP[]>([]);
  const [showAPSelect, setShowAPSelect] = useState(false);

  // Migration state
  const [isLoading, setIsLoading] = useState(false);
  const [migrationResult, setMigrationResult] = useState<any>(null);

  // License check state
  const [licenseCheck, setLicenseCheck] = useState<{
    available: number;
    required: number;
    sufficient: boolean;
    remaining: number;
    message: string;
    total: number;
    used: number;
  } | null>(null);
  const [licenseCheckLoading, setLicenseCheckLoading] = useState(false);
  const [licenseCheckError, setLicenseCheckError] = useState<string | null>(null);

  const handleZoneSelect = (zoneId: string | null, zoneName: string | null) => {
    setSelectedZoneId(zoneId);
    setSelectedZoneName(zoneName);
    setSelectedAPs([]); // Clear AP selection when zone changes
  };

  const handleEcSelect = (ecId: string | null, ec: any) => {
    setDestEcId(ecId);
    setDestEcName(ec?.name || null);
    setDestVenueId(null); // Clear venue when EC changes
    setDestVenueName(null);
  };

  const handleVenueSelect = (venueId: string | null, venue: any) => {
    setDestVenueId(venueId);
    setDestVenueName(venue?.name || null);
  };

  const handleApGroupChange = (apGroup: string) => {
    setDestApGroup(apGroup);
  };

  const handleSelectAPClick = () => {
    if (!selectedZoneId || !activeControllerId) {
      alert("Please select a SmartZone zone first");
      return;
    }
    setShowAPSelect(true);
  };

  const handleAPSelectClose = () => {
    setShowAPSelect(false);
  };

  const handleAPSelectConfirm = (aps: SzAP[]) => {
    setSelectedAPs(aps);
    setShowAPSelect(false);
    console.log("Selected APs for migration:", aps);
  };

  const handleMigrate = async () => {
    if (!selectedZoneId) {
      alert("Please select a SmartZone zone");
      return;
    }

    if (!secondaryControllerId) {
      alert("Please select a destination RuckusONE controller");
      return;
    }

    // Check if EC selection is required (MSP) and if it's selected
    if (secondaryControllerSubtype === "MSP" && !destEcId) {
      alert("Please select a destination EC");
      return;
    }

    if (!destVenueId) {
      alert("Please select a destination venue");
      return;
    }

    if (selectedAPs.length === 0) {
      alert("Please select at least one Access Point to migrate");
      return;
    }

    setIsLoading(true);
    setMigrationResult(null);

    try {
      const payload = {
        source_controller_id: activeControllerId,
        dest_controller_id: secondaryControllerId,
        dest_tenant_id: effectiveTenantId, // Include tenant ID for MSP or EC
        dest_venue_id: destVenueId,
        dest_ap_group: destApGroup || "Default",
        aps: selectedAPs.map(ap => ({
          serial: ap.serial,
          name: ap.name,
          description: ap.description,
          mac: ap.mac,
          model: ap.model,
          latitude: ap.latitude,
          longitude: ap.longitude,
        }))
      };

      const response = await fetch(`${API_BASE_URL}/migrate/sz-to-r1`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        credentials: "include",
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || "Migration failed");
      }

      const result = await response.json();
      setMigrationResult(result);

      // Build alert message with license info if available
      let alertMsg = `Migration completed!\n${result.migrated_count} APs migrated successfully\n${result.failed_count} APs failed`;

      if (result.license_info && result.license_info.available !== "unknown") {
        const remaining = result.license_info.available - result.migrated_count;
        alertMsg += `\n\nLicenses: ${remaining} remaining (${result.license_info.available} available, ${result.migrated_count} used)`;
      }

      alert(alertMsg);

      // Reset on success
      if (result.failed_count === 0) {
        setSelectedAPs([]);
        setDestVenueId("");
      }

    } catch (error: any) {
      console.error("Migration failed:", error);
      alert(`Migration failed: ${error.message}`);
    } finally {
      setIsLoading(false);
    }
  };

  // Validate controller types
  const isActiveSmartZone = activeControllerType === "SmartZone";
  const isSecondaryRuckusOne = secondaryControllerType === "RuckusONE";
  const controllersValid = isActiveSmartZone && isSecondaryRuckusOne;

  // Determine if we need to show EC selector (MSP) or go straight to venue (EC)
  const needsEcSelection = secondaryControllerSubtype === "MSP";

  // For MSP controllers: use the selected EC ID
  // For EC controllers: use the controller's r1_tenant_id from the database
  const secondaryController = controllers.find(c => c.id === secondaryControllerId);
  const effectiveTenantId = needsEcSelection
    ? destEcId
    : (secondaryController?.r1_tenant_id || null);

  const isReadyToSelectAPs = selectedZoneId && activeControllerId && controllersValid;
  const isReadyToMigrate = isReadyToSelectAPs &&
    selectedAPs.length > 0 &&
    destVenueId &&
    secondaryControllerId &&
    (!needsEcSelection || destEcId); // EC must be selected if needed

  // Check license availability when APs are selected
  useEffect(() => {
    const checkLicenses = async () => {
      // Only check if we have APs selected and destination controller configured
      if (selectedAPs.length === 0 || !secondaryControllerId) {
        setLicenseCheck(null);
        setLicenseCheckError(null);
        return;
      }

      // Check if EC selection is required and if it's selected
      if (needsEcSelection && !destEcId) {
        setLicenseCheck(null);
        setLicenseCheckError(null);
        return;
      }

      setLicenseCheckLoading(true);
      setLicenseCheckError(null);

      try {
        const payload = {
          controller_id: secondaryControllerId,
          tenant_id: effectiveTenantId,
          ap_count: selectedAPs.length,
        };

        const response = await fetch(`${API_BASE_URL}/migrate/check-license`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          credentials: "include",
          body: JSON.stringify(payload),
        });

        if (!response.ok) {
          const error = await response.json();
          throw new Error(error.detail || "License check failed");
        }

        const result = await response.json();
        setLicenseCheck(result);
      } catch (error: any) {
        console.error("License check failed:", error);
        setLicenseCheckError(error.message);
        setLicenseCheck(null);
      } finally {
        setLicenseCheckLoading(false);
      }
    };

    checkLicenses();
  }, [selectedAPs, secondaryControllerId, effectiveTenantId, destEcId, needsEcSelection]);

  return (
    <div className="p-4 max-w-6xl mx-auto">
      <div className="mb-8">
        <h2 className="text-3xl font-bold mb-2">SmartZone to RuckusONE Migration</h2>
        <p className="text-gray-600">
          Migrate Access Points from SmartZone to RuckusONE in 3 simple steps
        </p>
      </div>

      {/* Controller Type Validation Error */}
      {!controllersValid && (activeControllerId || secondaryControllerId) && (
        <div className="bg-red-50 border-2 border-red-200 rounded-lg p-6 mb-6">
          <div className="flex items-start gap-3">
            <AlertCircle className="w-6 h-6 text-red-600 mt-0.5 flex-shrink-0" />
            <div className="flex-1">
              <h3 className="text-lg font-semibold text-red-900 mb-2">
                Invalid Controller Configuration
              </h3>
              <p className="text-red-800 mb-3">
                This migration tool requires your <strong>Active Controller</strong> to be a <strong>SmartZone</strong> controller
                and your <strong>Secondary Controller</strong> to be a <strong>RuckusONE</strong> controller.
              </p>
              <div className="bg-white rounded-lg p-4 space-y-2 text-sm">
                <div className="flex items-center justify-between">
                  <span className="text-gray-700">Active Controller:</span>
                  <span className={`font-semibold ${isActiveSmartZone ? 'text-green-600' : 'text-red-600'}`}>
                    {activeControllerType || 'Not selected'} {isActiveSmartZone ? '✓' : '✗'}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-gray-700">Secondary Controller:</span>
                  <span className={`font-semibold ${isSecondaryRuckusOne ? 'text-green-600' : 'text-red-600'}`}>
                    {secondaryControllerType || 'Not selected'} {isSecondaryRuckusOne ? '✓' : '✗'}
                  </span>
                </div>
              </div>
              <p className="text-sm text-red-700 mt-3">
                Please go to the <a href="/controllers" className="underline font-semibold">Controllers</a> page
                to select the correct controllers for this migration.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Migration Flow Diagram */}
      <div className="bg-gradient-to-r from-blue-50 to-green-50 rounded-lg p-6 mb-6">
        <div className="flex items-center justify-center gap-6">
          <div className="flex items-center gap-3">
            <Server className="w-6 h-6 text-blue-600" />
            <div>
              <div className="font-semibold text-blue-900">SmartZone</div>
              <div className="text-sm text-blue-700">{activeControllerName || "Not selected"}</div>
            </div>
          </div>

          <ArrowRight className="w-8 h-8 text-gray-400" />

          <div className="flex items-center gap-3">
            <Target className="w-6 h-6 text-green-600" />
            <div>
              <div className="font-semibold text-green-900">RuckusONE</div>
              <div className="text-sm text-green-700">{secondaryControllerName || "Not selected"}</div>
            </div>
          </div>
        </div>
      </div>

      {/* Step 1: Select SmartZone Zone */}
      <div className="mb-6">
        <div className="flex items-center gap-2 mb-3">
          <div className="w-8 h-8 rounded-full bg-blue-600 text-white flex items-center justify-center font-bold">
            1
          </div>
          <h3 className="text-xl font-semibold">Select Source Zone</h3>
        </div>
        <SmartZoneSelector
          onZoneSelect={handleZoneSelect}
          disabled={!activeControllerId}
        />
      </div>

      {/* Step 2: Select Destination EC (if MSP) */}
      {selectedZoneId && needsEcSelection && (
        <div className="mb-6">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-8 h-8 rounded-full bg-blue-600 text-white flex items-center justify-center font-bold">
              2
            </div>
            <h3 className="text-xl font-semibold">Select Destination EC</h3>
          </div>

          <div className="bg-white rounded-lg shadow p-6">
            <SingleEcSelector
              controllerId={secondaryControllerId}
              onEcSelect={handleEcSelect}
              selectedEcId={destEcId}
            />
          </div>
        </div>
      )}

      {/* Step 3: Select Destination Venue */}
      {selectedZoneId && (!needsEcSelection || destEcId) && effectiveTenantId && (
        <div className="mb-6">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-8 h-8 rounded-full bg-green-600 text-white flex items-center justify-center font-bold">
              {needsEcSelection ? "3" : "2"}
            </div>
            <h3 className="text-xl font-semibold">Select Destination Venue</h3>
          </div>

          <div className="bg-white rounded-lg shadow p-6">
            <SingleVenueSelector
              controllerId={secondaryControllerId}
              tenantId={effectiveTenantId}
              onVenueSelect={handleVenueSelect}
              onApGroupChange={handleApGroupChange}
              selectedVenueId={destVenueId}
              selectedApGroup={destApGroup}
            />
          </div>
        </div>
      )}

      {/* Step 4: Select APs */}
      {selectedZoneId && destVenueId && (
        <div className="mb-6">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-8 h-8 rounded-full bg-purple-600 text-white flex items-center justify-center font-bold">
              {needsEcSelection ? "4" : "3"}
            </div>
            <h3 className="text-xl font-semibold">Select Access Points</h3>
          </div>

          <div className="bg-white rounded-lg shadow p-6">
            <button
              onClick={handleSelectAPClick}
              disabled={!isReadyToSelectAPs || isLoading}
              className={`btn px-6 py-2 rounded font-medium ${
                isReadyToSelectAPs && !isLoading
                  ? "bg-purple-600 text-white hover:bg-purple-700"
                  : "bg-gray-300 text-gray-500 cursor-not-allowed"
              }`}
            >
              {selectedAPs.length > 0
                ? `Reselect APs (${selectedAPs.length} selected)`
                : "Select APs to Migrate"}
            </button>

            {selectedAPs.length > 0 && (
              <div className="mt-4 bg-green-50 border border-green-200 rounded-lg p-4">
                <div className="font-semibold text-green-900 mb-2">
                  {selectedAPs.length} APs Selected
                </div>
                <div className="text-sm text-green-700">
                  {selectedAPs.slice(0, 3).map(ap => ap.name || ap.serial).join(", ")}
                  {selectedAPs.length > 3 && ` and ${selectedAPs.length - 3} more...`}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Migration Button */}
      {isReadyToMigrate && (
        <div className="bg-white rounded-lg shadow p-6">
          {/* License Status Display */}
          {licenseCheckLoading && (
            <div className="mb-4 flex items-center gap-2 text-blue-600">
              <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-blue-600"></div>
              <span className="text-sm">Checking license availability...</span>
            </div>
          )}

          {licenseCheckError && (
            <div className="mb-4 bg-yellow-50 border border-yellow-200 rounded-lg p-4">
              <div className="flex items-start gap-2">
                <AlertCircle className="w-5 h-5 text-yellow-600 mt-0.5 flex-shrink-0" />
                <div>
                  <div className="font-semibold text-yellow-900 mb-1">License Check Failed</div>
                  <div className="text-sm text-yellow-800">{licenseCheckError}</div>
                  <div className="text-sm text-yellow-700 mt-2">
                    You can still attempt migration, but it may fail if licenses are insufficient.
                  </div>
                </div>
              </div>
            </div>
          )}

          {licenseCheck && (
            <div className={`mb-4 rounded-lg p-4 border-2 ${
              licenseCheck.sufficient
                ? "bg-green-50 border-green-200"
                : "bg-red-50 border-red-200"
            }`}>
              <div className="flex items-start gap-2">
                <div className={`text-2xl ${licenseCheck.sufficient ? "text-green-600" : "text-red-600"}`}>
                  {licenseCheck.sufficient ? "✓" : "✗"}
                </div>
                <div className="flex-1">
                  <div className={`font-semibold mb-2 ${
                    licenseCheck.sufficient ? "text-green-900" : "text-red-900"
                  }`}>
                    {licenseCheck.sufficient ? "Ready to Migrate" : "Insufficient Licenses"}
                  </div>
                  <div className={`text-sm mb-3 ${
                    licenseCheck.sufficient ? "text-green-800" : "text-red-800"
                  }`}>
                    {licenseCheck.message}
                  </div>
                  {/* Show Total/Used/Available breakdown */}
                  <div className={`text-xs mb-3 font-mono ${
                    licenseCheck.sufficient ? "text-green-700" : "text-red-700"
                  }`}>
                    Total: {licenseCheck.total}, Used: {licenseCheck.used}
                  </div>
                  <div className="grid grid-cols-3 gap-4 text-sm">
                    <div className={licenseCheck.sufficient ? "text-green-700" : "text-red-700"}>
                      <div className="font-medium">Available</div>
                      <div className="text-lg font-bold">{licenseCheck.available}</div>
                    </div>
                    <div className={licenseCheck.sufficient ? "text-green-700" : "text-red-700"}>
                      <div className="font-medium">Required</div>
                      <div className="text-lg font-bold">{licenseCheck.required}</div>
                    </div>
                    <div className={licenseCheck.sufficient ? "text-green-700" : "text-red-700"}>
                      <div className="font-medium">{licenseCheck.sufficient ? "Remaining" : "Short by"}</div>
                      <div className="text-lg font-bold">
                        {licenseCheck.sufficient
                          ? licenseCheck.remaining
                          : licenseCheck.required - licenseCheck.available}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}

          <button
            onClick={handleMigrate}
            disabled={isLoading || (licenseCheck !== null && !licenseCheck.sufficient)}
            className={`btn px-8 py-3 rounded-lg font-medium text-lg ${
              isLoading || (licenseCheck !== null && !licenseCheck.sufficient)
                ? "bg-gray-300 text-gray-500 cursor-not-allowed"
                : "bg-green-600 text-white hover:bg-green-700"
            }`}
          >
            {isLoading ? "Migrating..." : `Migrate ${selectedAPs.length} APs to RuckusONE`}
          </button>

          {licenseCheck !== null && !licenseCheck.sufficient && (
            <div className="mt-3 text-sm text-red-600">
              Migration is disabled due to insufficient licenses. Please purchase additional licenses or reduce the number of APs.
            </div>
          )}

          {isLoading && (
            <div className="mt-4 flex items-center gap-2 text-blue-600">
              <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-blue-600"></div>
              <span className="text-sm">Migration in progress...</span>
            </div>
          )}
        </div>
      )}

      {/* Migration Result */}
      {migrationResult && (
        <div className={`mt-6 rounded-lg p-6 ${
          migrationResult.failed_count === 0
            ? "bg-green-50 border border-green-200"
            : "bg-yellow-50 border border-yellow-200"
        }`}>
          <h3 className="font-semibold text-lg mb-2">
            {migrationResult.status === "completed" ? "✅ Migration Completed" : "⚠️ Migration Partial"}
          </h3>
          <p className="text-sm mb-4">{migrationResult.message}</p>

          {migrationResult.details && migrationResult.details.length > 0 && (
            <div className="bg-white rounded p-4 max-h-60 overflow-y-auto">
              <div className="text-sm space-y-2">
                {migrationResult.details.map((detail: any, idx: number) => (
                  <div key={idx} className={`flex items-start gap-2 ${
                    detail.status === "success" ? "text-green-700" : "text-red-700"
                  }`}>
                    <span>{detail.status === "success" ? "✓" : "✗"}</span>
                    <span>{detail.serial}: {detail.message}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* AP Selection Modal */}
      {showAPSelect && activeControllerId && selectedZoneId && (
        <SzApSelect
          controllerId={activeControllerId}
          zoneId={selectedZoneId}
          onClose={handleAPSelectClose}
          onConfirm={handleAPSelectConfirm}
        />
      )}
    </div>
  );
}

export default MigrateSzToR1;
