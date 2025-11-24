import React, { useState } from "react";
import { useAuth } from "@/context/AuthContext";

//import { useDualMspEcs } from "@/hooks/useDualMspEcs";
import { useVenueDetails } from "@/hooks/useVenueDetails";
//import DoubleECSelect from "@/components/DoubleECSelect";
import DoubleEc from "@/components/DoubleEc";
//import { useDualEc } from "@/hooks/useDualEc";
import VenueSelectPanel from "@/components/VenueSelectPanel";
import SimpleAPSelect from "@/components/SimpleAPSelect";

function Migrate() {
    const { activeControllerName } = useAuth();
    //const { ecData, loading: loadingEcs, error: errorEcs } = useMspEcs();
    //const { activeEcData, secondaryEcData, loadingEcs, errorEcs } = useDualMspEcs();
    //const { activeEcData, secondaryEcData, loadingEcs, errorEcs } = useDualEc();
    
    const [selectedSource, setSelectedSource] = useState(null);
    const [selectedDestination, setSelectedDestination] = useState(null);
    const [selectedSourceName, setSelectedSourceName] = useState(null);
    const [selectedDestinationName, setSelectedDestinationName] = useState(null);
    const [selectedSourceEc, setSelectedSourceEc] = useState(null);
    const [selectedDestinationEc, setSelectedDestinationEc] = useState(null);
    const [selectedAPs, setSelectedAPs] = useState([]);
    const [showAPSelect, setShowAPSelect] = useState(false);
    const [isLoading, setIsLoading] = useState(false);
    const [resetKey, setResetKey] = useState(0); // Key to force component reset
    const [selectedSourceVenues, setSelectedSourceVenues] = useState([]); // list of venues
    const [selectedDestinationVenue, setSelectedDestinationVenue] = useState(null); // single ID or object
    const {
        sourceVenueData,
        destinationVenueData,
        loadingVenues,
        errorVenues
      } = useVenueDetails(selectedSource, selectedDestination);


    //const handleSelectionChange = (sourceId, destinationId) => {
    //    setSelectedSource(sourceId);
    //    setSelectedDestination(destinationId);
        
        // Clear selected APs when EC selection changes
    //    setSelectedAPs([]);
        
        // Log for debugging
    //    console.log("Selection changed:", {
    //        source: sourceId,
    //        destination: destinationId
    //    });
    //};

    const handleSelectionChange = (sourceId, destinationId, sourceEc, destinationEc) => {
        setSelectedSource(sourceId);
        setSelectedDestination(destinationId);
        setSelectedSourceName(sourceEc?.name || null);
        setSelectedDestinationName(destinationEc?.name || null);
        setSelectedSourceEc(sourceEc || null);
        setSelectedDestinationEc(destinationEc || null);
        setSelectedSourceVenues([]);
        setSelectedDestinationVenue(null);
        setSelectedAPs([]);
        //console.log("[migrate] Selection changed:", { sourceId, destinationId, sourceEc, destinationEc });
    };
    

    const handleSelectAPClick = () => {
        if (!selectedSource || !selectedDestination) {
            alert("Please select both source and destination ECs");
            return;
        }
        setShowAPSelect(true);
    };

    const handleAPSelectClose = () => {
        setShowAPSelect(false);
    };

    const handleAPSelectConfirm = (aps) => {
        setSelectedAPs(aps);
        setShowAPSelect(false);
        console.log("Selected APs for migration:", aps);
        
        // Automatically proceed to migration after AP selection
        //if (aps.length > 0) {
        //    handleMigrate(aps);
        //}
        
    };

    const handleMigrate = async (apsToMigrate = selectedAPs) => {
        if (!selectedSource || !selectedDestination) {
            alert("Please select both source and destination ECs");
            return;
        }

        if (!apsToMigrate || apsToMigrate.length === 0) {
            alert("Please select at least one Access Point to migrate");
            return;
        }

        setIsLoading(true);
        
        try {
            //const sourceEc = activeEcData.find(ec => ec.id === selectedSource);
            //const destinationEc = secondaryEcData.find(ec => ec.id === selectedDestination);
            
            console.log("Starting migration:", {
                //source: sourceEc,
                //destination: destinationEc,
                source: selectedSourceName,
                destination: selectedDestinationName,
                aps: apsToMigrate
            });
              

            // TODO: Step 1:  Create a BACKUP of the selected APs and ALL their data.  
            //       Step 2:  Store that into redis or my pqql  db? 
            //       Step 3:  Add historical migrations to teh uer's profile somehow aka Audit logs

            // TODO: Replace with your actual migration API calls
            // const result = await migrationService.migrateAPs(
            //     selectedSource, 
            //     selectedDestination, 
            //     apsToMigrate.map(ap => ap.serialNumber)
            // );
            
            // Simulate API call for now
            await new Promise(resolve => setTimeout(resolve, 3000));
            
            //alert(`Migration completed! ${apsToMigrate.length} Access Points migrated from ${sourceEc.name} to ${destinationEc.name}.`);
            alert(`Migration completed! ${apsToMigrate.length} Access Points migrated from ${selectedSourceName} to ${selectedDestinationName}.`);


            // TODO: Add a confirmation step perhaps???
            
            // Reset selections after successful migration
            setSelectedSource(null);
            setSelectedDestination(null);
            setSelectedAPs([]);
            setResetKey(prev => prev + 1); // Force component reset
            
        } catch (error) {
            console.error("Migration failed:", error);
            alert("Migration failed. Please try again.");
        } finally {
            setIsLoading(false);
        }
    };

    // Loading and error states
    //if (loadingEcs) return <p className="p-4">Loading End Customers...</p>;
    //if (errorEcs) return <p className="p-4">Error loading End Customers: {errorEcs}</p>;

    const isReadyToSelectAPs = selectedSource && selectedDestination && selectedSource !== selectedDestination;
    const isReadyToMigrate = isReadyToSelectAPs && selectedAPs.length > 0;

    return (
        <div className="p-4 max-w-6xl mx-auto">
            <div className="mb-8">
                <h2 className="text-3xl font-bold mb-2">Access Point Migration</h2>
                <p className="text-gray-600">
                    Select source and destination End Customers, then choose specific Access Points to migrate.
                </p>
            </div>

            <div className="bg-white rounded-lg shadow p-6 mb-6">
                <DoubleEc
                    onSelectionChange={handleSelectionChange}
                    initialSource={selectedSource}
                    initialDestination={selectedDestination}
                    showActions={false}
                    disabled={false}
                />
            </div>
            <div className="bg-white rounded-lg shadow p-6 mb-6">
                <VenueSelectPanel
                sourceVenues={sourceVenueData}
                destinationVenues={destinationVenueData}
                defaultDestinationAddress={selectedDestination?.address || ""}
                onSourceChange={setSelectedSourceVenues}
                onDestinationChange={setSelectedDestinationVenue}
                />
            </div>

            {/* Migration Status Panel */}
            {isReadyToSelectAPs && (
                <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6">
                    <h3 className="font-semibold text-blue-900 mb-2">Migration Summary</h3>
                    <div className="text-sm text-blue-800 space-y-1">
                        <div>
                            Source: <span className="font-medium">{selectedSourceName || 'Not selected'}</span>
                        </div>
                        <div>
                            Destination: <span className="font-medium">{selectedDestinationName || 'Not selected'}</span>
                        </div>

                        {selectedAPs.length > 0 && (
                            <div className="mt-2 pt-2 border-t border-blue-300">
                                <span className="font-medium text-green-700">
                                    {selectedAPs.length} Access Point{selectedAPs.length !== 1 ? 's' : ''} selected for migration
                                </span>
                                <div className="mt-1 text-xs">
                                    {selectedAPs.slice(0, 3).map(ap => ap.name || ap.serialNumber).join(', ')}
                                    {selectedAPs.length > 3 && ` and ${selectedAPs.length - 3} more...`}
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            )}

            <div className="flex gap-4 mb-6">
                <button
                    onClick={handleSelectAPClick}
                    disabled={!isReadyToSelectAPs || isLoading}
                    className={`btn px-6 py-2 rounded font-medium ${
                        isReadyToSelectAPs && !isLoading
                            ? "bg-blue-600 text-white hover:bg-blue-700"
                            : "bg-gray-300 text-gray-500 cursor-not-allowed"
                    }`}
                >
                    {selectedAPs.length > 0 ? `Reselect APs (${selectedAPs.length} selected)` : "Select APs to Migrate"}
                </button>

                {/* Alternative direct migration button if APs are already selected */}
                {isReadyToMigrate && !isLoading && (
                    <button
                        onClick={() => handleMigrate()}
                        className="btn px-6 py-2 rounded font-medium bg-green-600 text-white hover:bg-green-700"
                    >
                        Start Migration Now
                    </button>
                )}
                
                {isLoading && (
                    <div className="flex items-center gap-2 text-blue-600">
                        <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-blue-600"></div>
                        <span className="text-sm">Migration in progress...</span>
                    </div>
                )}
            </div>

            {/* Migration Information Panel */}
            <div className="mt-8 p-4 bg-gray-50 rounded-lg">
                <h3 className="font-medium mb-2">Migration Information</h3>
                <div className="text-sm text-gray-600 space-y-1">
                    <p>• Selected Access Points will be transferred from source to destination EC</p>
                    <p>• No AP configurations or settings will be changed during migration</p>
                    <p>• Migration process may take several minutes depending on the number of APs</p>
                    <p>• APs may experience brief connectivity interruptions during the process</p>
                </div>
            </div>

            {/* Debug Info (remove in production) */}
            {process.env.NODE_ENV === 'development' && (
                <div className="mt-8 p-4 bg-yellow-50 rounded-lg">
                    <h3 className="font-medium mb-2">Debug Info</h3>
                    <div className="text-sm space-y-1">
                        
                        <p>Selected Source ID: {selectedSource || 'None'}</p>
                        <p>Selected Destination ID: {selectedDestination || 'None'}</p>
                        <p>Selected APs: {selectedAPs.length}</p>
                        <p>Ready to select APs: {isReadyToSelectAPs ? 'Yes' : 'No'}</p>
                        <p>Ready to migrate: {isReadyToMigrate ? 'Yes' : 'No'}</p>
                    </div>
                </div>
            )}

            {/* AP Selection Modal */}
            {showAPSelect && (
                <SimpleAPSelect
                    sourceId={selectedSource}
                    destinationId={selectedDestination}
                    sourceVenueData={sourceVenueData}
                    destinationVenueData={destinationVenueData}
                    onClose={handleAPSelectClose}
                    onConfirm={handleAPSelectConfirm}
                />
            )}
        </div>
    );
}

export default Migrate;