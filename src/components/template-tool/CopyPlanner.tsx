import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ChevronDown, Copy, Loader2, X } from "lucide-react";
import { apiGet, apiPost } from "@/utils/api";

/**
 * Copy a venue "bundle" (a VENUE template and the templates under it) with a
 * new country and other edits. The reason this exists: country code is fixed
 * when a venue template is created, so a venue for another country needs a
 * new template -- and the radio channel lists have to follow the country.
 *
 * PREVIEW ONLY. "Preview copy" asks the backend for the exact steps a copy
 * would take; nothing is written to RUCKUS ONE.
 */

interface R1Error {
  status: number | null;
  what: string;
  body: unknown;
}

interface BundleNode {
  id: string;
  name: string;
  type: string;
  context: "MSP" | "REC";
  body: Record<string, any> | null;
  settings: Record<string, unknown> | null;
  venueNetworkSettings: unknown;
  error: R1Error | null;
  children: BundleNode[];
}

interface BundleResponse {
  root: BundleNode | null;
  error: string | null;
  notCopiedSettings?: Record<string, string>;
}

interface VenueCountry {
  id: string;
  name: string;
  country?: string;
  countryCode?: string;
}

interface Step {
  op: string;
  type: string;
  method: string;
  path: string;
  summary: string;
  unverified?: boolean;
  reused?: boolean;
}

type Mode = "reuse" | "copy" | "skip";

interface EffectiveMode {
  mode: Mode;
  chosen: boolean; // false = decided by its parent or a rule, not the operator
  reason: string | null;
}

/**
 * Mirrors resolve_modes() in api/services/msp_template_copy.py, which is the
 * authority -- this copy exists only so the cards can show it as you click.
 * Default is REUSE: attach the existing template to the new venue. Only the
 * children of a COPIED template get their own choice, and a copied DPSK pool
 * forces a copied identity group (one group belongs to exactly one pool).
 */
function effectiveModes(root: BundleNode, requested: Record<string, Mode>): Record<string, EffectiveMode> {
  const out: Record<string, EffectiveMode> = {};
  const visit = (node: BundleNode, parentMode: Mode) => {
    for (const kid of node.children) {
      if (parentMode !== "copy") {
        out[kid.id] = {
          mode: parentMode,
          chosen: false,
          reason: parentMode === "reuse" ? "comes with its parent" : "skipped with its parent",
        };
      } else if (node.type === "DPSK" && kid.type === "IDENTITY_GROUP") {
        out[kid.id] = { mode: "copy", chosen: false, reason: "an identity group belongs to exactly one DPSK pool" };
      } else {
        out[kid.id] = { mode: requested[kid.id] ?? "reuse", chosen: true, reason: null };
      }
      visit(kid, out[kid.id].mode);
    }
  };
  visit(root, "copy");
  return out;
}

const MODE_STYLE: Record<Mode, string> = {
  reuse: "bg-green-600 text-white border-green-600",
  copy: "bg-blue-600 text-white border-blue-600",
  skip: "bg-gray-500 text-white border-gray-500",
};

const MODE_LABEL: Record<Mode, string> = { reuse: "Reuse", copy: "Copy", skip: "Skip" };

interface TrimEntry {
  band: string;
  field: string;
  bandwidth: string;
  source: string[];
  kept: string[];
  dropped: string[];
  checked: boolean;
  emptied: boolean;
}

interface PreviewResponse {
  steps: Step[];
  warnings: string[];
  radio: { reference: VenueCountry | null; report: TrimEntry[]; deferred: boolean } | null;
}

interface Address {
  countryCode: string;
  country: string;
  city: string;
  addressLine: string;
  timezone: string;
  latitude: string;
  longitude: string;
}

const regionNames = new Intl.DisplayNames(["en"], { type: "region", fallback: "none" });

// Every ISO 3166 alpha-2 code the browser can name -- no hand-kept table.
const COUNTRY_CODES: string[] = (() => {
  const codes: string[] = [];
  for (let a = 65; a <= 90; a++)
    for (let b = 65; b <= 90; b++) {
      const code = String.fromCharCode(a, b);
      try {
        if (regionNames.of(code)) codes.push(code);
      } catch {
        /* not a region */
      }
    }
  return codes;
})();

const TIMEZONES: string[] =
  (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.("timeZone") ?? [];

function countryName(code: string) {
  try {
    return (code.length === 2 && regionNames.of(code.toUpperCase())) || "";
  } catch {
    return "";
  }
}

function Json({ value }: { value: unknown }) {
  return (
    <pre className="text-xs bg-white border border-gray-200 rounded p-2 overflow-auto max-h-80 whitespace-pre-wrap break-all">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

function Chevron({ open }: { open: boolean }) {
  return <ChevronDown size={14} className={`text-gray-400 transition-transform ${open ? "rotate-0" : "-rotate-90"}`} />;
}

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-xs text-gray-600">{label}</span>
      {children}
      {hint && <span className="block text-[11px] mt-0.5">{hint}</span>}
    </label>
  );
}

const inputCls = "mt-0.5 w-full px-2 py-1 text-sm border border-gray-300 rounded";

/** One template card in the bundle tree, with its subtree. */
function TemplateCard({
  node,
  depth,
  modes,
  expanded,
  renames,
  onMode,
  onToggleExpand,
  onRename,
}: {
  node: BundleNode;
  depth: number;
  modes: Record<string, EffectiveMode>;
  expanded: Set<string>;
  renames: Record<string, string>;
  onMode: (id: string, mode: Mode) => void;
  onToggleExpand: (key: string) => void;
  onRename: (id: string, name: string) => void;
}) {
  const eff = modes[node.id] ?? { mode: "reuse", chosen: true, reason: null };
  const off = eff.mode === "skip";
  const key = `t:${node.id}`;
  const open = expanded.has(key);
  return (
    <div style={{ marginLeft: depth * 20 }} className="space-y-1">
      <div
        className={`border rounded-lg ${
          off ? "bg-gray-50 border-gray-200 opacity-60" : eff.mode === "copy" ? "bg-blue-50/40 border-blue-300" : "bg-white border-gray-300"
        }`}
      >
        <div className="flex items-center gap-2 px-3 py-2">
          {eff.chosen ? (
            <div className="flex shrink-0">
              {(["reuse", "copy", "skip"] as Mode[]).map((m, i) => (
                <button
                  key={m}
                  onClick={() => onMode(node.id, m)}
                  className={`text-[11px] px-2 py-0.5 border ${i === 0 ? "rounded-l" : i === 2 ? "rounded-r" : ""} ${
                    eff.mode === m ? MODE_STYLE[m] : "bg-white text-gray-600 border-gray-300 hover:bg-gray-50"
                  }`}
                >
                  {MODE_LABEL[m]}
                </button>
              ))}
            </div>
          ) : (
            <span
              className="shrink-0 text-[11px] px-2 py-0.5 rounded border border-dashed border-gray-300 text-gray-600"
              title={eff.reason ?? ""}
            >
              {MODE_LABEL[eff.mode]} · {eff.reason}
            </span>
          )}
          <button onClick={() => onToggleExpand(key)} className="flex items-center gap-2 min-w-0 text-left">
            <Chevron open={open} />
            <span className="text-[10px] font-mono bg-gray-100 text-gray-700 px-1.5 py-0.5 rounded">{node.type}</span>
            <span className="text-sm font-medium truncate">{node.name}</span>
          </button>
          {node.error && <span className="text-xs text-red-700">read failed</span>}
          {eff.mode === "copy" && (
            <input
              value={renames[node.id] ?? ""}
              onChange={(e) => onRename(node.id, e.target.value)}
              placeholder={`Name of the copy (default: ${node.name})`}
              className="ml-auto w-72 px-2 py-0.5 text-xs border border-gray-300 rounded"
            />
          )}
        </div>
        {open && (
          <div className="px-3 pb-3 space-y-2">
            {node.error && (
              <div className="text-xs text-red-700">
                {node.error.what}: {JSON.stringify(node.error.body)}
              </div>
            )}
            <Json value={node.body} />
            {node.venueNetworkSettings != null && (
              <div>
                <div className="text-xs font-semibold text-gray-700 mb-1">
                  Activation on the venue (radio types, VLAN, schedule)
                </div>
                <Json value={node.venueNetworkSettings} />
              </div>
            )}
          </div>
        )}
      </div>
      {node.children.map((kid) => (
        <TemplateCard
          key={kid.id}
          node={kid}
          depth={depth + 1}
          modes={modes}
          expanded={expanded}
          renames={renames}
          onMode={onMode}
          onToggleExpand={onToggleExpand}
          onRename={onRename}
        />
      ))}
    </div>
  );
}

function RadioTrim({ radio }: { radio: NonNullable<PreviewResponse["radio"]> }) {
  if (radio.deferred) {
    return (
      <div className="text-sm text-amber-900 bg-amber-50 border border-amber-200 rounded p-3">
        No venue template in this MSP uses the target country, so the legal channels are not known yet. A copy
        will read them from the new venue template right after creating it, and trim the channel lists then.
      </div>
    );
  }
  const changed = radio.report.filter((e) => e.dropped.length || !e.checked);
  return (
    <div className="space-y-2">
      <div className="text-xs text-gray-600">
        Legal channels borrowed from venue template <b>{radio.reference?.name}</b> ({radio.reference?.countryCode}).
      </div>
      {changed.length === 0 ? (
        <div className="text-sm text-green-800">Every configured channel is legal in the target country.</div>
      ) : (
        <table className="w-full text-xs border border-gray-200">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="text-left p-1">Band</th>
              <th className="text-left p-1">List</th>
              <th className="text-left p-1">Width</th>
              <th className="text-left p-1">Kept</th>
              <th className="text-left p-1">Dropped</th>
            </tr>
          </thead>
          <tbody>
            {changed.map((e) => (
              <tr key={`${e.band}.${e.field}`} className={`border-t border-gray-100 align-top ${e.emptied ? "bg-red-50" : ""}`}>
                <td className="p-1 font-mono">{e.band}</td>
                <td className="p-1 font-mono">{e.field}</td>
                <td className="p-1">{e.bandwidth}</td>
                <td className="p-1">
                  {e.kept.length}/{e.source.length}
                </td>
                <td className="p-1 font-mono break-all">
                  {e.checked ? e.dropped.join(", ") : <span className="text-gray-500">not checked (no table)</span>}
                  {e.emptied && <span className="ml-1 text-red-700 font-sans font-semibold">no legal channels left</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function CopyPlanner({
  base,
  templateId,
  templateName,
  onClose,
}: {
  base: string;
  templateId: string;
  templateName: string;
  onClose: () => void;
}) {
  const [bundle, setBundle] = useState<BundleResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [venueCountries, setVenueCountries] = useState<VenueCountry[]>([]);

  const [name, setName] = useState("");
  const [address, setAddress] = useState<Address>({
    countryCode: "",
    country: "",
    city: "",
    addressLine: "",
    timezone: "",
    latitude: "",
    longitude: "",
  });
  const [excludeSettings, setExcludeSettings] = useState<Set<string>>(new Set());
  const [requestedModes, setRequestedModes] = useState<Record<string, Mode>>({});
  const [renames, setRenames] = useState<Record<string, string>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    apiGet<BundleResponse>(`${base}/templates/${templateId}/bundle`)
      .then((res) => {
        if (!live) return;
        setBundle(res);
        const src = res.root?.body?.address ?? {};
        setName(`${res.root?.name ?? templateName} (copy)`);
        setAddress({
          countryCode: src.countryCode ?? "",
          country: src.country ?? "",
          city: src.city ?? "",
          addressLine: src.addressLine ?? "",
          timezone: src.timezone ?? "",
          latitude: src.latitude != null ? String(src.latitude) : "",
          longitude: src.longitude != null ? String(src.longitude) : "",
        });
      })
      .catch((e) => live && setLoadError(e instanceof Error ? e.message : String(e)));
    apiGet<{ venues: VenueCountry[] }>(`${base}/venue-countries`)
      .then((res) => live && setVenueCountries(res.venues))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [base, templateId, templateName]);

  // Any edit makes the last preview stale.
  useEffect(() => setPreview(null), [name, address, excludeSettings, requestedModes, renames]);

  const root = bundle?.root ?? null;
  const notCopied = bundle?.notCopiedSettings ?? {};
  const settingNames = useMemo(() => Object.keys(root?.settings ?? {}), [root]);
  const modes = useMemo(() => (root ? effectiveModes(root, requestedModes) : {}), [root, requestedModes]);
  const modeCounts = useMemo(() => {
    const c: Record<Mode, number> = { reuse: 0, copy: 0, skip: 0 };
    Object.values(modes).forEach((m) => c[m.mode]++);
    return c;
  }, [modes]);
  const sourceCountry = root?.body?.address?.countryCode ?? "";
  const reference = venueCountries.find(
    (v) => (v.countryCode ?? "").toUpperCase() === address.countryCode.toUpperCase()
  );

  const allKeys = useMemo(() => {
    const keys: string[] = settingNames.map((s) => `s:${s}`);
    const walk = (n: BundleNode) => n.children.forEach((k) => (keys.push(`t:${k.id}`), walk(k)));
    if (root) walk(root);
    return keys;
  }, [root, settingNames]);

  const toggle = (set: Set<string>, value: string) => {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    return next;
  };

  const setAddr = (patch: Partial<Address>) => setAddress((a) => ({ ...a, ...patch }));

  const runPreview = async () => {
    setPreviewing(true);
    setPreviewError(null);
    try {
      const lat = parseFloat(address.latitude);
      const lng = parseFloat(address.longitude);
      setPreview(
        await apiPost<PreviewResponse>(`${base}/templates/${templateId}/copy-preview`, {
          name,
          address: {
            ...address,
            countryCode: address.countryCode.toUpperCase(),
            latitude: Number.isFinite(lat) ? lat : undefined,
            longitude: Number.isFinite(lng) ? lng : undefined,
          },
          excludeSettings: [...excludeSettings],
          modes: requestedModes,
          renames: Object.fromEntries(
            Object.entries(renames).filter(([id, v]) => v.trim() && modes[id]?.mode === "copy")
          ),
        })
      );
    } catch (e) {
      setPreviewError(e instanceof Error ? e.message : String(e));
    } finally {
      setPreviewing(false);
    }
  };

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-5">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Copy size={18} /> Copy bundle: {templateName}
          </h2>
          <p className="text-xs text-gray-500">
            Builds a new venue template in this MSP, reusing or copying the templates under it. Preview only —
            nothing is written yet.
          </p>
        </div>
        <button onClick={onClose} className="text-gray-500 hover:text-gray-800" aria-label="Close">
          <X size={18} />
        </button>
      </div>

      {loadError && <div className="text-sm text-red-700">{loadError}</div>}
      {bundle?.error && <div className="text-sm text-red-700">{bundle.error}</div>}
      {!bundle && !loadError && (
        <div className="flex items-center gap-2 text-sm text-gray-500">
          <Loader2 size={14} className="animate-spin" /> Reading the bundle (venue, settings and every template under
          it)…
        </div>
      )}

      {root && (
        <>
          {/* ── New venue template ───────────────────────────────────── */}
          <section>
            <h3 className="text-sm font-semibold text-gray-800 mb-1">New venue template</h3>
            <p className="text-xs text-gray-500 mb-2">
              Country is fixed when the venue template is created — this is the one chance to set it.
            </p>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
              <Field label="Name">
                <input value={name} onChange={(e) => setName(e.target.value)} className={inputCls} />
              </Field>
              <Field
                label={`Country code (source: ${sourceCountry || "?"})`}
                hint={
                  address.countryCode.length === 2 &&
                  (reference ? (
                    <span className="text-green-700">Channels can be checked now (via {reference.name}).</span>
                  ) : (
                    <span className="text-amber-700">No venue template in this country — channels checked after creation.</span>
                  ))
                }
              >
                <input
                  value={address.countryCode}
                  list="tt-country-codes"
                  maxLength={2}
                  onChange={(e) => {
                    const code = e.target.value.toUpperCase();
                    setAddr({ countryCode: code, ...(countryName(code) ? { country: countryName(code) } : {}) });
                  }}
                  className={`${inputCls} font-mono uppercase`}
                />
                <datalist id="tt-country-codes">
                  {COUNTRY_CODES.map((c) => (
                    <option key={c} value={c}>
                      {countryName(c)}
                    </option>
                  ))}
                </datalist>
              </Field>
              <Field label="Country name">
                <input value={address.country} onChange={(e) => setAddr({ country: e.target.value })} className={inputCls} />
              </Field>
              <Field label="Timezone">
                <input
                  value={address.timezone}
                  list="tt-timezones"
                  onChange={(e) => setAddr({ timezone: e.target.value })}
                  className={inputCls}
                />
                <datalist id="tt-timezones">
                  {TIMEZONES.map((t) => (
                    <option key={t} value={t} />
                  ))}
                </datalist>
              </Field>
              <Field label="Address line">
                <input
                  value={address.addressLine}
                  onChange={(e) => setAddr({ addressLine: e.target.value })}
                  className={inputCls}
                />
              </Field>
              <Field label="City">
                <input value={address.city} onChange={(e) => setAddr({ city: e.target.value })} className={inputCls} />
              </Field>
              <Field label="Latitude">
                <input value={address.latitude} onChange={(e) => setAddr({ latitude: e.target.value })} className={inputCls} />
              </Field>
              <Field label="Longitude">
                <input value={address.longitude} onChange={(e) => setAddr({ longitude: e.target.value })} className={inputCls} />
              </Field>
            </div>
          </section>

          <div className="flex justify-end gap-3 -mb-3">
            <button onClick={() => setExpanded(new Set(allKeys))} className="text-[11px] text-blue-600 hover:text-blue-800">
              Expand all
            </button>
            <button onClick={() => setExpanded(new Set())} className="text-[11px] text-gray-500 hover:text-gray-700">
              Collapse all
            </button>
          </div>

          {/* ── Venue settings ───────────────────────────────────────── */}
          <section>
            <h3 className="text-sm font-semibold text-gray-800 mb-2">
              Venue settings ({settingNames.length - excludeSettings.size} of {settingNames.length} copied)
            </h3>
            <div className="space-y-1">
              {settingNames.map((s) => {
                const key = `s:${s}`;
                const open = expanded.has(key);
                const reason = notCopied[s];
                const value = root.settings?.[s];
                const failed = !!value && typeof value === "object" && "_error" in (value as object);
                const off = !!reason || failed || excludeSettings.has(s);
                return (
                  <div key={s} className={`border rounded-lg ${off ? "bg-gray-50 border-gray-200" : "bg-white border-gray-300"}`}>
                    <div className="flex items-center gap-2 px-3 py-1.5">
                      <input
                        type="checkbox"
                        checked={!off}
                        disabled={!!reason || failed}
                        onChange={() => setExcludeSettings((x) => toggle(x, s))}
                      />
                      <button onClick={() => setExpanded((x) => toggle(x, key))} className="flex items-center gap-2 text-left">
                        <Chevron open={open} />
                        <span className={`text-sm font-mono ${off ? "text-gray-500" : ""}`}>{s}</span>
                      </button>
                      {s === "apRadioSettings" && (
                        <span className="text-[10px] bg-amber-100 text-amber-800 px-1.5 py-0.5 rounded">
                          channels follow the country
                        </span>
                      )}
                      {reason && <span className="text-xs text-gray-500">{reason}</span>}
                      {failed && <span className="text-xs text-red-700">read failed — not copied</span>}
                    </div>
                    {open && (
                      <div className="px-3 pb-3">
                        <Json value={value} />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </section>

          {/* ── Templates under the venue ────────────────────────────── */}
          <section>
            <h3 className="text-sm font-semibold text-gray-800 mb-1">Templates in this bundle</h3>
            <p className="text-xs text-gray-500 mb-2">
              <b>Reuse</b> (default) attaches the existing template to the new venue, with everything under it.{" "}
              <b>Copy</b> creates a new template — its own children then get the same choice. <b>Skip</b> leaves it
              (and everything under it) out. Copies need names that don't clash with templates already in this MSP.
            </p>
            <div className="text-xs text-gray-600 mb-2">
              {modeCounts.reuse} reused · {modeCounts.copy} copied · {modeCounts.skip} skipped
            </div>
            <div className="space-y-1">
              {root.children.length === 0 && <div className="text-sm text-gray-500">No templates under this venue.</div>}
              {root.children.map((kid) => (
                <TemplateCard
                  key={kid.id}
                  node={kid}
                  depth={0}
                  modes={modes}
                  expanded={expanded}
                  renames={renames}
                  onMode={(id, m) => setRequestedModes((r) => ({ ...r, [id]: m }))}
                  onToggleExpand={(key) => setExpanded((x) => toggle(x, key))}
                  onRename={(id, value) => setRenames((r) => ({ ...r, [id]: value }))}
                />
              ))}
            </div>
          </section>

          {/* ── Preview ──────────────────────────────────────────────── */}
          <section className="border-t border-gray-200 pt-4">
            <button
              onClick={runPreview}
              disabled={previewing || address.countryCode.length !== 2 || !name.trim()}
              className="flex items-center gap-2 px-4 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
            >
              {previewing && <Loader2 size={14} className="animate-spin" />} Preview copy
            </button>
            {previewError && <div className="mt-2 text-sm text-red-700">{previewError}</div>}

            {preview && (
              <div className="mt-4 space-y-4">
                {preview.warnings.length > 0 && (
                  <div className="bg-amber-50 border border-amber-200 rounded p-3">
                    <div className="flex items-center gap-1 text-sm font-semibold text-amber-900 mb-1">
                      <AlertTriangle size={14} /> {preview.warnings.length} to review
                    </div>
                    <ul className="list-disc ml-5 text-xs text-amber-900 space-y-0.5">
                      {preview.warnings.map((w, i) => (
                        <li key={i}>{w}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {preview.radio && (
                  <div>
                    <h4 className="text-sm font-semibold text-gray-800 mb-1">Radio channels</h4>
                    <RadioTrim radio={preview.radio} />
                  </div>
                )}

                <div>
                  <h4 className="text-sm font-semibold text-gray-800 mb-1">
                    Steps a copy would take ({preview.steps.length})
                  </h4>
                  <p className="text-xs text-gray-500 mb-1">
                    {"{venue}"} and {"{<source id>}"} stand for the ids of the new copies; a bare id is an existing
                    template being reused.
                  </p>
                  <ol className="text-xs space-y-0.5 list-decimal ml-5">
                    {preview.steps.map((s, i) => (
                      <li key={i}>
                        <span className="font-mono text-gray-500">{s.method}</span> {s.summary}
                        {s.reused && (
                          <span className="ml-1 text-[10px] bg-green-100 text-green-800 px-1 rounded">existing template</span>
                        )}
                        {s.unverified && (
                          <span className="ml-1 text-[10px] bg-red-100 text-red-800 px-1 rounded">unverified endpoint</span>
                        )}
                        <div className="font-mono text-[11px] text-gray-400 break-all">{s.path}</div>
                      </li>
                    ))}
                  </ol>
                </div>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
