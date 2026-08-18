import type { RssiTier } from "./mapColors";

export interface MapClient {
  mac_address: string;
  hostname: string | null;
  ip_address: string | null;
  os_type: string | null;
  device_type: string | null;
  ssid: string | null;
  vlan: number | null;
  band: string | null;
  channel: number | null;
  rssi: number | null;
  snr: number | null;
  noise_floor: number | null;
  health: string | null;
  tier: RssiTier;
  signal_fields_swapped: boolean;
  connected_time: string | null;
  total_traffic: number | null;
  /** Modelled from RSSI — a radius, not a position. */
  estimated_distance_m: number | null;
  /** Stable but arbitrary: spreads dots around the AP, carries no direction. */
  bearing_deg: number;
}

export interface RssiStats {
  count: number;
  min: number | null;
  max: number | null;
  mean: number | null;
  median: number | null;
  p10: number | null;
  p90: number | null;
  tiers: Record<string, number>;
}

export interface MapRadio {
  band: string | null;
  channel: number | null;
  channel_bandwidth: string | null;
  tx_power_dbm: number | null;
}

export interface MapAp {
  serial_number: string;
  name: string;
  model: string | null;
  status: string | null;
  ap_group_id: string | null;
  x_percent: number;
  y_percent: number;
  reported_client_count: number | null;
  radios: MapRadio[];
  clients: MapClient[];
  client_count: number;
  rssi_stats: RssiStats;
  bands: Record<string, number>;
  ssids: Record<string, number>;
  /** Percentile of observed client distances — where this AP's clients are. */
  cell_radius_m: number | null;
  median_distance_m: number | null;
}

export interface FloorplanScale {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  distance_m: number;
  distance_ft: number;
}

export interface FloorplanSummary {
  id: string;
  name: string | null;
  floor_number: number | null;
  image_id: string | null;
  image_name?: string | null;
  ap_count?: number;
  scale: FloorplanScale | null;
  calibrated: boolean;
}

export interface LiveOverlay {
  venue_id: string;
  floorplan: FloorplanSummary;
  aps: MapAp[];
  summary: {
    ap_count: number;
    client_count: number;
    venue_client_count: number;
    rssi: RssiStats;
  };
  model: {
    path_loss_exponent: number;
    client_tx_power_dbm: number;
    cell_percentile: number;
    tiers: { name: string; floor_dbm: number | null }[];
  };
  warnings: string[];
}

export type OverlayMode = "health" | "density";

export interface MapLayers {
  cells: boolean;
  clients: boolean;
  labels: boolean;
}

/** A client resolved to a pixel position on the rendered plan. */
export interface PlacedClient {
  client: MapClient;
  ap: MapAp;
  x: number;
  y: number;
}
