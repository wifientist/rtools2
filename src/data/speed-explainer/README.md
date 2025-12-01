# Speed Explainer Mock Data

This directory contains mock diagnostic scenarios for the Speed Explainer tool.

## File Structure

- `mock-scenarios.json` - Contains one or more diagnostic scenarios with full data
- Each scenario includes context (device name, scope, time window) and complete diagnostic data

## Data Contract

All mock data must conform to the TypeScript interfaces defined in:
- `/src/types/speedDiagnostics.ts` - Mock scenario structure
- `/src/types/speedExplainer.ts` - Diagnostic data structure (THE SOURCE OF TRUTH)

**Important:** When the real API is implemented, it should return data in the exact same format as defined in `/src/types/speedExplainer.ts`.

## How It Works

1. **Component Mount** - SpeedExplainer loads `mock-scenarios.json` on mount
2. **Demo Mode** - User can select from available scenarios via dropdown
3. **Live Mode** - Fetches real data from API endpoint: `/api/r1/{controllerId}/diagnostics/{scopeType}/{scopeId}`
4. **Data Format** - Both mock and live data use the same `DiagnosticData` interface

## Adding New Scenarios

To add a new mock scenario (e.g., poor signal, backhaul bottleneck), add to the `scenarios` array in `mock-scenarios.json`:

```json
{
  "scenarios": [
    {
      "id": "unique-id",
      "name": "Display Name",
      "description": "Brief description of the scenario",
      "context": {
        "scopeType": "client",
        "scopeId": "demo-xyz",
        "scopeName": "Demo Device Name",
        "timeWindow": "15min"
      },
      "data": {
        "summary": { ... },
        "linkQuality": { ... },
        "phyVsReal": { ... },
        "interference": { ... },
        "backhaul": { ... },
        "clientLimitations": { ... }
      }
    }
  ]
}
```

## Scenario Ideas

Potential scenarios to demonstrate different bottlenecks:

- ✅ **Moderate Congestion** (implemented) - Airtime bottleneck
- ⬜ **Poor Signal** - Weak RSSI/SNR causing low MCS
- ⬜ **Heavy Interference** - High retry/failure rates
- ⬜ **Backhaul Saturated** - WAN capacity maxed out
- ⬜ **Client Limitations** - Old Wi-Fi 4 device limiting throughput
- ⬜ **Perfect Conditions** - All green, excellent performance
- ⬜ **Multi-Factor Issue** - Combination of problems

## API Implementation Guide

When implementing the live API endpoint, ensure it returns data matching this structure:

### Endpoint
```
GET /api/r1/{controllerId}/diagnostics/{scopeType}/{scopeId}?timeWindow={timeWindow}
```

### Response Format
```typescript
{
  "summary": SpeedScoreSummary,
  "linkQuality": LinkQualityData,
  "phyVsReal": PhyVsRealData,
  "interference": InterferenceData,
  "backhaul": BackhaulData,
  "clientLimitations": ClientLimitationsData
}
```

See `/src/types/speedExplainer.ts` for complete interface definitions.

### Data Sources

The backend should aggregate data from:
- **Client Session Data** - RSSI, SNR, MCS histogram, retry/failure rates
- **Radio Stats** - Airtime utilization, channel info, client count
- **Backhaul Stats** - WAN capacity, usage peaks, uplink type
- **Client Capabilities** - Spatial streams, channel width, Wi-Fi generation

### Calculation Logic

The API should calculate:
1. **Speed Score** (0-100) based on all factors
2. **Primary Bottleneck** by identifying the weakest link
3. **Diagnoses** for each section with actionable recommendations
4. **isBottleneck** flags to highlight problem areas
