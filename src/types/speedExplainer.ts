/**
 * Type definitions for Speed Explainer diagnostic data
 */

export interface McsHistogram {
  [mcs: string]: number; // MCS index (0-11) -> packet count
}

export interface ClientSession {
  clientMac: string;
  apMac: string;
  ssid: string;
  band: '2.4GHz' | '5GHz' | '6GHz';
  rssiAvg: number;
  snrAvg: number;
  mcsHistogram: McsHistogram;
  txBytes: number;
  rxBytes: number;
  retries: number;
  failures: number;
  timespanStart: string;
  timespanEnd: string;
}

export interface AirtimeBreakdown {
  data: number;      // Percentage
  management: number;
  retries: number;
  other: number;
}

export interface RadioStats {
  apMac: string;
  band: '2.4GHz' | '5GHz' | '6GHz';
  channel: number;
  widthMhz: 20 | 40 | 80 | 160 | 320;
  airtimeUtilizationTotal: number; // Percentage
  airtimeBreakdown: AirtimeBreakdown;
  clientsConnected: number;
}

export interface BackhaulStats {
  apMac?: string;
  siteId?: string;
  wanCapacityEstimate: number; // Mbps
  wanUsagePeak: number;        // Mbps
  wanUsageAvg: number;          // Mbps
  uplinkType: 'copper' | 'fiber' | 'wireless' | 'unknown';
  uplinkSpeed: number;          // Mbps
}

export interface ClientCapabilities {
  clientMac?: string;
  deviceProfile?: string;
  streams: number;              // 1x1, 2x2, 4x4, etc.
  maxWidthMhz: 20 | 40 | 80 | 160 | 320;
  wifiGeneration: 'Wi-Fi 4' | 'Wi-Fi 5' | 'Wi-Fi 6' | 'Wi-Fi 6E' | 'Wi-Fi 7';
  notes?: string;
}

export interface SpeedBottleneck {
  type: 'signal' | 'airtime' | 'interference' | 'backhaul' | 'client' | 'unknown';
  severity: 'low' | 'medium' | 'high';
  description: string;
  recommendation: string;
}

export interface SpeedScoreSummary {
  score: 'excellent' | 'good' | 'fair' | 'poor';
  scoreValue: number; // 0-100
  primaryBottleneck: SpeedBottleneck;
  quickSignals: {
    signal: { status: 'good' | 'fair' | 'poor'; detail: string };
    wifiLoad: { status: 'good' | 'fair' | 'poor'; detail: string };
    retries: { status: 'good' | 'fair' | 'poor'; detail: string };
    backhaul: { status: 'good' | 'fair' | 'poor'; detail: string };
  };
  tldr: string;
}

export interface LinkQualityData {
  rssi: number;
  snr: number;
  mcsMode: string; // e.g., "MCS 7 / 2SS @ 80 MHz"
  mcsHistogram: McsHistogram;
  diagnosis: string;
  isBottleneck: boolean;
}

export interface PhyVsRealData {
  expectedPhyCeiling: number;  // Mbps
  actualThroughputBest: number; // Mbps
  efficiency: number;           // Percentage
  airtimeUtilization: number;   // Percentage
  airtimeBreakdown: AirtimeBreakdown;
  clientsOnRadio: number;
  avgPerClientAirtime: number;
}

export interface InterferenceData {
  retryRate: number;           // Percentage
  failureRate: number;         // Percentage
  currentChannel: number;
  channelWidth: number;
  neighborApCount: number;
  recentDfsEvents: number;
  noiseFloor: number;          // dBm
  diagnosis: string;
  isBottleneck: boolean;
}

export interface BackhaulData extends BackhaulStats {
  diagnosis: string;
  isBottleneck: boolean;
}

export interface ClientLimitationsData extends ClientCapabilities {
  maxRealisticThroughput: number; // Mbps
  diagnosis: string;
  isBottleneck: boolean;
}

export interface DiagnosticData {
  summary: SpeedScoreSummary;
  linkQuality: LinkQualityData;
  phyVsReal: PhyVsRealData;
  interference: InterferenceData;
  backhaul: BackhaulData;
  clientLimitations: ClientLimitationsData;
}

/**
 * PHY Rate calculation helpers
 */
export interface PhyRateParams {
  mcs: number;
  streams: number;
  widthMhz: 20 | 40 | 80 | 160 | 320;
  guardInterval: 'short' | 'long'; // 400ns/800ns for Wi-Fi 5/6, or 0.8us/1.6us/3.2us for Wi-Fi 6/7
  wifiGeneration: 4 | 5 | 6 | 7;
  band?: '2.4GHz' | '5GHz' | '6GHz'; // Important for understanding capabilities
}

/**
 * MCS Table entry
 */
export interface McsTableEntry {
  mcs: number;
  modulation: string;        // e.g., "256-QAM", "1024-QAM", "4096-QAM"
  codingRate: string;        // e.g., "5/6"
  spatialStreams: number;
  ratesMbps: {
    '20MHz': number;
    '40MHz': number;
    '80MHz': number;
    '160MHz'?: number;
    '320MHz'?: number;  // Wi-Fi 7 only
  };
}
