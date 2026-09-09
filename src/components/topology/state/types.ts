/**
 * Hand-written mirror of the backend model.
 *
 * Deliberately hand-written rather than generated: the backend emits camelCase
 * dicts with no schema, and a wrong-but-plausible type here would be worse than
 * none. Every field below has been observed in a real /graph response.
 *
 * `verbatimModuleSyntax` is on, so every import of these must be `import type`.
 */

export const TIERS = [
  "rejected",
  "weak",
  "probable",
  "strong",
  "confirmed",
] as const;
export type Tier = (typeof TIERS)[number];

export const TIER_RANK: Record<Tier, number> = {
  rejected: 0,
  weak: 1,
  probable: 2,
  strong: 3,
  confirmed: 4,
};

export type DeviceKind =
  | "switch"
  | "stack"
  | "ap"
  | "client"
  | "external"
  | "wan"
  | "unknown";

export type LinkKind =
  | "ethernet"
  | "lag"
  | "stack"
  | "mesh"
  | "wireless-client"
  | "wan";

/** How many INDEPENDENT devices saw a link — not how many rows mention it. */
export type Directionality =
  | "bidirectional"
  | "a-only"
  | "b-only"
  | "derived"
  | "none";

export type Device = {
  id: string;
  kind: DeviceKind;
  name: string;
  displayName: string;
  venueId: string;
  venueName: string;
  mac: string;
  serial: string;
  model: string;
  ip: string;
  status: "online" | "offline" | "pending" | "degraded" | "unknown";
  rawStatus: string;
  role: string;
  managed: boolean;
  portIds: string[];
  counts: Record<string, number>;
  attrs: Record<string, unknown>;
  stack?: { activeSerial: string; units: Record<string, unknown>[] };
  floorplan?: { floorplanId: string; xPercent?: number; yPercent?: number };
};

export type Endpoint = {
  deviceId: string;
  portId: string | null;
  ident: string | null;
  resolvedVia: string;
  discoveredAs: Record<string, unknown>;
};

export type EvidenceRow = {
  id: string;
  source: string;
  kind: "support" | "contradict" | "context";
  claim: string;
  weight: number;
  baseWeight: number;
  a: string | null;
  b: string | null;
  fields: Record<string, unknown>;
  observedAt: string;
};

export type Link = {
  id: string;
  a: Endpoint;
  b: Endpoint;
  kind: LinkKind;
  logicalOf: string | null;
  members: string[];
  score: number;
  confidence: number;
  tier: Tier;
  tierReason: string;
  directionality: Directionality;
  userAsserted: boolean;
  evidenceSummary: {
    total: number;
    support: number;
    contradict: number;
    context: number;
    sources: string[];
  };
  attrs: Record<string, unknown>;
  override?: {
    key: string;
    verdict: string;
    by: string;
    at: string;
    note: string;
  };
  /**
   * What the engine concluded BEFORE a human verdict was applied. Present only
   * on overridden links, so the panel can show the disagreement rather than let
   * the override quietly stand in for evidence.
   */
  machine?: {
    tier: Tier;
    score: number;
    confidence: number;
    tierReason: string;
  };
};

export type Port = {
  id: string;
  deviceId: string;
  ident: string;
  label: string;
  r1PortId: string;
  portMac: string;
  unit: number | null;
  kind: string;
  adminUp: boolean | null;
  operUp: boolean | null;
  speedMbps: number | null;
  untaggedVlan: number | null;
  taggedVlans: number[];
  lagId: string | null;
  lagKey: string | null;
  stackCapable: boolean;
  stackPeerIdent: string;
  isCloudPort: boolean;
  stpState: string;
  macCount: number;
  neighborName: string;
  neighborMac: string;
  neighborPortMac: string;
  poe: Record<string, unknown>;
  attrs: Record<string, unknown>;
};

export type SnapshotMeta = {
  takenAt: string;
  takenAtEpoch: number;
  tenantId: string;
  scopeVenueIds: string[];
  venues: Record<string, string>;
  counts: Record<string, number>;
  sources: {
    id: string;
    status: string;
    rows: number;
    elapsedMs: number;
    error?: string;
    note?: string;
  }[];
  completeness: {
    queries?: number;
    incomplete?: number;
    expected?: number;
    collected?: number;
    shortfalls?: unknown[];
  };
  warnings: string[];
  elapsedSeconds: number;
  deep: boolean;
  correlation?: {
    claims: number;
    links: number;
    byTier: Record<string, number>;
    byKind: Record<string, number>;
    byDirectionality: Record<string, number>;
    externalDevices: number;
    outOfScopeDevices: number;
    unattachedAps: number;
    elapsedSeconds: number;
    sourcesRun: { id: string; claims: number }[];
    sourcesFailed: { id: string; error: string }[];
    sourcesSkipped?: { id: string; reason?: string }[];
    /** A name that resolved to more than one device, so it resolved to none. */
    aliasCollisions?: { kind: string; key: string; held: string; rejected: string }[];
  };
};

export type SnapshotRow = {
  name: string;
  takenAt: string;
  takenAtEpoch: number;
  counts: Record<string, number>;
  deep: boolean;
  warnings: string[];
  sizeBytes: number;
  expiresAtEpoch: number;
};

/** What GET /links/{id}/evidence returns. */
export type EvidenceResponse = {
  evidence: EvidenceRow[];
  arithmetic: {
    lines: {
      source: string;
      kind: "support" | "contradict" | "context";
      claim: string;
      weight: number;
      running: number;
    }[];
    total: number;
    confidence: number;
  };
  tierReason: string;
};

/** One entry in GET /sources. */
export type SourceInfo = {
  id: string;
  label: string;
  tierCap: string | null;
  emits: { source: string; tierCap: string | null; weight: number }[];
  proves: string;
  reads: string;
  caveats: string;
  enabledByDefault: boolean;
};

export type UplinkReason = {
  source: string;
  claim: string;
  weight: number;
  fields: Record<string, unknown>;
};

export type UplinkCandidate = {
  key: string;
  venueId: string;
  deviceId: string;
  deviceName: string;
  portIdent: string | null;
  score: number;
  reasons: UplinkReason[];
};

export type UplinkVenue = {
  venueId: string;
  venueName: string;
  confirmed: { deviceId: string; portIdent: string; by: string; at: string } | null;
  rejected: string[];
  candidates: UplinkCandidate[];
};

export type UplinkState = {
  venues: Record<string, UplinkVenue>;
  totalCandidates: number;
};

export type GraphResponse = {
  snapshot: string | null;
  meta: SnapshotMeta;
  nodes: Device[];
  links: Link[];
  wan: Record<string, unknown>;
  overrides: Record<string, unknown>;
};

export type VenueOption = {
  venueId: string;
  venueName: string;
  city: string | null;
  switches: number | null;
  aps: number | null;
  clients: number | null;
  hasSnapshot: boolean;
};

/** Which question colour is answering. "none" means hue is back to link kind. */
export type OverlayId = "none" | "loops";

/** One device the map could not fully place. See GET /topology/{cid}/findings. */
export type FindingRow = {
  deviceId: string;
  name: string;
  kind?: string;
  status?: string;
  rawStatus?: string;
  mac?: string;
  model?: string;
  serial?: string;
  venueId?: string;
  venueName?: string;
  hint?: string;
  foreignOui?: boolean;
  /** For an external peer: the managed ports that reported it. */
  seenFrom?: { device: string; deviceId: string; port: string; tier: string }[];
};

export type TopologyFindings = {
  unmanaged: FindingRow[];
  outOfScope: FindingRow[];
  unattached: FindingRow[];
  ghosts: FindingRow[];
  counts: {
    unmanaged: number;
    outOfScope: number;
    unattached: number;
    ghosts: number;
  };
};
