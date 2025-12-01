/**
 * PHY rate calculations and MCS tables for Wi-Fi performance analysis
 * Supports Wi-Fi 4/5/6/6E/7 across 2.4GHz, 5GHz, and 6GHz bands
 */

import type { McsTableEntry, PhyRateParams } from '@/types/speedExplainer';

/**
 * Wi-Fi 5 (802.11ac) MCS Table - 5GHz only
 * Rates in Mbps for 800ns GI (long guard interval)
 * Per spatial stream rates
 */
export const WIFI5_MCS_TABLE: McsTableEntry[] = [
  { mcs: 0, modulation: 'BPSK', codingRate: '1/2', spatialStreams: 1, ratesMbps: { '20MHz': 6.5, '40MHz': 13.5, '80MHz': 29.3, '160MHz': 58.5 } },
  { mcs: 1, modulation: 'QPSK', codingRate: '1/2', spatialStreams: 1, ratesMbps: { '20MHz': 13, '40MHz': 27, '80MHz': 58.5, '160MHz': 117 } },
  { mcs: 2, modulation: 'QPSK', codingRate: '3/4', spatialStreams: 1, ratesMbps: { '20MHz': 19.5, '40MHz': 40.5, '80MHz': 87.8, '160MHz': 175.5 } },
  { mcs: 3, modulation: '16-QAM', codingRate: '1/2', spatialStreams: 1, ratesMbps: { '20MHz': 26, '40MHz': 54, '80MHz': 117, '160MHz': 234 } },
  { mcs: 4, modulation: '16-QAM', codingRate: '3/4', spatialStreams: 1, ratesMbps: { '20MHz': 39, '40MHz': 81, '80MHz': 175.5, '160MHz': 351 } },
  { mcs: 5, modulation: '64-QAM', codingRate: '2/3', spatialStreams: 1, ratesMbps: { '20MHz': 52, '40MHz': 108, '80MHz': 234, '160MHz': 468 } },
  { mcs: 6, modulation: '64-QAM', codingRate: '3/4', spatialStreams: 1, ratesMbps: { '20MHz': 58.5, '40MHz': 121.5, '80MHz': 263.3, '160MHz': 526.5 } },
  { mcs: 7, modulation: '64-QAM', codingRate: '5/6', spatialStreams: 1, ratesMbps: { '20MHz': 65, '40MHz': 135, '80MHz': 292.5, '160MHz': 585 } },
  { mcs: 8, modulation: '256-QAM', codingRate: '3/4', spatialStreams: 1, ratesMbps: { '20MHz': 78, '40MHz': 162, '80MHz': 351, '160MHz': 702 } },
  { mcs: 9, modulation: '256-QAM', codingRate: '5/6', spatialStreams: 1, ratesMbps: { '20MHz': 86.7, '40MHz': 180, '80MHz': 390, '160MHz': 780 } },
];

/**
 * Wi-Fi 6/6E (802.11ax) MCS Table - 2.4GHz, 5GHz, 6GHz
 * Rates in Mbps for 3.2us GI (long guard interval)
 * Per spatial stream rates
 * Note: Wi-Fi 6E just adds 6GHz band access, same MCS table
 */
export const WIFI6_MCS_TABLE: McsTableEntry[] = [
  { mcs: 0, modulation: 'BPSK', codingRate: '1/2', spatialStreams: 1, ratesMbps: { '20MHz': 8.6, '40MHz': 17.2, '80MHz': 36, '160MHz': 72.1 } },
  { mcs: 1, modulation: 'QPSK', codingRate: '1/2', spatialStreams: 1, ratesMbps: { '20MHz': 17.2, '40MHz': 34.4, '80MHz': 72.1, '160MHz': 144.1 } },
  { mcs: 2, modulation: 'QPSK', codingRate: '3/4', spatialStreams: 1, ratesMbps: { '20MHz': 25.8, '40MHz': 51.5, '80MHz': 108.1, '160MHz': 216.2 } },
  { mcs: 3, modulation: '16-QAM', codingRate: '1/2', spatialStreams: 1, ratesMbps: { '20MHz': 34.4, '40MHz': 68.8, '80MHz': 144.1, '160MHz': 288.2 } },
  { mcs: 4, modulation: '16-QAM', codingRate: '3/4', spatialStreams: 1, ratesMbps: { '20MHz': 51.5, '40MHz': 103.1, '80MHz': 216.2, '160MHz': 432.4 } },
  { mcs: 5, modulation: '64-QAM', codingRate: '2/3', spatialStreams: 1, ratesMbps: { '20MHz': 68.8, '40MHz': 137.5, '80MHz': 288.2, '160MHz': 576.5 } },
  { mcs: 6, modulation: '64-QAM', codingRate: '3/4', spatialStreams: 1, ratesMbps: { '20MHz': 77.4, '40MHz': 154.9, '80MHz': 324.3, '160MHz': 648.5 } },
  { mcs: 7, modulation: '64-QAM', codingRate: '5/6', spatialStreams: 1, ratesMbps: { '20MHz': 86, '40MHz': 172.3, '80MHz': 360.3, '160MHz': 720.6 } },
  { mcs: 8, modulation: '256-QAM', codingRate: '3/4', spatialStreams: 1, ratesMbps: { '20MHz': 103.2, '40MHz': 206.5, '80MHz': 432.4, '160MHz': 864.7 } },
  { mcs: 9, modulation: '256-QAM', codingRate: '5/6', spatialStreams: 1, ratesMbps: { '20MHz': 114.7, '40MHz': 229.4, '80MHz': 480.4, '160MHz': 960.8 } },
  { mcs: 10, modulation: '1024-QAM', codingRate: '3/4', spatialStreams: 1, ratesMbps: { '20MHz': 129, '40MHz': 258.1, '80MHz': 540.4, '160MHz': 1080.9 } },
  { mcs: 11, modulation: '1024-QAM', codingRate: '5/6', spatialStreams: 1, ratesMbps: { '20MHz': 143.4, '40MHz': 286.8, '80MHz': 600.5, '160MHz': 1201 } },
];

/**
 * Wi-Fi 7 (802.11be) MCS Table - 2.4GHz, 5GHz, 6GHz
 * Rates in Mbps for 3.2us GI (long guard interval)
 * Per spatial stream rates
 * Supports up to 320MHz channels (6GHz only)
 */
export const WIFI7_MCS_TABLE: McsTableEntry[] = [
  { mcs: 0, modulation: 'BPSK', codingRate: '1/2', spatialStreams: 1, ratesMbps: { '20MHz': 8.6, '40MHz': 17.2, '80MHz': 36, '160MHz': 72.1, '320MHz': 144.1 } },
  { mcs: 1, modulation: 'QPSK', codingRate: '1/2', spatialStreams: 1, ratesMbps: { '20MHz': 17.2, '40MHz': 34.4, '80MHz': 72.1, '160MHz': 144.1, '320MHz': 288.2 } },
  { mcs: 2, modulation: 'QPSK', codingRate: '3/4', spatialStreams: 1, ratesMbps: { '20MHz': 25.8, '40MHz': 51.5, '80MHz': 108.1, '160MHz': 216.2, '320MHz': 432.4 } },
  { mcs: 3, modulation: '16-QAM', codingRate: '1/2', spatialStreams: 1, ratesMbps: { '20MHz': 34.4, '40MHz': 68.8, '80MHz': 144.1, '160MHz': 288.2, '320MHz': 576.5 } },
  { mcs: 4, modulation: '16-QAM', codingRate: '3/4', spatialStreams: 1, ratesMbps: { '20MHz': 51.5, '40MHz': 103.1, '80MHz': 216.2, '160MHz': 432.4, '320MHz': 864.7 } },
  { mcs: 5, modulation: '64-QAM', codingRate: '2/3', spatialStreams: 1, ratesMbps: { '20MHz': 68.8, '40MHz': 137.5, '80MHz': 288.2, '160MHz': 576.5, '320MHz': 1152.9 } },
  { mcs: 6, modulation: '64-QAM', codingRate: '3/4', spatialStreams: 1, ratesMbps: { '20MHz': 77.4, '40MHz': 154.9, '80MHz': 324.3, '160MHz': 648.5, '320MHz': 1297.1 } },
  { mcs: 7, modulation: '64-QAM', codingRate: '5/6', spatialStreams: 1, ratesMbps: { '20MHz': 86, '40MHz': 172.3, '80MHz': 360.3, '160MHz': 720.6, '320MHz': 1441.2 } },
  { mcs: 8, modulation: '256-QAM', codingRate: '3/4', spatialStreams: 1, ratesMbps: { '20MHz': 103.2, '40MHz': 206.5, '80MHz': 432.4, '160MHz': 864.7, '320MHz': 1729.4 } },
  { mcs: 9, modulation: '256-QAM', codingRate: '5/6', spatialStreams: 1, ratesMbps: { '20MHz': 114.7, '40MHz': 229.4, '80MHz': 480.4, '160MHz': 960.8, '320MHz': 1921.6 } },
  { mcs: 10, modulation: '1024-QAM', codingRate: '3/4', spatialStreams: 1, ratesMbps: { '20MHz': 129, '40MHz': 258.1, '80MHz': 540.4, '160MHz': 1080.9, '320MHz': 2161.8 } },
  { mcs: 11, modulation: '1024-QAM', codingRate: '5/6', spatialStreams: 1, ratesMbps: { '20MHz': 143.4, '40MHz': 286.8, '80MHz': 600.5, '160MHz': 1201, '320MHz': 2402 } },
  { mcs: 12, modulation: '4096-QAM', codingRate: '3/4', spatialStreams: 1, ratesMbps: { '20MHz': 154.9, '40MHz': 309.7, '80MHz': 648.5, '160MHz': 1297.1, '320MHz': 2594.1 } },
  { mcs: 13, modulation: '4096-QAM', codingRate: '5/6', spatialStreams: 1, ratesMbps: { '20MHz': 172.1, '40MHz': 344.1, '80MHz': 720.6, '160MHz': 1441.2, '320MHz': 2882.4 } },
];

/**
 * Calculate theoretical PHY rate
 */
export function calculatePhyRate(params: PhyRateParams): number {
  const { mcs, streams, widthMhz, guardInterval, wifiGeneration } = params;

  // Get base rate for 1 spatial stream
  let baseRate = 0;
  let mcsTable: McsTableEntry[] = [];

  // Select appropriate MCS table
  if (wifiGeneration === 5) {
    mcsTable = WIFI5_MCS_TABLE;
  } else if (wifiGeneration === 6) {
    mcsTable = WIFI6_MCS_TABLE;
  } else if (wifiGeneration === 7) {
    mcsTable = WIFI7_MCS_TABLE;
  } else if (wifiGeneration === 4) {
    // Wi-Fi 4 (802.11n) - simplified, use similar to Wi-Fi 5 but cap at MCS 7
    mcsTable = WIFI5_MCS_TABLE.slice(0, 8);
  }

  const mcsEntry = mcsTable[mcs];
  if (!mcsEntry) return 0;

  const widthKey = `${widthMhz}MHz` as keyof typeof mcsEntry.ratesMbps;
  baseRate = mcsEntry.ratesMbps[widthKey] || 0;

  // Multiply by spatial streams
  const phyRate = baseRate * streams;

  // Short GI gives boost
  // Wi-Fi 5: 400ns vs 800ns = ~11% boost
  // Wi-Fi 6/7: 0.8us vs 3.2us = ~11-14% boost (we'll use ~13%)
  let giMultiplier = 1.0;
  if (guardInterval === 'short') {
    if (wifiGeneration === 5 || wifiGeneration === 4) {
      giMultiplier = 1.11; // 400ns vs 800ns
    } else if (wifiGeneration === 6 || wifiGeneration === 7) {
      giMultiplier = 1.13; // 0.8us vs 3.2us
    }
  }

  return Math.round(phyRate * giMultiplier);
}

/**
 * Calculate realistic throughput from PHY rate
 * Accounts for MAC overhead, ACKs, contention, etc.
 */
export function calculateRealisticThroughput(
  phyRate: number,
  airtimeUtilization: number = 50,
  retryRate: number = 5
): number {
  // Base efficiency (MAC overhead, ACKs, etc.)
  // Wi-Fi 6/7 is slightly more efficient than Wi-Fi 5 due to better frame aggregation
  const baseEfficiency = 0.65; // ~65% is typical best case

  // Airtime factor: higher utilization = less usable capacity per client
  const airtimeFactor = Math.max(0.2, 1 - (airtimeUtilization / 100) * 0.6);

  // Retry penalty
  const retryFactor = Math.max(0.5, 1 - (retryRate / 100) * 0.8);

  const realisticThroughput = phyRate * baseEfficiency * airtimeFactor * retryFactor;

  return Math.round(realisticThroughput);
}

/**
 * Get MCS details by index
 */
export function getMcsDetails(mcs: number, wifiGen: 4 | 5 | 6 | 7 = 6): McsTableEntry | null {
  if (wifiGen === 5 || wifiGen === 4) {
    return WIFI5_MCS_TABLE[mcs] || null;
  } else if (wifiGen === 6) {
    return WIFI6_MCS_TABLE[mcs] || null;
  } else if (wifiGen === 7) {
    return WIFI7_MCS_TABLE[mcs] || null;
  }
  return null;
}

/**
 * Determine most common MCS from histogram
 */
export function getMostCommonMcs(mcsHistogram: Record<string, number>): number {
  let maxCount = 0;
  let mostCommonMcs = 0;

  Object.entries(mcsHistogram).forEach(([mcs, count]) => {
    if (count > maxCount) {
      maxCount = count;
      mostCommonMcs = parseInt(mcs);
    }
  });

  return mostCommonMcs;
}

/**
 * Calculate average MCS weighted by count
 */
export function getAverageMcs(mcsHistogram: Record<string, number>): number {
  let totalCount = 0;
  let weightedSum = 0;

  Object.entries(mcsHistogram).forEach(([mcs, count]) => {
    totalCount += count;
    weightedSum += parseInt(mcs) * count;
  });

  return totalCount > 0 ? Math.round(weightedSum / totalCount) : 0;
}

/**
 * Categorize link quality based on MCS distribution
 * Adjusts thresholds based on Wi-Fi generation
 */
export function categorizeLinkQuality(
  mcsHistogram: Record<string, number>,
  wifiGen: 4 | 5 | 6 | 7 = 6
): {
  category: 'excellent' | 'good' | 'fair' | 'poor';
  description: string;
} {
  const avgMcs = getAverageMcs(mcsHistogram);

  // Wi-Fi 6/7 have MCS 10-13, so adjust thresholds
  if (wifiGen === 6 || wifiGen === 7) {
    if (avgMcs >= 10) {
      return {
        category: 'excellent',
        description: 'Very high data rates (MCS 10+). Excellent 1024-QAM or 4096-QAM link.'
      };
    } else if (avgMcs >= 8) {
      return {
        category: 'excellent',
        description: 'High data rates (MCS 8-9). Link quality is excellent.'
      };
    } else if (avgMcs >= 5) {
      return {
        category: 'good',
        description: 'Good data rates (MCS 5-7). Link quality is solid.'
      };
    } else if (avgMcs >= 3) {
      return {
        category: 'fair',
        description: 'Moderate data rates (MCS 3-4). Some signal or interference issues.'
      };
    } else {
      return {
        category: 'poor',
        description: 'Low data rates (MCS 0-2). Weak signal or significant interference.'
      };
    }
  } else {
    // Wi-Fi 4/5 (MCS 0-9)
    if (avgMcs >= 8) {
      return {
        category: 'excellent',
        description: 'High data rates (MCS 8-9). Link quality is excellent.'
      };
    } else if (avgMcs >= 5) {
      return {
        category: 'good',
        description: 'Good data rates (MCS 5-7). Link quality is solid.'
      };
    } else if (avgMcs >= 3) {
      return {
        category: 'fair',
        description: 'Moderate data rates (MCS 3-4). Some signal or interference issues.'
      };
    } else {
      return {
        category: 'poor',
        description: 'Low data rates (MCS 0-2). Weak signal or significant interference.'
      };
    }
  }
}

/**
 * Estimate max realistic throughput for a client
 */
export function estimateClientMaxThroughput(
  streams: number,
  widthMhz: 20 | 40 | 80 | 160 | 320,
  wifiGen: 4 | 5 | 6 | 7,
  airtimeUtilization: number = 50
): { min: number; max: number; typical: number } {
  // Best case: Highest MCS with short GI
  const bestMcs = wifiGen === 7 ? 13 : wifiGen === 6 ? 11 : 9;
  const bestPhyRate = calculatePhyRate({
    mcs: bestMcs,
    streams,
    widthMhz,
    guardInterval: 'short',
    wifiGeneration: wifiGen
  });

  // Worst case: MCS 5 with long GI
  const worstPhyRate = calculatePhyRate({
    mcs: 5,
    streams,
    widthMhz,
    guardInterval: 'long',
    wifiGeneration: wifiGen
  });

  const maxThroughput = calculateRealisticThroughput(bestPhyRate, airtimeUtilization, 5);
  const minThroughput = calculateRealisticThroughput(worstPhyRate, airtimeUtilization, 15);
  const typicalThroughput = Math.round((maxThroughput + minThroughput) / 2);

  return { min: minThroughput, max: maxThroughput, typical: typicalThroughput };
}

/**
 * Get band capabilities - what channel widths are realistically available
 */
export function getBandCapabilities(band: '2.4GHz' | '5GHz' | '6GHz'): {
  maxWidth: 20 | 40 | 80 | 160 | 320;
  typicalWidths: number[];
  description: string;
} {
  if (band === '2.4GHz') {
    return {
      maxWidth: 40,
      typicalWidths: [20, 40],
      description: '2.4GHz is congested and typically limited to 20/40MHz. Best for range, not speed.'
    };
  } else if (band === '5GHz') {
    return {
      maxWidth: 160,
      typicalWidths: [20, 40, 80, 160],
      description: '5GHz supports up to 160MHz channels with good performance. Best balance of range and speed.'
    };
  } else {
    return {
      maxWidth: 320,
      typicalWidths: [20, 40, 80, 160, 320],
      description: '6GHz supports up to 320MHz (Wi-Fi 7) with very clean spectrum. Best for maximum speed.'
    };
  }
}

/**
 * Format band and capabilities for display
 */
export function formatBandInfo(
  band: '2.4GHz' | '5GHz' | '6GHz',
  widthMhz: number
): string {
  const emoji = band === '2.4GHz' ? '📻' : band === '5GHz' ? '📡' : '🚀';
  return `${emoji} ${band} @ ${widthMhz}MHz`;
}
