/**
 * Types for Roaming Explainer feature
 */

// Overall roaming health assessment
export type RoamingHealth = 'excellent' | 'good' | 'fair' | 'poor' | 'critical';

// Individual roaming event
export interface RoamingEvent {
  timestamp: string;
  clientMac: string;
  clientName?: string;
  fromApMac: string;
  fromApName: string;
  toApMac: string;
  toApName: string;
  roamType: 'fast' | 'full' | 'failed';
  roamTimeMs: number;
  rssiAtRoam: number;
  reason: 'signal_quality' | 'load_balance' | 'band_steer' | 'client_initiated' | 'ap_disconnect';
}

// Sticky client instance
export interface StickyClient {
  clientMac: string;
  clientName?: string;
  clientType: string; // "iPhone 14", "Galaxy S23", "Ring Doorbell", etc.
  connectedApMac: string;
  connectedApName: string;
  connectedApLocation?: string;
  currentRssi: number;
  betterApMac?: string;
  betterApName?: string;
  betterApRssi?: number;
  stuckDurationMinutes: number;
  lastRoamAttempt?: string;
}

// AP coverage info for floor plan visualization
export interface ApCoverageInfo {
  apMac: string;
  apName: string;
  location: string; // "Unit 101", "Hallway 1st Floor", etc.
  channel: number;
  band: '2.4GHz' | '5GHz' | '6GHz';
  clientCount: number;
  avgRssi: number;
  coverageRadiusMeters: number;
  isOverlapping: boolean;
  overlappingAps?: string[];
}

// MDU-specific metrics
export interface MduMetrics {
  totalUnits: number;
  totalAps: number;
  hallwayAps: number;
  inUnitAps: number;
  avgApsPerFloor: number;
  avgOverlapPercent: number;
  floorBleedIssues: number;
  hallwayHuggerClients: number;
}

// 802.11k/v/r support status
export interface RoamingStandardsSupport {
  dot11k: boolean; // Neighbor Reports
  dot11v: boolean; // BSS Transition Management
  dot11r: boolean; // Fast BSS Transition
  okcEnabled: boolean; // Opportunistic Key Caching
  preauthEnabled: boolean;
}

// Roaming configuration
export interface RoamingConfig {
  minRssiThreshold: number | null; // e.g., -80 dBm
  bssMinRate: number | null; // e.g., 12 Mbps
  bandSteeringEnabled: boolean;
  loadBalancingEnabled: boolean;
  roamingStandards: RoamingStandardsSupport;
}

// Section-specific data structures
export interface WhatIsRoamingData {
  totalRoamEvents24h: number;
  successfulRoams: number;
  failedRoams: number;
  avgRoamTimeMs: number;
  fastRoamPercent: number;
  recentEvents: RoamingEvent[];
}

export interface StickyClientsData {
  totalStickyClients: number;
  stickyClients: StickyClient[];
  worstOffenders: StickyClient[];
  commonDeviceTypes: { type: string; count: number }[];
  recommendations: string[];
}

export interface MduProblemsData {
  metrics: MduMetrics;
  hallwayHuggers: StickyClient[];
  floorBleeders: StickyClient[];
  coverageMap: ApCoverageInfo[];
  problemAreas: {
    location: string;
    issue: 'overlap' | 'gap' | 'interference' | 'floor_bleed';
    severity: 'low' | 'medium' | 'high';
    description: string;
  }[];
}

export interface TroubleshootingData {
  config: RoamingConfig;
  issues: {
    category: 'config' | 'client' | 'infrastructure' | 'design';
    issue: string;
    severity: 'info' | 'warning' | 'critical';
    recommendation: string;
  }[];
  score: number; // 0-100 roaming health score
  scoreBreakdown: {
    category: string;
    score: number;
    maxScore: number;
    notes: string;
  }[];
}

// Complete diagnostic data for a scenario
export interface RoamingDiagnosticData {
  summary: {
    health: RoamingHealth;
    score: number;
    headline: string;
    subheadline: string;
  };
  whatIsRoaming: WhatIsRoamingData;
  stickyClients: StickyClientsData;
  mduProblems: MduProblemsData;
  troubleshooting: TroubleshootingData;
}

// Demo scenario structure
export interface RoamingScenario {
  id: string;
  name: string;
  description: string;
  category: 'mdu' | 'enterprise' | 'hospitality' | 'education';
  data: RoamingDiagnosticData;
}

export interface RoamingScenariosFile {
  scenarios: RoamingScenario[];
}

// View mode for simple vs detailed
export type ViewMode = 'simple' | 'detailed';
export type DataMode = 'demo' | 'live';
