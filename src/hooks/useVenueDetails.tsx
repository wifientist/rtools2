import { useState, useEffect } from "react";

export function useVenueDetails(sourceEcId?: string | number, destinationEcId?: string | number) {
  const [sourceVenueData, setSourceVenueData] = useState([]);
  const [destinationVenueData, setDestinationVenueData] = useState([]);
  const [loadingVenues, setLoading] = useState(true);
  const [errorVenues, setError] = useState<null | string>(null);

  useEffect(() => {
    if (sourceEcId == null || destinationEcId == null) return;
    console.log("Fetching venues for ECs:", {
      sourceEcId,
      destinationEcId,
    });

    const parseVenueResponse = (json) => {
        if (Array.isArray(json)) return json;
        if (Array.isArray(json?.data)) return json.data;
        return [];
      };

    async function fetchVenues() {
      setLoading(true);
      try {
        const [activeRes, secondaryRes] = await Promise.all([
            fetch(`/api/r1a/venues/${sourceEcId}`, { credentials: "include" }),
            fetch(`/api/r1b/venues/${destinationEcId}`, { credentials: "include" })
        ]);
        const [activeJson, secondaryJson] = await Promise.all([
          activeRes.json(),
          secondaryRes.json()
        ]);
        //console.log("Active Venue Response:", activeJson);
        setSourceVenueData(parseVenueResponse(activeJson));
        setDestinationVenueData(parseVenueResponse(secondaryJson));
        
        console.log("✅ Parsed venue data:", {
            sourceEcId,
            destinationEcId,
            sourceVenueData: parseVenueResponse(activeJson),
            destinationVenueData: parseVenueResponse(secondaryJson),
          });
        //setSourceVenueData(activeJson.data || []);
        //setDestinationVenueData(secondaryJson.data || []);
        //console.log("Source Venue Data:", activeJson.data);
        //console.log("Destination Venue Data:", secondaryJson.data);
      } catch (err) {
        console.error("Failed to fetch EC data:", err);
        setError("Failed to load EC data.");
      } finally {
        setLoading(false);
      }
    }

    fetchVenues();
  }, [sourceEcId, destinationEcId]);

  return { sourceVenueData, destinationVenueData, loadingVenues, errorVenues };
}
