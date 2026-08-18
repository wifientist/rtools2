import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@/context/AuthContext";
import SingleEcSelector from "@/components/SingleEcSelector";
import SingleVenueSelector from "@/components/SingleVenueSelector";
import { apiFetch } from "@/utils/api";

import FloorplanCanvas from "@/components/maps/FloorplanCanvas";
import { ApDetail, MapLegend, MapStats, TierBar } from "@/components/maps/MapSidebar";
import { TIER_COLORS, TIER_LABELS } from "@/components/maps/mapColors";
import { applyBandFilter, availableBands } from "@/components/maps/bandFilter";
import type {
  FloorplanSummary,
  LiveOverlay,
  MapAp,
  MapLayers,
  OverlayMode,
  PlacedClient,
} from "@/components/maps/types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

const REFRESH_OPTIONS = [
  { label: "15s", value: 15 },
  { label: "30s", value: 30 },
  { label: "1m", value: 60 },
  { label: "5m", value: 300 },
];

interface TooltipState {
  x: number;
  y: number;
  title: string;
  lines: string[];
  swatch?: string;
}

export default function Maps() {
  const { activeControllerId, activeControllerType, activeControllerSubtype, controllers } =
    useAuth();

  const activeController = controllers.find((c) => c.id === activeControllerId);
  const isR1 = activeControllerType === "RuckusONE";
  const needsEcSelection = activeControllerSubtype === "MSP";

  // ── Scope selection ──────────────────────────────────────
  const [ecId, setEcId] = useState<string | null>(null);
  const [ecName, setEcName] = useState<string | null>(null);
  const [venueId, setVenueId] = useState<string | null>(null);
  const [venueName, setVenueName] = useState<string | null>(null);

  const effectiveTenantId = needsEcSelection
    ? ecId
    : activeController?.r1_tenant_id || null;

  // ── Floor plans ──────────────────────────────────────────
  const [floorplans, setFloorplans] = useState<FloorplanSummary[]>([]);
  const [unplacedApCount, setUnplacedApCount] = useState(0);
  const [planId, setPlanId] = useState<string | null>(null);
  const [plansLoading, setPlansLoading] = useState(false);

  // ── Live overlay ─────────────────────────────────────────
  const [overlay, setOverlay] = useState<LiveOverlay | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  // ── View state ───────────────────────────────────────────
  const [mode, setMode] = useState<OverlayMode>("health");
  const [layers, setLayers] = useState<MapLayers>({
    cells: true,
    clients: true,
    labels: true,
  });
  const [planOpacity, setPlanOpacity] = useState(0.45);
  const [selectedSerial, setSelectedSerial] = useState<string | null>(null);
  const [pxPerMeter, setPxPerMeter] = useState<number | null>(null);
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);
  // Tracked as the *hidden* set rather than the selected one, so a band that
  // only shows up on a later poll (a 6 GHz client finally associating) is
  // visible by default instead of silently filtered out.
  const [hiddenBands, setHiddenBands] = useState<Set<string>>(new Set());

  // ── Model parameters ─────────────────────────────────────
  const [pathLossExponent, setPathLossExponent] = useState(3.0);
  const [clientTxPower, setClientTxPower] = useState(15);
  const [cellPercentile, setCellPercentile] = useState(90);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [refreshSeconds, setRefreshSeconds] = useState(30);

  const mapWrapRef = useRef<HTMLDivElement>(null);

  // ── Reset cascade ────────────────────────────────────────
  const handleEcSelect = (id: string | null, ec: any) => {
    setEcId(id);
    setEcName(ec?.name || null);
    setVenueId(null);
    setVenueName(null);
    setFloorplans([]);
    setPlanId(null);
    setOverlay(null);
  };

  const handleVenueSelect = (id: string | null, venue: any) => {
    setVenueId(id);
    setVenueName(venue?.name || null);
    setFloorplans([]);
    setPlanId(null);
    setOverlay(null);
    setSelectedSerial(null);
    setHiddenBands(new Set());
    setError("");
  };

  // ── Load floor plans for the venue ───────────────────────
  useEffect(() => {
    if (!venueId || !activeControllerId) return;

    const load = async () => {
      setPlansLoading(true);
      setError("");
      try {
        const query = effectiveTenantId ? `?tenant_id=${effectiveTenantId}` : "";
        const response = await apiFetch(
          `${API_BASE_URL}/maps/${activeControllerId}/venues/${venueId}/floorplans${query}`
        );
        if (!response.ok) {
          const err = await response.json().catch(() => ({}));
          throw new Error(err.detail || "Failed to load floor plans");
        }
        const data = await response.json();
        setFloorplans(data.floorplans || []);
        setUnplacedApCount(data.unplaced_ap_count || 0);

        // Default to the first plan that actually has APs on it — a plan with
        // none can be rendered but has nothing to overlay.
        const withAps = (data.floorplans || []).find((p: FloorplanSummary) => (p.ap_count || 0) > 0);
        setPlanId(withAps?.id || data.floorplans?.[0]?.id || null);
      } catch (err: any) {
        setError(err.message || "Failed to load floor plans");
      } finally {
        setPlansLoading(false);
      }
    };

    load();
  }, [venueId, activeControllerId, effectiveTenantId]);

  // ── Load the live overlay ────────────────────────────────
  const fetchOverlay = useCallback(
    async (isBackground: boolean) => {
      if (!venueId || !planId || !activeControllerId) return;

      if (isBackground) setRefreshing(true);
      else setLoading(true);

      try {
        const params = new URLSearchParams({
          path_loss_exponent: String(pathLossExponent),
          client_tx_power: String(clientTxPower),
          cell_percentile: String(cellPercentile),
        });
        if (effectiveTenantId) params.set("tenant_id", effectiveTenantId);

        const response = await apiFetch(
          `${API_BASE_URL}/maps/${activeControllerId}/venues/${venueId}` +
            `/floorplans/${planId}/live?${params.toString()}`
        );
        if (!response.ok) {
          const err = await response.json().catch(() => ({}));
          throw new Error(err.detail || "Failed to load live data");
        }
        setOverlay(await response.json());
        setLastUpdated(new Date());
        setError("");
      } catch (err: any) {
        setError(err.message || "Failed to load live data");
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [
      venueId,
      planId,
      activeControllerId,
      effectiveTenantId,
      pathLossExponent,
      clientTxPower,
      cellPercentile,
    ]
  );

  // Model sliders are debounced — dragging one shouldn't fire a request per pixel.
  useEffect(() => {
    if (!planId) return;
    const timer = setTimeout(() => fetchOverlay(false), 300);
    return () => clearTimeout(timer);
  }, [fetchOverlay, planId]);

  useEffect(() => {
    if (!autoRefresh || !planId) return;
    const timer = setInterval(() => fetchOverlay(true), refreshSeconds * 1000);
    return () => clearInterval(timer);
  }, [autoRefresh, refreshSeconds, fetchOverlay, planId]);

  // ── Band filter ──────────────────────────────────────────
  const bands = useMemo(
    () => (overlay ? availableBands(overlay.aps) : []),
    [overlay]
  );

  const visibleBands = useMemo(
    () => new Set(bands.filter((band) => !hiddenBands.has(band))),
    [bands, hiddenBands]
  );

  /**
   * Filtered APs with every derived number rebuilt from the surviving clients
   * — hiding a band moves the cell radius and the medians with it, so the
   * circles always describe the clients actually on screen.
   */
  const filtered = useMemo(() => {
    if (!overlay) return null;
    return applyBandFilter(overlay.aps, visibleBands, overlay.model.cell_percentile);
  }, [overlay, visibleBands]);

  const toggleBand = (band: string) => {
    setHiddenBands((prev) => {
      const next = new Set(prev);
      if (next.has(band)) next.delete(band);
      else next.add(band);
      return next;
    });
  };

  // ── Derived ──────────────────────────────────────────────
  const selectedAp = useMemo<MapAp | null>(() => {
    if (!filtered || !selectedSerial) return null;
    return filtered.aps.find((ap) => ap.serial_number === selectedSerial) || null;
  }, [filtered, selectedSerial]);

  const tableClients = useMemo(() => {
    if (!filtered) return [];
    const source = selectedAp ? [selectedAp] : filtered.aps;
    return source
      .flatMap((ap) => ap.clients.map((client) => ({ ap, client })))
      .sort((a, b) => (a.client.rssi ?? -999) - (b.client.rssi ?? -999));
  }, [filtered, selectedAp]);

  const imageUrl = useMemo(() => {
    if (!venueId || !planId || !activeControllerId) return null;
    const query = effectiveTenantId ? `?tenant_id=${effectiveTenantId}` : "";
    return (
      `${API_BASE_URL}/maps/${activeControllerId}/venues/${venueId}` +
      `/floorplans/${planId}/image${query}`
    );
  }, [venueId, planId, activeControllerId, effectiveTenantId]);

  const currentPlan = floorplans.find((p) => p.id === planId) || null;

  // ── Tooltip handlers ─────────────────────────────────────
  const positionFor = (event: React.MouseEvent) => {
    const rect = mapWrapRef.current?.getBoundingClientRect();
    return {
      x: event.clientX - (rect?.left || 0) + 14,
      y: event.clientY - (rect?.top || 0) + 14,
    };
  };

  const handleHoverClient = useCallback(
    (placed: PlacedClient | null, event?: React.MouseEvent) => {
      if (!placed || !event) {
        setTooltip(null);
        return;
      }
      const { client, ap } = placed;
      const lines = [
        `${client.rssi ?? "—"} dBm · ${TIER_LABELS[client.tier]}`,
        `${ap.name}${client.band ? ` · ${client.band}` : ""}${
          client.channel ? ` ch ${client.channel}` : ""
        }`,
      ];
      if (client.ssid) lines.push(`SSID ${client.ssid}`);
      if (client.snr !== null) lines.push(`SNR ${client.snr} dB`);
      if (client.estimated_distance_m !== null) {
        lines.push(`~${client.estimated_distance_m.toFixed(1)} m from AP (estimated)`);
      }
      setTooltip({
        ...positionFor(event),
        title: client.hostname || client.mac_address,
        lines,
        swatch: TIER_COLORS[client.tier],
      });
    },
    []
  );

  const handleHoverAp = useCallback((ap: MapAp | null, event?: React.MouseEvent) => {
    if (!ap || !event) {
      setTooltip(null);
      return;
    }
    const lines = [
      `${ap.client_count} client${ap.client_count === 1 ? "" : "s"}`,
      ap.rssi_stats.median !== null
        ? `Median ${Math.round(ap.rssi_stats.median)} dBm`
        : "No RSSI readings",
    ];
    if (ap.cell_radius_m) lines.push(`Cell ~${ap.cell_radius_m.toFixed(1)} m`);
    lines.push(`${ap.model || "AP"} · ${ap.status || "unknown"}`);
    setTooltip({ ...positionFor(event), title: ap.name, lines });
  }, []);

  // ── Render ───────────────────────────────────────────────
  return (
    <div className="max-w-[1600px] mx-auto p-6">
      <h1 className="text-2xl font-bold mb-1">Maps</h1>
      <p className="text-sm text-gray-500 mb-5">
        Overlay live client signal on a venue floor plan. APs are drawn where
        they were placed in RUCKUS One; client positions are estimated from RSSI.
      </p>

      {error && (
        <div className="mb-4 bg-red-50 border border-red-200 rounded-lg p-3 flex items-center justify-between">
          <p className="text-sm text-red-700">{error}</p>
          <button
            onClick={() => setError("")}
            className="text-red-400 hover:text-red-600 text-xs"
          >
            dismiss
          </button>
        </div>
      )}

      {/* ============= Scope ============= */}
      <div className="bg-white rounded-lg shadow p-5 mb-5">
        <h2 className="text-lg font-semibold mb-3">1. Select Venue</h2>

        {!isR1 ? (
          <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
            <p className="text-sm text-yellow-800">
              Please select a RuckusONE controller to use this tool.
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {needsEcSelection && (
              <div>
                <div className="text-xs font-semibold text-gray-600 mb-2">
                  End Customer
                </div>
                <SingleEcSelector
                  controllerId={activeControllerId}
                  onEcSelect={handleEcSelect}
                  selectedEcId={ecId}
                />
              </div>
            )}

            {(!needsEcSelection || ecId) && (
              <SingleVenueSelector
                controllerId={activeControllerId}
                tenantId={effectiveTenantId}
                onVenueSelect={handleVenueSelect}
                selectedVenueId={venueId}
              />
            )}
          </div>
        )}

        {venueId && venueName && (
          <div className="mt-3 text-sm text-gray-600">
            Selected: <strong>{venueName}</strong>
            {ecName && <> in <strong>{ecName}</strong></>}
          </div>
        )}
      </div>

      {/* ============= Floor plan picker ============= */}
      {venueId && (
        <div className="bg-white rounded-lg shadow p-5 mb-5">
          <h2 className="text-lg font-semibold mb-3">2. Floor Plan</h2>

          {plansLoading ? (
            <p className="text-sm text-gray-500">Loading floor plans…</p>
          ) : floorplans.length === 0 ? (
            <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
              <p className="text-sm text-yellow-800">
                This venue has no floor plans in RUCKUS One. Add one in R1
                (Venue → Floor Plans) and place your APs on it, then come back.
              </p>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-3">
              <select
                value={planId || ""}
                onChange={(event) => {
                  setPlanId(event.target.value);
                  setSelectedSerial(null);
                  setHiddenBands(new Set());
                }}
                className="border border-gray-300 rounded-lg px-3 py-2 text-sm"
              >
                {floorplans.map((plan) => (
                  <option key={plan.id} value={plan.id}>
                    {plan.name || "Untitled"}
                    {plan.floor_number !== null ? ` (floor ${plan.floor_number})` : ""}
                    {` — ${plan.ap_count ?? 0} AP${plan.ap_count === 1 ? "" : "s"}`}
                    {plan.calibrated ? "" : " — no scale"}
                  </option>
                ))}
              </select>

              {unplacedApCount > 0 && (
                <span className="text-xs text-gray-500">
                  {unplacedApCount} AP{unplacedApCount === 1 ? "" : "s"} in this
                  venue {unplacedApCount === 1 ? "is" : "are"} not placed on any plan.
                </span>
              )}
            </div>
          )}
        </div>
      )}

      {/* ============= Map ============= */}
      {planId && (
        <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_340px] gap-5">
          {/* ---- Map column ---- */}
          <div className="bg-white rounded-lg shadow p-5">
            {/* Controls */}
            <div className="flex flex-wrap items-center gap-x-5 gap-y-3 mb-4 pb-4 border-b border-gray-200">
              <div className="flex items-center gap-1 bg-gray-100 rounded-lg p-0.5">
                {(["health", "density"] as OverlayMode[]).map((value) => (
                  <button
                    key={value}
                    onClick={() => setMode(value)}
                    className={`px-3 py-1.5 text-xs font-medium rounded-md transition ${
                      mode === value
                        ? "bg-white shadow text-gray-900"
                        : "text-gray-600 hover:text-gray-900"
                    }`}
                  >
                    {value === "health" ? "Signal health" : "Client density"}
                  </button>
                ))}
              </div>

              <div className="flex items-center gap-3 text-xs text-gray-600">
                {(
                  [
                    ["clients", "Clients"],
                    ["cells", "Coverage cells"],
                    ["labels", "AP names"],
                  ] as [keyof MapLayers, string][]
                ).map(([key, label]) => (
                  <label key={key} className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={layers[key]}
                      onChange={(event) =>
                        setLayers((prev) => ({ ...prev, [key]: event.target.checked }))
                      }
                      disabled={key === "clients" && mode === "density"}
                    />
                    {label}
                  </label>
                ))}
              </div>

              {bands.length > 0 && (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-gray-600">Band</span>
                  <div className="flex items-center gap-1">
                    {bands.map((band) => {
                      const active = !hiddenBands.has(band);
                      return (
                        <button
                          key={band}
                          onClick={() => toggleBand(band)}
                          aria-pressed={active}
                          className={`px-2 py-1 text-[11px] font-medium rounded-md border transition ${
                            active
                              ? "bg-gray-900 text-white border-gray-900"
                              : "bg-white text-gray-500 border-gray-300 hover:border-gray-400"
                          }`}
                        >
                          {band}
                        </button>
                      );
                    })}
                  </div>
                  {hiddenBands.size > 0 && (
                    <button
                      onClick={() => setHiddenBands(new Set())}
                      className="text-[11px] text-gray-500 hover:text-gray-900 underline"
                    >
                      all
                    </button>
                  )}
                </div>
              )}

              <label className="flex items-center gap-2 text-xs text-gray-600">
                Plan fade
                <input
                  type="range"
                  min={0.1}
                  max={1}
                  step={0.05}
                  value={planOpacity}
                  onChange={(event) => setPlanOpacity(Number(event.target.value))}
                  className="w-20"
                />
              </label>

              <div className="flex items-center gap-2 ml-auto">
                <label className="flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={autoRefresh}
                    onChange={(event) => setAutoRefresh(event.target.checked)}
                  />
                  Live
                </label>
                <select
                  value={refreshSeconds}
                  onChange={(event) => setRefreshSeconds(Number(event.target.value))}
                  disabled={!autoRefresh}
                  className="border border-gray-300 rounded px-2 py-1 text-xs disabled:opacity-50"
                >
                  {REFRESH_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <button
                  onClick={() => fetchOverlay(true)}
                  disabled={loading || refreshing}
                  className="px-3 py-1.5 text-xs font-medium rounded-md bg-gray-900 text-white hover:bg-gray-700 disabled:opacity-50"
                >
                  {refreshing ? "Refreshing…" : "Refresh"}
                </button>
              </div>
            </div>

            {/* Canvas */}
            <div ref={mapWrapRef} className="relative">
              {loading && !overlay ? (
                <div className="h-96 flex items-center justify-center text-sm text-gray-500">
                  Loading floor plan…
                </div>
              ) : imageUrl && overlay && filtered ? (
                <FloorplanCanvas
                  imageUrl={imageUrl}
                  aps={filtered.aps}
                  scale={overlay.floorplan.scale}
                  mode={mode}
                  layers={layers}
                  planOpacity={planOpacity}
                  selectedSerial={selectedSerial}
                  onSelectAp={setSelectedSerial}
                  onHoverClient={handleHoverClient}
                  onHoverAp={handleHoverAp}
                  onScaleResolved={setPxPerMeter}
                />
              ) : null}

              {tooltip && (
                <div
                  className="absolute z-20 pointer-events-none rounded-lg bg-gray-900 text-white text-[11px] px-2.5 py-2 shadow-lg max-w-[240px]"
                  style={{ left: tooltip.x, top: tooltip.y }}
                >
                  <div className="flex items-center gap-1.5 font-semibold mb-0.5">
                    {tooltip.swatch && (
                      <span
                        className="inline-block h-2.5 w-2.5 rounded-full"
                        style={{ backgroundColor: tooltip.swatch }}
                      />
                    )}
                    {tooltip.title}
                  </div>
                  {tooltip.lines.map((line, index) => (
                    <div key={index} className="text-gray-300">
                      {line}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[11px] text-gray-500">
              <span>
                {lastUpdated
                  ? `Updated ${lastUpdated.toLocaleTimeString()}`
                  : "Not yet loaded"}
                {pxPerMeter ? (
                  ` · ${pxPerMeter.toFixed(1)} px/m`
                ) : (
                  <span className="text-yellow-700 font-medium">
                    {" "}· not to scale (plan has no calibration)
                  </span>
                )}
              </span>
              <span>
                {selectedSerial
                  ? "Click the AP again or the plan background to clear the selection."
                  : "Click an AP to focus it."}
              </span>
            </div>
          </div>

          {/* ---- Sidebar ---- */}
          <div className="space-y-5">
            {overlay && filtered && (
              <div className="bg-white rounded-lg shadow p-5 space-y-4">
                <MapStats
                  apCount={overlay.summary.ap_count}
                  clientCount={filtered.clientCount}
                  rssi={filtered.rssi}
                />
                {filtered.hiddenCount > 0 && (
                  <p className="text-[11px] text-gray-500">
                    {visibleBands.size === 0 ? (
                      <>
                        Every band is switched off — all {filtered.hiddenCount}{" "}
                        clients are hidden.
                      </>
                    ) : (
                      <>
                        {filtered.hiddenCount} client
                        {filtered.hiddenCount === 1 ? "" : "s"} hidden by the
                        band filter. Stats and coverage cells describe{" "}
                        {Array.from(visibleBands).join(" / ")} only.
                      </>
                    )}
                  </p>
                )}
                {filtered.rssi.count > 0 && (
                  <TierBar
                    tiers={filtered.rssi.tiers}
                    total={filtered.rssi.count}
                  />
                )}
                <div className="pt-1 border-t border-gray-200">
                  <MapLegend mode={mode} />
                </div>
              </div>
            )}

            {selectedAp && (
              <div className="bg-white rounded-lg shadow p-5">
                <ApDetail ap={selectedAp} />
              </div>
            )}

            {/* Model controls */}
            <div className="bg-white rounded-lg shadow p-5">
              <h3 className="text-sm font-semibold text-gray-900 mb-1">
                Distance model
              </h3>
              <p className="text-[11px] text-gray-500 mb-3">
                Client distance is solved from RSSI with a log-distance path loss
                model. Tune it to your building — these are estimates, not
                measured positions.
              </p>

              <div className="space-y-3">
                <div>
                  <div className="flex justify-between text-xs text-gray-600 mb-1">
                    <span>Path loss exponent</span>
                    <span className="font-medium tabular-nums">
                      {pathLossExponent.toFixed(1)}
                    </span>
                  </div>
                  <input
                    type="range"
                    min={1.5}
                    max={6}
                    step={0.1}
                    value={pathLossExponent}
                    onChange={(event) =>
                      setPathLossExponent(Number(event.target.value))
                    }
                    className="w-full"
                  />
                  <div className="flex justify-between text-[10px] text-gray-400">
                    <span>2 open</span>
                    <span>3 typical</span>
                    <span>4+ dense</span>
                  </div>
                </div>

                <div>
                  <div className="flex justify-between text-xs text-gray-600 mb-1">
                    <span>Assumed client Tx power</span>
                    <span className="font-medium tabular-nums">
                      {clientTxPower} dBm
                    </span>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={25}
                    step={1}
                    value={clientTxPower}
                    onChange={(event) => setClientTxPower(Number(event.target.value))}
                    className="w-full"
                  />
                </div>

                <div>
                  <div className="flex justify-between text-xs text-gray-600 mb-1">
                    <span>Cell radius percentile</span>
                    <span className="font-medium tabular-nums">
                      p{cellPercentile}
                    </span>
                  </div>
                  <input
                    type="range"
                    min={50}
                    max={100}
                    step={5}
                    value={cellPercentile}
                    onChange={(event) => setCellPercentile(Number(event.target.value))}
                    className="w-full"
                  />
                </div>
              </div>
            </div>

            {/* Warnings */}
            {overlay && overlay.warnings.length > 0 && (
              <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
                <div className="text-xs font-semibold text-yellow-900 mb-1.5">
                  Worth knowing
                </div>
                <ul className="space-y-1 text-[11px] text-yellow-800 list-disc list-inside">
                  {overlay.warnings.map((warning, index) => (
                    <li key={index}>{warning}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ============= Client table (the non-color reading of the map) ============= */}
      {overlay && tableClients.length > 0 && (
        <div className="bg-white rounded-lg shadow p-5 mt-5">
          <div className="flex items-baseline justify-between mb-3">
            <h2 className="text-lg font-semibold">
              {selectedAp
                ? `Clients on ${selectedAp.name}`
                : hiddenBands.size > 0
                ? `${Array.from(visibleBands).join(" / ")} clients on this plan`
                : "All clients on this plan"}
            </h2>
            <span className="text-xs text-gray-500">
              {tableClients.length} client{tableClients.length === 1 ? "" : "s"},
              weakest first
            </span>
          </div>

          <div className="overflow-x-auto max-h-96 overflow-y-auto">
            <table className="min-w-full text-xs">
              <thead className="sticky top-0 bg-white">
                <tr className="text-left text-gray-500 border-b border-gray-200">
                  <th className="py-2 pr-3 font-medium">Signal</th>
                  <th className="py-2 pr-3 font-medium">RSSI</th>
                  <th className="py-2 pr-3 font-medium">SNR</th>
                  <th className="py-2 pr-3 font-medium">Client</th>
                  <th className="py-2 pr-3 font-medium">AP</th>
                  <th className="py-2 pr-3 font-medium">Band / ch</th>
                  <th className="py-2 pr-3 font-medium">SSID</th>
                  <th className="py-2 pr-3 font-medium">Est. distance</th>
                </tr>
              </thead>
              <tbody>
                {tableClients.map(({ ap, client }) => (
                  <tr
                    key={client.mac_address}
                    className="border-b border-gray-100 hover:bg-gray-50"
                  >
                    <td className="py-1.5 pr-3">
                      <span className="flex items-center gap-1.5">
                        <span
                          className="inline-block h-2.5 w-2.5 rounded-full"
                          style={{ backgroundColor: TIER_COLORS[client.tier] }}
                        />
                        {TIER_LABELS[client.tier]}
                      </span>
                    </td>
                    <td className="py-1.5 pr-3 tabular-nums">
                      {client.rssi !== null ? `${client.rssi} dBm` : "—"}
                    </td>
                    <td className="py-1.5 pr-3 tabular-nums text-gray-600">
                      {client.snr !== null ? `${client.snr} dB` : "—"}
                    </td>
                    <td className="py-1.5 pr-3">
                      <span className="text-gray-900">
                        {client.hostname || client.mac_address}
                      </span>
                      {client.os_type && (
                        <span className="text-gray-400"> · {client.os_type}</span>
                      )}
                    </td>
                    <td className="py-1.5 pr-3 text-gray-600">{ap.name}</td>
                    <td className="py-1.5 pr-3 text-gray-600 tabular-nums">
                      {client.band || "—"}
                      {client.channel ? ` / ${client.channel}` : ""}
                    </td>
                    <td className="py-1.5 pr-3 text-gray-600">{client.ssid || "—"}</td>
                    <td className="py-1.5 pr-3 tabular-nums text-gray-600">
                      {client.estimated_distance_m !== null
                        ? `~${client.estimated_distance_m.toFixed(1)} m`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ============= How to read this ============= */}
      {planId && (
        <div className="mt-5 rounded-lg border border-gray-200 bg-gray-50 p-4">
          <h3 className="text-sm font-semibold text-gray-900 mb-2">
            How to read this map
          </h3>
          <ul className="space-y-1.5 text-xs text-gray-600 list-disc list-inside">
            <li>
              <strong>APs are exact.</strong> They sit where someone placed them
              on the plan in RUCKUS One.
            </li>
            <li>
              <strong>Client dots are not positions.</strong> We know how loud a
              client sounds to its AP, which gives a distance; nothing in the
              data gives a direction. Each dot sits at its estimated distance on
              a fixed but arbitrary bearing, so a ring of dots means "this many
              clients, roughly this far out" — not "clients are over there".
            </li>
            <li>
              <strong>Coverage cells are observed, not predicted.</strong> The
              circle is the p{cellPercentile} distance of the clients actually
              associated right now, so it grows and shrinks with the crowd rather
              than describing the AP's true reach.
            </li>
            <li>
              <strong>Distance depends on assumptions.</strong> Client transmit
              power is unknown and one path loss exponent is applied to the whole
              floor, so an obstructed client reads as a distant one.
            </li>
          </ul>
        </div>
      )}
    </div>
  );
}
