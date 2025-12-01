/**
 * Speed Diagnostics Data Contract
 *
 * This defines the standard format for speed diagnostic data.
 * Both mock data (from JSON files) and real API responses must conform to this structure.
 */

export type ScopeType = 'client' | 'ap' | 'ssid';
export type TimeWindow = '15min' | '1hour' | '24hours';
export type ScoreLevel = 'excellent' | 'good' | 'fair' | 'poor';
export type BottleneckType = 'signal' | 'airtime' | 'interference' | 'backhaul' | 'client' | 'unknown';
export type SeverityLevel = 'low' | 'medium' | 'high';
export type StatusLevel = 'good' | 'fair' | 'poor';
export type UplinkType = 'copper' | 'fiber' | 'wireless' | 'unknown';
export type WifiGeneration = 'Wi-Fi 4' | 'Wi-Fi 5' | 'Wi-Fi 6' | 'Wi-Fi 6E' | 'Wi-Fi 7';

export interface SpeedExplainerContext {
  scopeType: ScopeType;
  scopeId: string | null;
  scopeName: string | null;
  timeWindow: TimeWindow;
}

export interface QuickSignal {
  status: StatusLevel;
  detail: string;
}

export interface PrimaryBottleneck {
  type: BottleneckType;
  severity: SeverityLevel;
  description: string;
  recommendation: string;
}

export interface DiagnosticSummary {
  score: ScoreLevel;
  scoreValue: number; // 0-100
  primaryBottleneck: PrimaryBottleneck;
  quickSignals: {
    signal: QuickSignal;
    wifiLoad: QuickSignal;
    retries: QuickSignal;
    backhaul: QuickSignal;
  };
  tldr: string;
}

export interface LinkQuality {
  rssi: number; // dBm
  snr: number; // dB
  mcsMode: string; // e.g., "MCS 7 / 2SS @ 80 MHz"
  mcsHistogram: Record<string, number>; // MCS index to packet count
  diagnosis: string;
  isBottleneck: boolean;
}

export interface PhyVsReal {
  expectedPhyCeiling: number; // Mbps
  actualThroughputBest: number; // Mbps
  efficiency: number; // percentage (0-100)
  airtimeUtilization: number; // percentage (0-100)
  airtimeBreakdown: {
    data: number;
    management: number;
    retries: number;
    other: number;
  };
  clientsOnRadio: number;
  avgPerClientAirtime: number; // percentage
}

export interface Interference {
  retryRate: number; // percentage (0-100)
  failureRate: number; // percentage (0-100)
  currentChannel: number;
  channelWidth: number; // MHz
  neighborApCount: number;
  recentDfsEvents: number;
  noiseFloor: number; // dBm
  diagnosis: string;
  isBottleneck: boolean;
}

export interface Backhaul {
  wanCapacityEstimate: number; // Mbps
  wanUsagePeak: number; // Mbps
  wanUsageAvg: number; // Mbps
  uplinkType: UplinkType;
  uplinkSpeed: number; // Mbps
  diagnosis: string;
  isBottleneck: boolean;
}

export interface ClientLimitations {
  streams: number; // spatial streams
  maxWidthMhz: 20 | 40 | 80 | 160;
  wifiGeneration: WifiGeneration;
  maxRealisticThroughput: number; // Mbps
  deviceProfile: string;
  diagnosis: string;
  isBottleneck: boolean;
}

export interface DiagnosticData {
  summary: DiagnosticSummary;
  linkQuality: LinkQuality;
  phyVsReal: PhyVsReal;
  interference: Interference;
  backhaul: Backhaul;
  clientLimitations: ClientLimitations;
}

export interface MockScenario {
  id: string;
  name: string;
  description: string;
  context: SpeedExplainerContext;
  data: DiagnosticData;
}

export interface MockScenariosFile {
  scenarios: MockScenario[];
}
