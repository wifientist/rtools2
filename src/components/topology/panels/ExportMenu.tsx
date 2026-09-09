import { useState } from "react";
import { Download, FileJson, FileSpreadsheet, Image } from "lucide-react";
import { getNodesBounds, getViewportForBounds, useReactFlow } from "@xyflow/react";
import { toPng, toSvg } from "html-to-image";
import { useTopology } from "../state/store";

/**
 * Getting the map out.
 *
 * The image exports capture the canvas EXACTLY as arranged — layout, collapse
 * state, filters, everything — because that arrangement is the work, and a
 * picture that quietly re-laid itself out would not be the thing the person
 * was looking at when they hit export.
 *
 * Except for one thing, which has to be undone first: past 300 nodes the canvas
 * only mounts what is on screen. Measured on a 420-node graph, 27 nodes were in
 * the DOM — and since these exports serialise the DOM, the picture would have
 * held 27 of 420 nodes with nothing to say the rest were missing. So culling is
 * suspended, the graph is given time to mount in full, and only then captured.
 *
 * The data exports come from the stored snapshot server-side rather than from
 * what the browser holds, so an export can never disagree with the map it was
 * taken from.
 */

type Props = {
  /** Backend URL for a given export path, already carrying tenant + venue scope. */
  exportUrl?: (path: string) => string;
  label?: string;
};

const PADDING = 40;
const MAX_DIMENSION = 8000;
/**
 * Above this, SVG stops being a sensible format and asks first.
 *
 * SVG keeps every node as markup with its computed styles inlined, so its size
 * scales with the node COUNT rather than with the picture's dimensions.
 * Measured on a 420-node graph: 103 MB of SVG against well under 1 MB of PNG
 * for the same map. A 4,000-node estate would be an unopenable file, and would
 * probably take the browser tab with it on the way out.
 */
const SVG_WARN_NODES = 500;
/** Give up waiting for nodes to mount; capture what there is rather than hang. */
const MOUNT_TIMEOUT_MS = 20000;

/**
 * Wait until every node is actually in the DOM.
 *
 * The target is the count, not elapsed time: mounting a few thousand nodes
 * takes as long as it takes, and a guessed delay would either truncate a big
 * export or make every small one feel broken.
 *
 * The subtlety that cost a first attempt: "the count stopped changing" is true
 * immediately, before React has even committed the un-culled render, so a
 * stability check on its own returns the culled count and produces exactly the
 * wrong picture. Stability is therefore only accepted after a grace period, and
 * only as a fallback for a graph that genuinely cannot mount everything.
 */
const GRACE_MS = 1500;

async function waitForFullMount(expected: number): Promise<number> {
  const started = Date.now();
  const frame = () =>
    new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
  let previous = -1;
  let stableFor = 0;
  for (;;) {
    await frame();
    const count = document.querySelectorAll(".react-flow__node").length;
    if (count >= expected) return count;
    stableFor = count === previous ? stableFor + 1 : 0;
    previous = count;
    const elapsed = Date.now() - started;
    if (elapsed > MOUNT_TIMEOUT_MS) return count;
    if (elapsed > GRACE_MS && stableFor > 30) return count;
  }
}

export default function ExportMenu({ exportUrl, label = "topology" }: Props) {
  const { getNodes } = useReactFlow();
  const setExporting = useTopology((s) => s.setExporting);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [warning, setWarning] = useState("");

  const saveImage = async (format: "png" | "svg") => {
    setBusy(format);
    setError("");
    setWarning("");
    try {
      const viewport = document.querySelector(
        ".react-flow__viewport",
      ) as HTMLElement | null;
      if (!viewport) throw new Error("nothing to export");

      const nodes = getNodes();
      if (!nodes.length) throw new Error("nothing to export");

      if (
        format === "svg" &&
        nodes.length > SVG_WARN_NODES &&
        !window.confirm(
          `This map has ${nodes.length.toLocaleString()} nodes. SVG keeps every ` +
            `one of them as markup, so the file will be very large — around ` +
            `${Math.round((nodes.length / 420) * 100)} MB — and may be slow or ` +
            `impossible to open.\n\nPNG is a far better choice at this size. ` +
            `Export the SVG anyway?`,
        )
      ) {
        return;
      }

      // Mount everything, then wait for it. Without this the picture holds
      // only what was on screen — see the note at the top of this file.
      setExporting(true);
      const mounted = await waitForFullMount(nodes.length);
      // If it still could not mount them all, SAY SO. A short picture that
      // admits it is short is recoverable; one that does not is a wrong map
      // somebody will act on.
      if (mounted < nodes.length) {
        setWarning(
          `Captured ${mounted.toLocaleString()} of ${nodes.length.toLocaleString()} ` +
            `nodes — the canvas did not finish drawing the rest in time. Collapse ` +
            `some groups and export again for a complete picture.`,
        );
      }

      const bounds = getNodesBounds(nodes);

      // Export the WHOLE graph, not just what is on screen — but cap it, since
      // a 15,000px-wide hierarchy at full size is a browser-crashing image.
      const scale = Math.min(
        1,
        MAX_DIMENSION / Math.max(bounds.width + PADDING * 2, 1),
        MAX_DIMENSION / Math.max(bounds.height + PADDING * 2, 1),
      );
      const width = Math.ceil((bounds.width + PADDING * 2) * scale);
      const height = Math.ceil((bounds.height + PADDING * 2) * scale);
      const transform = getViewportForBounds(
        bounds,
        width,
        height,
        0.01,
        4,
        PADDING * scale,
      );

      const options = {
        backgroundColor: "#ffffff",
        width,
        height,
        style: {
          width: `${width}px`,
          height: `${height}px`,
          transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.zoom})`,
        },
        // The controls and minimap are UI, not map.
        filter: (node: HTMLElement) =>
          !node.classList?.contains?.("react-flow__minimap") &&
          !node.classList?.contains?.("react-flow__controls") &&
          !node.classList?.contains?.("react-flow__background"),
      };

      const data =
        format === "png" ? await toPng(viewport, options) : await toSvg(viewport, options);
      const link = document.createElement("a");
      link.download = `${label}-${new Date().toISOString().slice(0, 10)}.${format}`;
      link.href = data;
      link.click();
      // Held open when the capture was short, so the warning is actually read.
      if (!warning) setOpen(false);
    } catch (e) {
      setError(`Could not export: ${e}`);
    } finally {
      // Always, or the canvas is left uncullable and a big graph crawls.
      setExporting(false);
      setBusy(null);
    }
  };

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        title="Save this map"
        className="flex items-center gap-1 rounded border border-gray-300 px-2 py-1 text-gray-700 hover:bg-gray-50"
      >
        <Download size={12} /> Export
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full z-30 mt-1 w-64 rounded-lg border border-gray-200 bg-white p-1 shadow-lg">
            <button
              onClick={() => saveImage("png")}
              disabled={busy !== null}
              className="flex w-full items-start gap-2 rounded px-2 py-1.5 text-left hover:bg-gray-50 disabled:opacity-50"
            >
              <Image size={13} className="mt-0.5 shrink-0 text-gray-500" />
              <span>
                <span className="block text-xs font-medium text-gray-900">
                  {busy === "png" ? "Rendering…" : "Picture (PNG)"}
                </span>
                <span className="block text-[11px] text-gray-500">
                  Exactly as arranged. For tickets and handovers.
                </span>
              </span>
            </button>
            <button
              onClick={() => saveImage("svg")}
              disabled={busy !== null}
              className="flex w-full items-start gap-2 rounded px-2 py-1.5 text-left hover:bg-gray-50 disabled:opacity-50"
            >
              <Image size={13} className="mt-0.5 shrink-0 text-gray-500" />
              <span>
                <span className="block text-xs font-medium text-gray-900">
                  {busy === "svg" ? "Rendering…" : "Vector (SVG)"}
                </span>
                <span className="block text-[11px] text-gray-500">
                  Scales without blurring; editable. Large on a big map — use
                  PNG past a few hundred nodes.
                </span>
              </span>
            </button>

            {exportUrl && (
              <>
                <div className="my-1 border-t border-gray-100" />
                <a
                  href={exportUrl("export/links.csv")}
                  className="flex w-full items-start gap-2 rounded px-2 py-1.5 text-left hover:bg-gray-50"
                  onClick={() => setOpen(false)}
                >
                  <FileSpreadsheet
                    size={13}
                    className="mt-0.5 shrink-0 text-gray-500"
                  />
                  <span>
                    <span className="block text-xs font-medium text-gray-900">
                      Link inventory (CSV)
                    </span>
                    <span className="block text-[11px] text-gray-500">
                      Every link, both ports, how sure we are, and why. An
                      as-built patch schedule.
                    </span>
                  </span>
                </a>
                <a
                  href={exportUrl("export.json")}
                  className="flex w-full items-start gap-2 rounded px-2 py-1.5 text-left hover:bg-gray-50"
                  onClick={() => setOpen(false)}
                >
                  <FileJson size={13} className="mt-0.5 shrink-0 text-gray-500" />
                  <span>
                    <span className="block text-xs font-medium text-gray-900">
                      Snapshot (JSON)
                    </span>
                    <span className="block text-[11px] text-gray-500">
                      Devices, ports, links and all the evidence behind them.
                    </span>
                  </span>
                </a>
              </>
            )}

            {error && (
              <p className="px-2 py-1 text-[11px] text-red-700">{error}</p>
            )}
            {warning && (
              <p className="mx-1 my-1 rounded bg-amber-50 px-2 py-1 text-[11px] text-amber-800">
                {warning}
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
