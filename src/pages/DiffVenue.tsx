import { useEffect, useState } from "react";
import { useAuth } from "@/context/AuthContext";
import DoubleEc from "@/components/DoubleEc";
import DoubleVenue from "@/components/DoubleVenue";
import ObjectDiff from "@/components/ObjectDiff";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

interface Venue {
  id: string;
  name?: string;
}

function DiffVenue() {
  const { activeControllerId, secondaryControllerId } = useAuth();
  const [selectedSource, setSelectedSource] = useState<string | null>(null);
  const [selectedDestination, setSelectedDestination] = useState<string | null>(null);
  const [sourceVenues, setSourceVenues] = useState<Venue[]>([]);
  const [destinationVenues, setDestinationVenues] = useState<Venue[]>([]);
  const [selectedSourceVenue, setSelectedSourceVenue] = useState<string | null>(null);
  const [selectedDestinationVenue, setSelectedDestinationVenue] = useState<string | null>(null);
  const [sourceVenueDetails, setSourceVenueDetails] = useState<any>(null);
  const [destinationVenueDetails, setDestinationVenueDetails] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [showOnlyDifferences, setShowOnlyDifferences] = useState(false);

  const handleSelectionChange = (sourceId: string | null, destinationId: string | null) => {
    setSelectedSource(sourceId);
    setSelectedDestination(destinationId);
    setSourceVenues([]);
    setDestinationVenues([]);
    setSelectedSourceVenue(null);
    setSelectedDestinationVenue(null);
    setSourceVenueDetails(null);
    setDestinationVenueDetails(null);
  };

  // Fetch venues when ECs are selected
  useEffect(() => {
    const fetchVenues = async () => {
      if (selectedSource && selectedDestination) {
        setLoading(true);
        try {
          // If same tenant selected for both, use same data
          if (selectedSource === selectedDestination) {
            const res = await fetch(`${API_BASE_URL}/fer1agg/${activeControllerId}/tenant/fulldetails?tenant_id=${selectedSource}`);
            const data = await res.json();
            const venues = data?.data?.venues || [];
            setSourceVenues(venues);
            setDestinationVenues(venues);
          } else {
            const [srcRes, destRes] = await Promise.all([
              fetch(`${API_BASE_URL}/fer1agg/${activeControllerId}/tenant/fulldetails?tenant_id=${selectedSource}`),
              fetch(`${API_BASE_URL}/fer1agg/${secondaryControllerId}/tenant/fulldetails?tenant_id=${selectedDestination}`),
            ]);
            const [srcData, destData] = await Promise.all([
              srcRes.json(),
              destRes.json(),
            ]);

            setSourceVenues(srcData?.data?.venues || []);
            setDestinationVenues(destData?.data?.venues || []);
          }
        } catch (error) {
          console.error("Error fetching venues:", error);
        } finally {
          setLoading(false);
        }
      }
    };

    fetchVenues();
  }, [selectedSource, selectedDestination, activeControllerId, secondaryControllerId]);

  // Fetch venue details when venues are selected
  useEffect(() => {
    const fetchVenueDetails = async () => {
      if (selectedSourceVenue && selectedDestinationVenue) {
        setLoading(true);
        try {
          const [srcRes, destRes] = await Promise.all([
            fetch(`${API_BASE_URL}/fer1agg/${activeControllerId}/venue/fulldetails?tenant_id=${selectedSource}&venue_id=${selectedSourceVenue}`),
            fetch(`${API_BASE_URL}/fer1agg/${secondaryControllerId}/venue/fulldetails?tenant_id=${selectedDestination}&venue_id=${selectedDestinationVenue}`),
          ]);
          const [srcData, destData] = await Promise.all([
            srcRes.json(),
            destRes.json(),
          ]);

          console.log('Source venue data:', srcData);
          console.log('Destination venue data:', destData);

          setSourceVenueDetails(srcData?.data);
          setDestinationVenueDetails(destData?.data);
        } catch (error) {
          console.error("Error fetching venue details:", error);
        } finally {
          setLoading(false);
        }
      }
    };

    fetchVenueDetails();
  }, [selectedSourceVenue, selectedDestinationVenue, selectedSource, selectedDestination, activeControllerId, secondaryControllerId]);

  return (
    <div className="p-4 max-w-7xl mx-auto">
      <h2 className="text-3xl font-bold mb-4">Compare Venues</h2>
      <p className="text-gray-600 mb-6">
        Compare detailed venue WiFi settings between two Ruckus ONE tenants including AP radio settings,
        load balancing, channel configurations, and more.
      </p>

      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h3 className="text-lg font-semibold mb-4">Step 1: Select Source and Destination Tenants</h3>
        <DoubleEc
          onSelectionChange={handleSelectionChange}
          showActions={false}
          disabled={false}
          allowSameTenant={true}
        />
      </div>

      {selectedSource && selectedDestination && selectedSource === selectedDestination && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6">
          <p className="text-blue-700">
            ℹ️ Comparing venues within the same tenant: You can select different venues from the same tenant.
          </p>
        </div>
      )}

      {/* Venue Selection */}
      {sourceVenues.length > 0 && destinationVenues.length > 0 && (
        <div className="bg-white rounded-lg shadow p-6 mb-6">
          <h3 className="text-lg font-semibold mb-4">Step 2: Select Venues to Compare</h3>
          <DoubleVenue
            sourceVenues={sourceVenues}
            destinationVenues={destinationVenues}
            onSelectionChange={(sourceId, destId) => {
              setSelectedSourceVenue(sourceId);
              setSelectedDestinationVenue(destId);
            }}
            initialSource={selectedSourceVenue}
            initialDestination={selectedDestinationVenue}
            disabled={loading}
          />
        </div>
      )}

      {loading && (
        <div className="text-center py-8">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
          <p className="mt-2 text-gray-600">Loading venue details...</p>
        </div>
      )}

      {/* Venue Details Comparison */}
      {sourceVenueDetails && destinationVenueDetails && !loading && (
        <>
          <div className="mb-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-2xl font-bold">Venue Comparison Results</h3>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={showOnlyDifferences}
                  onChange={(e) => setShowOnlyDifferences(e.target.checked)}
                  className="w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500"
                />
                <span className="text-sm font-medium text-gray-700">Only show differences</span>
              </label>
            </div>
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-4">
              <p className="text-sm text-blue-800">
                Comparing all WiFi settings including: AP Load Balancing, Radio Settings, Available Channels,
                Band Mode, External Antenna, Client Admission Control, Model Capabilities, and Antenna Types.
              </p>
            </div>
          </div>

          <ObjectDiff
            matchInfo={{
              source: sourceVenueDetails,
              dest: destinationVenueDetails,
              score: 1.0,
              matchType: 'matched'
            }}
            sectionType="venue"
            showOnlyDifferences={showOnlyDifferences}
          />

          {/* Raw Data Section - Collapsible */}
          <details className="mt-8 bg-gray-50 rounded-lg border border-gray-200">
            <summary className="cursor-pointer p-4 font-semibold text-gray-700 hover:bg-gray-100">
              View Raw JSON Data
            </summary>
            <div className="grid md:grid-cols-2 gap-6 p-4">
              <div>
                <h4 className="font-semibold text-lg mb-2 text-gray-700">Source Venue Raw Data</h4>
                <pre className="text-xs text-left overflow-auto max-h-96 bg-white p-4 rounded border border-gray-300">
                  {JSON.stringify(sourceVenueDetails, null, 2)}
                </pre>
              </div>
              <div>
                <h4 className="font-semibold text-lg mb-2 text-gray-700">Destination Venue Raw Data</h4>
                <pre className="text-xs text-left overflow-auto max-h-96 bg-white p-4 rounded border border-gray-300">
                  {JSON.stringify(destinationVenueDetails, null, 2)}
                </pre>
              </div>
            </div>
          </details>
        </>
      )}
    </div>
  );
}

export default DiffVenue;
