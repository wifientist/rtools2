import { useEffect, useMemo, useRef, useState } from "react";
import {
  DENSITY_RAMP,
  INK,
  TIER_COLORS,
  densityColorAt,
  tierForRssi,
} from "./mapColors";
import type {
  FloorplanScale,
  MapAp,
  MapLayers,
  OverlayMode,
  PlacedClient,
} from "./types";

interface FloorplanCanvasProps {
  imageUrl: string;
  aps: MapAp[];
  scale: FloorplanScale | null;
  mode: OverlayMode;
  layers: MapLayers;
  /** Floor plan is drawn washed out so overlay marks stay legible on any image. */
  planOpacity: number;
  selectedSerial: string | null;
  onSelectAp: (serial: string | null) => void;
  onHoverClient: (placed: PlacedClient | null, event?: React.MouseEvent) => void;
  onHoverAp: (ap: MapAp | null, event?: React.MouseEvent) => void;
  onScaleResolved: (pxPerMeter: number | null) => void;
}

// Client dots are small so a few hundred can share a floor without merging
// into a blob; the surface ring is what keeps them readable over dark imagery.
const CLIENT_RADIUS = 4;
const CLIENT_RING = 1.5;
const AP_SIZE = 11;

// When the plan has no scale, distances get squeezed into this pixel band so
// relative near/far is still visible even though nothing is to scale.
const UNCALIBRATED_MIN_PX = 10;
const UNCALIBRATED_MAX_PX = 58;

// Density kernel: 3 m of influence per client when we know the scale.
const DENSITY_RADIUS_M = 3;
const DENSITY_RADIUS_PX_FALLBACK = 30;

export default function FloorplanCanvas({
  imageUrl,
  aps,
  scale,
  mode,
  layers,
  planOpacity,
  selectedSerial,
  onSelectAp,
  onHoverClient,
  onHoverAp,
  onScaleResolved,
}: FloorplanCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [natural, setNatural] = useState({ width: 0, height: 0 });
  const [imageError, setImageError] = useState(false);

  // ── Track the rendered size of the plan image ────────────
  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;

    const update = () => {
      const rect = element.getBoundingClientRect();
      setSize({ width: rect.width, height: rect.height });
    };
    update();

    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, [imageUrl]);

  // ── Pixels per metre from the plan's calibration segment ──
  const pxPerMeter = useMemo(() => {
    if (!scale || !size.width || !size.height || !natural.width) return null;

    const dx = scale.x2 - scale.x1;
    const dy = scale.y2 - scale.y1;

    // R1 doesn't document whether scale points are percentages or image
    // pixels. AP positions are explicitly percentages, so percent is the
    // working assumption — but a value above 100 can't be one, so those are
    // read as natural-image pixels instead.
    const looksLikePixels = [scale.x1, scale.y1, scale.x2, scale.y2].some(
      (value) => Math.abs(value) > 100
    );

    let segmentPx: number;
    if (looksLikePixels) {
      const renderRatio = size.width / natural.width;
      segmentPx = Math.hypot(dx, dy) * renderRatio;
    } else {
      segmentPx = Math.hypot((dx / 100) * size.width, (dy / 100) * size.height);
    }

    if (!segmentPx || !scale.distance_m) return null;
    return segmentPx / scale.distance_m;
  }, [scale, size, natural]);

  useEffect(() => {
    onScaleResolved(pxPerMeter);
  }, [pxPerMeter, onScaleResolved]);

  // ── Resolve every client to a pixel position ─────────────
  const placedClients = useMemo<PlacedClient[]>(() => {
    if (!size.width || !size.height) return [];

    // Uncalibrated plans need a distance range to normalize against.
    let minDistance = Infinity;
    let maxDistance = -Infinity;
    if (!pxPerMeter) {
      for (const ap of aps) {
        for (const client of ap.clients) {
          const distance = client.estimated_distance_m;
          if (distance === null) continue;
          if (distance < minDistance) minDistance = distance;
          if (distance > maxDistance) maxDistance = distance;
        }
      }
    }
    const span = maxDistance - minDistance;

    const placed: PlacedClient[] = [];
    for (const ap of aps) {
      const apX = (ap.x_percent / 100) * size.width;
      const apY = (ap.y_percent / 100) * size.height;

      for (const client of ap.clients) {
        const distance = client.estimated_distance_m;

        // No RSSI means no distance estimate — those sit on the AP itself.
        let radiusPx = 0;
        if (distance !== null) {
          if (pxPerMeter) {
            radiusPx = distance * pxPerMeter;
          } else if (span > 0) {
            radiusPx =
              UNCALIBRATED_MIN_PX +
              ((distance - minDistance) / span) *
                (UNCALIBRATED_MAX_PX - UNCALIBRATED_MIN_PX);
          } else {
            radiusPx = UNCALIBRATED_MIN_PX;
          }
        }

        const radians = (client.bearing_deg * Math.PI) / 180;
        placed.push({
          client,
          ap,
          x: apX + Math.cos(radians) * radiusPx,
          y: apY + Math.sin(radians) * radiusPx,
        });
      }
    }
    return placed;
  }, [aps, size, pxPerMeter]);

  // ── Density heat layer ───────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || mode !== "density" || !size.width || !size.height) return;

    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(size.width * ratio);
    canvas.height = Math.round(size.height * ratio);

    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) return;

    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, size.width, size.height);

    if (!placedClients.length) return;

    // Accumulate opaque-ish blobs, then recolor by accumulated alpha. Drawing
    // in black first keeps the accumulation in one channel we can read back.
    const kernelPx = pxPerMeter
      ? Math.max(14, DENSITY_RADIUS_M * pxPerMeter)
      : DENSITY_RADIUS_PX_FALLBACK;

    for (const placed of placedClients) {
      const gradient = ctx.createRadialGradient(
        placed.x,
        placed.y,
        0,
        placed.x,
        placed.y,
        kernelPx
      );
      gradient.addColorStop(0, "rgba(0,0,0,0.32)");
      gradient.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = gradient;
      ctx.beginPath();
      ctx.arc(placed.x, placed.y, kernelPx, 0, Math.PI * 2);
      ctx.fill();
    }

    const image = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const data = image.data;
    for (let i = 0; i < data.length; i += 4) {
      const alpha = data[i + 3];
      if (alpha === 0) continue;
      const [r, g, b] = densityColorAt(alpha / 255);
      data[i] = r;
      data[i + 1] = g;
      data[i + 2] = b;
      // Keep the faintest tail translucent so isolated clients don't read as
      // a pocket, but let real clusters go solid.
      data[i + 3] = Math.min(235, alpha * 2.2);
    }
    ctx.putImageData(image, 0, 0);
  }, [placedClients, mode, size, pxPerMeter]);

  const hasSelection = selectedSerial !== null;

  // ── Render ───────────────────────────────────────────────
  return (
    <div
      ref={containerRef}
      className="relative w-full select-none"
      style={{ backgroundColor: INK.surface }}
      onClick={() => onSelectAp(null)}
    >
      <img
        src={imageUrl}
        alt="Floor plan"
        className="block w-full h-auto"
        style={{ opacity: planOpacity }}
        draggable={false}
        onLoad={(event) => {
          const img = event.currentTarget;
          setNatural({ width: img.naturalWidth, height: img.naturalHeight });
          setImageError(false);
        }}
        onError={() => setImageError(true)}
      />

      {imageError && (
        <div className="absolute inset-0 flex items-center justify-center bg-gray-50">
          <p className="text-sm text-gray-500">Floor plan image unavailable</p>
        </div>
      )}

      {mode === "density" && (
        <canvas
          ref={canvasRef}
          className="absolute inset-0 pointer-events-none"
          style={{ width: size.width, height: size.height }}
        />
      )}

      <svg
        className="absolute inset-0"
        width={size.width}
        height={size.height}
        style={{ overflow: "visible" }}
      >
        {/* Coverage cells — where this AP's clients actually are right now */}
        {layers.cells &&
          pxPerMeter &&
          aps.map((ap) => {
            if (!ap.cell_radius_m) return null;
            const isSelected = ap.serial_number === selectedSerial;
            const tier = tierForRssi(ap.rssi_stats.median);
            return (
              <circle
                key={`cell-${ap.serial_number}`}
                cx={(ap.x_percent / 100) * size.width}
                cy={(ap.y_percent / 100) * size.height}
                r={ap.cell_radius_m * pxPerMeter}
                fill={mode === "health" ? TIER_COLORS[tier] : DENSITY_RAMP[6]}
                fillOpacity={isSelected ? 0.14 : 0.06}
                stroke={mode === "health" ? TIER_COLORS[tier] : DENSITY_RAMP[8]}
                strokeOpacity={isSelected ? 0.9 : 0.35}
                strokeWidth={isSelected ? 2 : 1}
                strokeDasharray="4 3"
                pointerEvents="none"
              />
            );
          })}

        {/* Client dots — radius modelled from RSSI, bearing arbitrary */}
        {layers.clients &&
          mode === "health" &&
          placedClients.map((placed) => {
            const dimmed =
              hasSelection && placed.ap.serial_number !== selectedSerial;
            return (
              <circle
                key={`client-${placed.client.mac_address}`}
                cx={placed.x}
                cy={placed.y}
                r={CLIENT_RADIUS}
                fill={TIER_COLORS[placed.client.tier]}
                fillOpacity={dimmed ? 0.25 : 0.95}
                stroke={INK.surface}
                strokeWidth={CLIENT_RING}
                strokeOpacity={dimmed ? 0.3 : 1}
                style={{ cursor: "pointer" }}
                onMouseEnter={(event) => onHoverClient(placed, event)}
                onMouseMove={(event) => onHoverClient(placed, event)}
                onMouseLeave={() => onHoverClient(null)}
                onClick={(event) => {
                  event.stopPropagation();
                  onSelectAp(placed.ap.serial_number);
                }}
              />
            );
          })}

        {/* APs — the only marks on this map at a real, surveyed position */}
        {aps.map((ap) => {
          const x = (ap.x_percent / 100) * size.width;
          const y = (ap.y_percent / 100) * size.height;
          const isSelected = ap.serial_number === selectedSerial;
          const offline = (ap.status || "").toLowerCase() !== "operational";

          return (
            <g
              key={`ap-${ap.serial_number}`}
              style={{ cursor: "pointer" }}
              onMouseEnter={(event) => onHoverAp(ap, event)}
              onMouseMove={(event) => onHoverAp(ap, event)}
              onMouseLeave={() => onHoverAp(null)}
              onClick={(event) => {
                event.stopPropagation();
                onSelectAp(isSelected ? null : ap.serial_number);
              }}
            >
              <rect
                x={x - AP_SIZE / 2}
                y={y - AP_SIZE / 2}
                width={AP_SIZE}
                height={AP_SIZE}
                rx={3}
                fill={offline ? INK.muted : INK.primary}
                stroke={INK.surface}
                strokeWidth={2}
              />
              {isSelected && (
                <rect
                  x={x - AP_SIZE / 2 - 4}
                  y={y - AP_SIZE / 2 - 4}
                  width={AP_SIZE + 8}
                  height={AP_SIZE + 8}
                  rx={5}
                  fill="none"
                  stroke={INK.primary}
                  strokeWidth={1.5}
                />
              )}
              {layers.labels && (
                <text
                  x={x}
                  y={y - AP_SIZE / 2 - 7}
                  textAnchor="middle"
                  fontSize={11}
                  fontWeight={isSelected ? 700 : 500}
                  fill={INK.primary}
                  stroke={INK.surface}
                  strokeWidth={3}
                  paintOrder="stroke"
                  pointerEvents="none"
                >
                  {ap.name}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
