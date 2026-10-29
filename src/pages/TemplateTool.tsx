import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Copy, LayoutTemplate, RefreshCw } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { apiGet } from "../utils/api";
import CopyPlanner from "../components/template-tool/CopyPlanner";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

type Context = "MSP" | "REC";

interface TemplateRow {
  id: string;
  name: string;
  type: string;
  context: Context;
  tenantId?: string;
  appliedOnTenants?: string[];
  driftStatus?: string | null;
  isEnforced?: boolean;
  createdBy?: string;
  createdOn?: number;
  lastModified?: number;
  lastApplied?: number;
  supportsChildren?: boolean;
}

interface R1Error {
  status: number | null;
  what: string;
  body: unknown;
}

interface ListResponse {
  templates: TemplateRow[];
  ecNames: Record<string, string>;
  errors: R1Error[];
  readableTypes: string[];
}

interface BodyResponse {
  body: unknown;
  settings: Record<string, unknown> | null;
  error: R1Error | null;
}

interface DiffEntry {
  diffName: string;
  diffData: { path: string; data: { template: unknown; instance: unknown } }[];
}

// Venues first: a venue template is the root most other templates hang off.
const TYPE_ORDER = ["VENUE", "NETWORK", "SWITCH_CLI", "PORTAL", "DPSK", "IDENTITY_GROUP"];

function typeRank(t: string) {
  const i = TYPE_ORDER.indexOf(t);
  return i === -1 ? TYPE_ORDER.length : i;
}

function fmtTime(ms?: number) {
  return ms ? new Date(ms).toLocaleString() : "";
}

function errorText(e: R1Error) {
  const body = typeof e.body === "string" ? e.body : JSON.stringify(e.body);
  return `${e.what}${e.status ? ` (HTTP ${e.status})` : ""}: ${body}`;
}

function DriftBadge({ status }: { status?: string | null }) {
  if (!status) return null;
  const style =
    status === "IN_SYNC"
      ? "bg-green-100 text-green-800"
      : status === "DRIFT_DETECTED"
        ? "bg-amber-100 text-amber-800"
        : "bg-gray-100 text-gray-700";
  return <span className={`text-xs px-1.5 py-0.5 rounded ${style}`}>{status.replace(/_/g, " ").toLowerCase()}</span>;
}

function Json({ value }: { value: unknown }) {
  return (
    <pre className="text-xs bg-gray-50 border border-gray-200 rounded p-3 overflow-auto max-h-[60vh] whitespace-pre-wrap break-all">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

/** One node of the dependency tree; children load when it is opened. */
function DependencyNode({
  row,
  base,
  onSelect,
  depth,
}: {
  row: TemplateRow;
  base: string;
  onSelect: (id: string) => void;
  depth: number;
}) {
  const [open, setOpen] = useState(false);
  const [kids, setKids] = useState<TemplateRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const expandable = row.supportsChildren !== false;

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && kids === null) {
      try {
        const res = await apiGet<{ children: TemplateRow[]; error: R1Error | null }>(
          `${base}/templates/${row.id}/children?context=${row.context ?? "MSP"}`
        );
        if (res.error) setError(errorText(res.error));
        setKids(res.children.map((c) => ({ ...c, context: row.context ?? "MSP" })));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setKids([]);
      }
    }
  };

  return (
    <div style={{ marginLeft: depth ? 16 : 0 }}>
      <div className="flex items-center gap-1 py-0.5 text-sm">
        {expandable ? (
          <button onClick={toggle} className="text-gray-500 hover:text-gray-800" aria-label="Expand">
            {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
        ) : (
          <span className="w-[14px]" />
        )}
        <span className="text-xs font-mono text-gray-500 w-28 shrink-0">{row.type}</span>
        <button onClick={() => onSelect(row.id)} className="text-blue-700 hover:underline text-left">
          {row.name}
        </button>
        <DriftBadge status={row.driftStatus} />
      </div>
      {open && error && <div className="ml-6 text-xs text-red-700">{error}</div>}
      {open && kids && kids.length === 0 && !error && (
        <div className="ml-6 text-xs text-gray-500">No dependencies.</div>
      )}
      {open &&
        kids?.map((k) => (
          <DependencyNode key={k.id} row={k} base={base} onSelect={onSelect} depth={depth + 1} />
        ))}
    </div>
  );
}

function DriftReport({ base, template, tenantId }: { base: string; template: TemplateRow; tenantId: string }) {
  const [diffs, setDiffs] = useState<DiffEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    apiGet<{ diffs: DiffEntry[]; error: R1Error | null }>(
      `${base}/templates/${template.id}/drift/${tenantId}?context=${template.context}`
    )
      .then((res) => {
        if (!live) return;
        if (res.error) setError(errorText(res.error));
        setDiffs(res.diffs);
      })
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [base, template.id, template.context, tenantId]);

  if (error) return <div className="text-sm text-red-700">{error}</div>;
  if (!diffs) return <div className="text-sm text-gray-500">Loading drift report…</div>;
  if (diffs.length === 0) return <div className="text-sm text-gray-500">No differences reported.</div>;
  return (
    <div className="space-y-3">
      {diffs.map((d) => (
        <div key={d.diffName}>
          <div className="text-xs font-semibold text-gray-700 mb-1">{d.diffName}</div>
          <table className="w-full text-xs border border-gray-200">
            <thead className="bg-gray-50 text-gray-600">
              <tr>
                <th className="text-left p-1">Path</th>
                <th className="text-left p-1">Template</th>
                <th className="text-left p-1">Instance</th>
              </tr>
            </thead>
            <tbody>
              {d.diffData.map((row) => (
                <tr key={row.path} className="border-t border-gray-100 align-top">
                  <td className="p-1 font-mono">{row.path}</td>
                  <td className="p-1 font-mono break-all">{JSON.stringify(row.data.template)}</td>
                  <td className="p-1 font-mono break-all">{JSON.stringify(row.data.instance)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

function TemplateDetail({
  base,
  template,
  ecNames,
  onSelect,
  onCopy,
}: {
  base: string;
  template: TemplateRow;
  ecNames: Record<string, string>;
  onSelect: (id: string) => void;
  onCopy: () => void;
}) {
  const [tab, setTab] = useState<"body" | "deps" | "applied">("body");
  const [body, setBody] = useState<BodyResponse | null>(null);
  const [bodyError, setBodyError] = useState<string | null>(null);
  const [openSetting, setOpenSetting] = useState<string | null>(null);
  const [driftTenant, setDriftTenant] = useState<string | null>(null);
  const applied = template.appliedOnTenants ?? [];

  useEffect(() => {
    setBody(null);
    setBodyError(null);
    setOpenSetting(null);
    setDriftTenant(null);
    let live = true;
    apiGet<BodyResponse>(
      `${base}/templates/${template.id}/body?type=${encodeURIComponent(template.type)}&context=${template.context}`
    )
      .then((res) => live && setBody(res))
      .catch((e) => live && setBodyError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [base, template.id, template.type, template.context]);

  const tabs: { key: typeof tab; label: string }[] = [
    { key: "body", label: "Configuration" },
    { key: "deps", label: "Dependencies" },
    { key: "applied", label: `Applied ECs (${applied.length})` },
  ];

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <div className="flex items-start justify-between gap-4 mb-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">{template.name}</h2>
          <div className="text-xs text-gray-500 font-mono">
            {template.type} · {template.context} · {template.id}
          </div>
          <div className="text-xs text-gray-500 mt-1">
            Created {fmtTime(template.createdOn)}
            {template.createdBy ? ` by ${template.createdBy}` : ""} · modified {fmtTime(template.lastModified)}
            {template.lastApplied ? ` · last applied ${fmtTime(template.lastApplied)}` : ""}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {template.type === "VENUE" && (
            <button
              onClick={onCopy}
              className="flex items-center gap-1 px-2 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700"
            >
              <Copy size={12} /> Copy with changes
            </button>
          )}
          {template.isEnforced && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-purple-100 text-purple-800">enforced</span>
          )}
          <DriftBadge status={template.driftStatus} />
        </div>
      </div>

      <div className="flex gap-1 border-b border-gray-200 mb-3">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-3 py-1.5 text-sm -mb-px border-b-2 ${
              tab === t.key ? "border-blue-600 text-blue-700" : "border-transparent text-gray-600 hover:text-gray-900"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "body" && (
        <div>
          {bodyError && <div className="text-sm text-red-700">{bodyError}</div>}
          {!body && !bodyError && <div className="text-sm text-gray-500">Loading configuration…</div>}
          {body?.error && <div className="text-sm text-red-700">{errorText(body.error)}</div>}
          {body && !body.error && <Json value={body.body} />}
          {body?.settings && (
            <div className="mt-4">
              <h3 className="text-sm font-semibold text-gray-800 mb-1">
                Venue settings ({Object.keys(body.settings).length})
              </h3>
              <p className="text-xs text-gray-500 mb-2">
                A venue template's own body is only name and address; everything else lives in these sub-resources.
              </p>
              <div className="divide-y divide-gray-100 border border-gray-200 rounded">
                {Object.entries(body.settings).map(([name, value]) => {
                  const failed = !!value && typeof value === "object" && "_error" in (value as object);
                  return (
                    <div key={name}>
                      <button
                        onClick={() => setOpenSetting(openSetting === name ? null : name)}
                        className="w-full flex items-center gap-1 px-2 py-1 text-sm text-left hover:bg-gray-50"
                      >
                        {openSetting === name ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                        <span className="font-mono">{name}</span>
                        {failed && <span className="text-xs text-red-700 ml-2">read failed</span>}
                      </button>
                      {openSetting === name && (
                        <div className="px-2 pb-2">
                          <Json value={value} />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {tab === "deps" && (
        <div>
          <p className="text-xs text-gray-500 mb-2">
            Direct children, loaded as you expand. Click a name to open that template.
          </p>
          <DependencyNode key={template.id} row={template} base={base} onSelect={onSelect} depth={0} />
        </div>
      )}

      {tab === "applied" && (
        <div>
          {applied.length === 0 && <div className="text-sm text-gray-500">Not applied to any EC.</div>}
          <ul className="space-y-1 mb-3">
            {applied.map((t) => (
              <li key={t} className="flex items-center gap-2 text-sm">
                <span className="text-gray-900">{ecNames[t] ?? t}</span>
                <span className="text-xs font-mono text-gray-400">{t}</span>
                <button
                  onClick={() => setDriftTenant(driftTenant === t ? null : t)}
                  className="text-xs text-blue-700 hover:underline"
                >
                  {driftTenant === t ? "hide drift" : "drift report"}
                </button>
              </li>
            ))}
          </ul>
          {driftTenant && <DriftReport base={base} template={template} tenantId={driftTenant} />}
        </div>
      )}
    </div>
  );
}

/**
 * Template Tool — browse an MSP's RUCKUS ONE configuration templates.
 *
 * Read-only for now: the list, each template's full configuration, its
 * dependency tree and per-EC drift. Copying a template to another MSP or EC
 * is the goal; this is the step that shows what a copy would have to carry.
 */
export default function TemplateTool() {
  const { activeControllerId, activeControllerType, activeControllerSubtype } = useAuth();
  const isR1 = activeControllerType === "RuckusONE";
  const isMsp = activeControllerSubtype === "MSP";
  const base = `${API_BASE_URL}/msp-templates/${activeControllerId}`;

  const [data, setData] = useState<ListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [copying, setCopying] = useState<TemplateRow | null>(null);

  const load = useCallback(async () => {
    if (!activeControllerId || !isR1 || !isMsp) return;
    setLoading(true);
    setError(null);
    try {
      setData(await apiGet<ListResponse>(`${base}/templates`));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [activeControllerId, isR1, isMsp, base]);

  useEffect(() => {
    setData(null);
    setSelectedId(null);
    setCopying(null);
    load();
  }, [load]);

  const typeCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const t of data?.templates ?? []) counts[t.type] = (counts[t.type] ?? 0) + 1;
    return Object.entries(counts).sort(([a], [b]) => typeRank(a) - typeRank(b) || a.localeCompare(b));
  }, [data]);

  const rows = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return (data?.templates ?? [])
      .filter((t) => !typeFilter || t.type === typeFilter)
      .filter((t) => !q || t.name.toLowerCase().includes(q) || t.id.includes(q))
      .sort((a, b) => typeRank(a.type) - typeRank(b.type) || a.name.localeCompare(b.name));
  }, [data, filter, typeFilter]);

  const selected = data?.templates.find((t) => t.id === selectedId) ?? null;

  if (!isR1 || !isMsp) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-2 flex items-center gap-2">
          <LayoutTemplate /> Template Tool
        </h1>
        <div className="bg-amber-50 border border-amber-200 rounded p-4 text-sm text-amber-900">
          Configuration templates live on a RUCKUS ONE MSP tenant. Select an MSP controller to browse them.
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <LayoutTemplate /> Template Tool
            <span className="text-xs bg-blue-200 text-blue-900 px-1.5 py-0.5 rounded">BETA</span>
          </h1>
          <p className="text-sm text-gray-600">
            The MSP's configuration templates — what each one holds, what it depends on, and where it has been
            applied. Read-only.
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="flex items-center gap-1 px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Refresh
        </button>
      </div>

      {error && <div className="bg-red-50 border border-red-200 rounded p-3 text-sm text-red-800">{error}</div>}
      {data?.errors.map((e, i) => (
        <div key={i} className="bg-amber-50 border border-amber-200 rounded p-3 text-sm text-amber-900">
          {errorText(e)}
        </div>
      ))}

      {data && (
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter by name or id"
            className="px-2 py-1 text-sm border border-gray-300 rounded w-64"
          />
          <button
            onClick={() => setTypeFilter(null)}
            className={`text-xs px-2 py-1 rounded border ${
              typeFilter === null ? "bg-blue-600 text-white border-blue-600" : "border-gray-300 text-gray-700"
            }`}
          >
            All ({data.templates.length})
          </button>
          {typeCounts.map(([type, n]) => (
            <button
              key={type}
              onClick={() => setTypeFilter(typeFilter === type ? null : type)}
              className={`text-xs px-2 py-1 rounded border font-mono ${
                typeFilter === type ? "bg-blue-600 text-white border-blue-600" : "border-gray-300 text-gray-700"
              }`}
            >
              {type} ({n})
            </button>
          ))}
        </div>
      )}

      {loading && !data && <div className="text-sm text-gray-500">Loading templates…</div>}

      {data && copying && (
        <CopyPlanner
          key={copying.id}
          base={base}
          templateId={copying.id}
          templateName={copying.name}
          onClose={() => setCopying(null)}
        />
      )}

      {data && !copying && (
        <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] gap-4 items-start">
          <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-600 text-xs">
                <tr>
                  <th className="text-left p-2">Name</th>
                  <th className="text-left p-2">Type</th>
                  <th className="text-left p-2">Applied</th>
                  <th className="text-left p-2">Drift</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((t) => (
                  <tr
                    key={t.id}
                    onClick={() => setSelectedId(t.id)}
                    className={`border-t border-gray-100 cursor-pointer ${
                      t.id === selectedId ? "bg-blue-50" : "hover:bg-gray-50"
                    }`}
                  >
                    <td className="p-2">
                      {t.name}
                      {t.context === "REC" && (
                        <span className="ml-1 text-xs px-1 rounded bg-gray-100 text-gray-600">REC</span>
                      )}
                      {!data.readableTypes.includes(t.type) && (
                        <span className="ml-1 text-xs text-gray-400" title="No body reader mapped for this type yet">
                          (list only)
                        </span>
                      )}
                    </td>
                    <td className="p-2 font-mono text-xs text-gray-600">{t.type}</td>
                    <td className="p-2 text-gray-700">{t.appliedOnTenants?.length || ""}</td>
                    <td className="p-2">
                      <DriftBadge status={t.driftStatus} />
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={4} className="p-4 text-center text-gray-500">
                      {data.templates.length ? "Nothing matches the filter." : "This MSP has no templates."}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {selected ? (
            <TemplateDetail
              base={base}
              template={selected}
              ecNames={data.ecNames}
              onSelect={setSelectedId}
              onCopy={() => setCopying(selected)}
            />
          ) : (
            <div className="bg-white border border-dashed border-gray-300 rounded-lg p-8 text-center text-sm text-gray-500">
              Select a template to see its configuration, dependencies and drift.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
