import { useState, useMemo, useEffect } from "react";
import { useAuth } from "@/context/AuthContext";
import SingleVenueSelector from "@/components/SingleVenueSelector";
import MspEcPicker from "@/components/MspEcPicker";
import JobMonitorModal from "@/components/JobMonitorModal";
import V2PlanConfirmModal from "@/components/V2PlanConfirmModal";
import type { JobResult } from "@/components/JobMonitorModal";
import { apiFetch } from "@/utils/api";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

const LIST_HEADER = "ap_serial,ap_name,ap_group,wlan,vlan";

interface Row {
  ap_identifier: string;
  ap_group_name: string;
  wlan: string;
  vlan: string;
}

interface UnitAp {
  ap_serial: string;
  ap_name: string;
  current_ap_group: string;
  move: boolean;
  note: string;
}

interface UnitRow {
  unit: string;
  ap_group: string;
  ap_groups: string[];
  group_exists: boolean;
  group_note: string;
  wlans: string[];
  network_vlan: number | null;
  aps: UnitAp[];
  todo: string[];
  done: boolean;
}

interface OtherAp {
  ap_serial: string;
  ap_name: string;
  current_ap_group: string;
  ap_group: string;
  status: "group_only" | "ambiguous" | "no_match";
  note: string;
  todo: string[];
  done: boolean;
}

interface SuggestResult {
  venue_name: string;
  property_name: string;
  total_aps: number;
  total_networks: number;
  ap_groups: string[];
  units: UnitRow[];
  other_aps: OtherAp[];
  ambiguous_units: { unit: string; networks: string[] }[];
  property_wlans: { name: string; ssid: string; groups_bound: number; vlan: number | null }[];
  extra_wlans: string[];
  unknown_extra_wlans: string[];
  counts: Record<string, number>;
}

/** Split one CSV line, honouring double-quoted fields. */
function splitCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (quoted) {
      if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (c === '"') quoted = false;
      else cur += c;
    } else if (c === '"') quoted = true;
    else if (c === "," || c === "\t") { out.push(cur.trim()); cur = ""; }
    else cur += c;
  }
  out.push(cur.trim());
  return out;
}

function csvCell(v: unknown): string {
  const s = v == null ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/**
 * Parse the list.
 *
 * With a header, columns are found by name: ap_serial / ap_identifier /
 * ap_name (serial preferred), ap_group (or ap_group_name), wlan, vlan --
 * so the downloaded CSV comes straight back in. Without one, columns are
 * positional: ap[,ap_group[,wlan[,vlan]]].
 *
 * A row with an AP but no AP Group is `incomplete`: the page fills it from
 * the suggestions, so a pasted list of AP names is enough. A row with
 * neither is skipped and counted.
 */
function parseCsv(text: string): { rows: Row[]; incomplete: Row[]; errors: string[]; skipped: number } {
  const rows: Row[] = [];
  const incomplete: Row[] = [];
  const errors: string[] = [];
  let skipped = 0;
  let cols: Record<string, number> | null = null;

  text.split(/\r?\n/).forEach((raw, i) => {
    const line = raw.trim();
    if (!line || line.startsWith("#")) return;
    const parts = splitCsvLine(line);
    const lower = parts.map((p) => p.toLowerCase());
    if (!cols && rows.length + incomplete.length === 0
        && lower.some((h) => ["wlan", "ap_group", "ap_group_name", "ap_name", "ap_serial", "ap_identifier"].includes(h))) {
      cols = {};
      lower.forEach((h, idx) => { cols![h] = idx; });
      return;
    }
    const get = (...names: string[]) => {
      for (const n of names) {
        const idx = cols?.[n];
        if (idx !== undefined && parts[idx]) return parts[idx];
      }
      return "";
    };
    let row: Row;
    if (cols) {
      row = {
        ap_identifier: get("ap_serial", "ap_identifier", "ap_name"),
        ap_group_name: get("ap_group", "ap_group_name"),
        wlan: get("wlan", "ssid", "network"),
        vlan: get("vlan"),
      };
    } else {
      const [ap = "", group = "", wlan = "", vlan = ""] = parts;
      row = { ap_identifier: ap, ap_group_name: group, wlan, vlan };
    }
    if (row.vlan && !/^\d+$/.test(row.vlan)) {
      errors.push(`Line ${i + 1}: VLAN "${row.vlan}" is not a number`);
    }
    if (row.ap_group_name && (row.ap_identifier || row.wlan)) rows.push(row);
    else if (row.ap_identifier && !row.ap_group_name) incomplete.push(row);
    else skipped++;
  });

  return { rows, incomplete, errors, skipped };
}

type View = "todo" | "done" | "all";

/**
 * List rows for one unit: each WLAN on each of its groups (no AP), plus
 * each AP that has to move, with the unit's base WLAN.
 */
function unitRows(u: UnitRow, onlyMoves = true, vlan = ""): Row[] {
  const rows: Row[] = [];
  for (const g of u.ap_groups) {
    for (const w of u.wlans) rows.push({ ap_identifier: "", ap_group_name: g, wlan: w, vlan: w === u.wlans[0] ? vlan : "" });
  }
  for (const ap of u.aps) {
    if (onlyMoves && !ap.move) continue;
    rows.push({ ap_identifier: ap.ap_serial || ap.ap_name, ap_group_name: u.ap_group, wlan: u.wlans[0], vlan });
  }
  return rows;
}

function rowsToCsv(rows: Row[], names: Map<string, string>): string[] {
  return rows.map((r) =>
    [r.ap_identifier, names.get(r.ap_identifier) || "", r.ap_group_name, r.wlan, r.vlan].map(csvCell).join(","),
  );
}

function WlanActivator() {
  const { activeControllerId, activeControllerSubtype, controllers } = useAuth();

  const [venueId, setVenueId] = useState<string | null>(null);
  const [venueName, setVenueName] = useState<string | null>(null);
  const [csvText, setCsvText] = useState("");

  const [wlanPattern, setWlanPattern] = useState("{unit}@{property}");
  const [propertyName, setPropertyName] = useState("");
  const [groupPattern, setGroupPattern] = useState("{unit}");
  const [suggest, setSuggest] = useState<SuggestResult | null>(null);
  const [loadingSuggest, setLoadingSuggest] = useState(false);
  const [view, setView] = useState<View>("todo");

  // Property-wide WLANs every group in the list also gets.
  const [extraWlans, setExtraWlans] = useState<string[]>([]);
  const [extraInput, setExtraInput] = useState("");
  const [applyExtras, setApplyExtras] = useState(true);

  const [error, setError] = useState("");
  const [processing, setProcessing] = useState(false);
  const [v2JobId, setV2JobId] = useState<string | null>(null);
  const [showPlanModal, setShowPlanModal] = useState(false);
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [showJobModal, setShowJobModal] = useState(false);
  const [lastResult, setLastResult] = useState<JobResult | null>(null);

  const activeController = controllers.find((c: any) => c.id === activeControllerId);
  const needsEcSelection = activeControllerSubtype === "MSP";
  const [ecId, setEcId] = useState<string | null>(null);
  const [ecName, setEcName] = useState<string | null>(null);
  const effectiveTenantId = needsEcSelection ? ecId : activeController?.r1_tenant_id || null;

  useEffect(() => {
    setVenueId(null);
    setVenueName(null);
  }, [activeControllerId, ecId]);

  useEffect(() => {
    setEcId(null);
    setEcName(null);
  }, [activeControllerId]);

  const parsed = useMemo(() => parseCsv(csvText), [csvText]);

  // Pasted AP names: fill AP Group / WLANs from the AP's unit.
  const { unitByAp, otherByAp, apNames } = useMemo(() => {
    const unitByAp = new Map<string, UnitRow>();
    const otherByAp = new Map<string, OtherAp>();
    const apNames = new Map<string, string>();
    for (const u of suggest?.units || []) {
      for (const ap of u.aps) {
        if (ap.ap_serial) { unitByAp.set(ap.ap_serial.toLowerCase(), u); apNames.set(ap.ap_serial, ap.ap_name); }
        if (ap.ap_name) unitByAp.set(ap.ap_name.toLowerCase(), u);
      }
    }
    for (const ap of suggest?.other_aps || []) {
      if (ap.ap_serial) { otherByAp.set(ap.ap_serial.toLowerCase(), ap); apNames.set(ap.ap_serial, ap.ap_name); }
      if (ap.ap_name) otherByAp.set(ap.ap_name.toLowerCase(), ap);
    }
    return { unitByAp, otherByAp, apNames };
  }, [suggest]);

  const { rows, unfilled } = useMemo(() => {
    const filled: Row[] = [...parsed.rows];
    const unfilled: string[] = [];
    for (const r of parsed.incomplete) {
      const key = r.ap_identifier.toLowerCase();
      const u = unitByAp.get(key);
      const o = otherByAp.get(key);
      if (u) {
        // The pasted AP always goes in, even if it is already in place.
        filled.push(...unitRows(u, true, r.vlan).filter((x) => x.ap_identifier === ""));
        filled.push({ ap_identifier: r.ap_identifier, ap_group_name: u.ap_group, wlan: u.wlans[0], vlan: r.vlan });
      } else if (o && o.ap_group) {
        filled.push({ ap_identifier: r.ap_identifier, ap_group_name: o.ap_group, wlan: "", vlan: "" });
      } else {
        unfilled.push(r.ap_identifier);
      }
    }
    return { rows: filled, unfilled };
  }, [parsed, unitByAp, otherByAp]);

  // Rows sent to the plan: the list, plus each checked property-wide WLAN
  // on every group in it (AP-less rows; already-bound ones are skipped by
  // the plan).
  const planRows = useMemo(() => {
    if (!applyExtras || extraWlans.length === 0) return rows;
    const groups = Array.from(new Set(rows.map((r) => r.ap_group_name)));
    const have = new Set(rows.map((r) => `${r.ap_group_name}\u0000${r.wlan}`));
    const extra: Row[] = [];
    for (const g of groups) {
      for (const w of extraWlans) {
        if (!have.has(`${g}\u0000${w}`)) extra.push({ ap_identifier: "", ap_group_name: g, wlan: w, vlan: "" });
      }
    }
    return [...rows, ...extra];
  }, [rows, extraWlans, applyExtras]);

  // Preview in the plan's shape: one line per AP Group.
  const groups = useMemo(() => {
    const m = new Map<string, { wlans: Map<string, string>; aps: string[] }>();
    for (const r of planRows) {
      if (!m.has(r.ap_group_name)) m.set(r.ap_group_name, { wlans: new Map(), aps: [] });
      const g = m.get(r.ap_group_name)!;
      if (r.wlan) g.wlans.set(r.wlan, r.vlan);
      if (r.ap_identifier) g.aps.push(r.ap_identifier);
    }
    return Array.from(m.entries())
      .map(([name, g]) => ({
        name,
        wlans: Array.from(g.wlans.entries()).map(([w, v]) => (v ? `${w} (VLAN ${v})` : w)),
        aps: g.aps,
      }))
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  }, [planRows]);

  // `property` / `extras` undefined: let the backend detect them.
  const loadSuggestions = async (property?: string, extras?: string[]) => {
    if (!activeControllerId || !venueId) return;
    setError("");
    setLoadingSuggest(true);
    try {
      const qs = new URLSearchParams({ wlan_pattern: wlanPattern, group_pattern: groupPattern });
      if (effectiveTenantId) qs.set("tenant_id", effectiveTenantId);
      if (property !== undefined) qs.set("property_name", property);
      if (extras !== undefined) {
        if (extras.length) extras.forEach((w) => qs.append("extra_wlans", w));
        else qs.set("detect_extras", "false");
      }
      const res = await apiFetch(
        `${API_BASE_URL}/wlan-activator/v2/${activeControllerId}/venue/${venueId}/suggest?${qs}`,
      );
      if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        throw new Error(e.detail || "Failed to read the venue");
      }
      const data: SuggestResult = await res.json();
      setSuggest(data);
      setPropertyName(data.property_name);
      setExtraWlans(data.extra_wlans);
    } catch (e: any) {
      setError(e.message || "Failed to read the venue");
    } finally {
      setLoadingSuggest(false);
    }
  };

  // Picking a venue runs the default guess with everything detected.
  useEffect(() => {
    setSuggest(null);
    setExtraWlans([]);
    if (!venueId || !activeControllerId) return;
    loadSuggestions(undefined, undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [venueId, activeControllerId, effectiveTenantId]);

  const resuggest = () => loadSuggestions(propertyName, extraWlans);

  const toggleExtra = (name: string) =>
    setExtraWlans((cur) => (cur.includes(name) ? cur.filter((w) => w !== name) : [...cur, name]));

  const addExtra = () => {
    const name = extraInput.trim();
    if (name && !extraWlans.includes(name)) setExtraWlans([...extraWlans, name]);
    setExtraInput("");
  };

  const visibleUnits = useMemo(() => {
    const all = suggest?.units || [];
    if (view === "done") return all.filter((u) => u.done);
    if (view === "todo") return all.filter((u) => !u.done);
    return all;
  }, [suggest, view]);

  const todoUnits = useMemo(() => (suggest?.units || []).filter((u) => !u.done), [suggest]);
  const todoOtherAps = useMemo(() => (suggest?.other_aps || []).filter((a) => !a.done), [suggest]);

  const useTodo = () => {
    const out: Row[] = todoUnits.flatMap((u) => unitRows(u));
    for (const a of todoOtherAps) {
      if (a.ap_group) out.push({ ap_identifier: a.ap_serial || a.ap_name, ap_group_name: a.ap_group, wlan: "", vlan: "" });
    }
    setCsvText([LIST_HEADER, ...rowsToCsv(out, apNames)].join("\n"));
  };

  // Every unit and AP with its status, for fixing up in a spreadsheet; the
  // first five columns upload straight back.
  const downloadCsv = () => {
    if (!suggest) return;
    const lines = [`${LIST_HEADER},unit,done,needs,note`];
    for (const u of suggest.units) {
      const extra = [u.unit, u.done ? "yes" : "no", u.todo.join("; "), u.group_note];
      for (const r of unitRows(u, false)) {
        lines.push([r.ap_identifier, apNames.get(r.ap_identifier) || "", r.ap_group_name, r.wlan, "", ...extra]
          .map(csvCell).join(","));
      }
    }
    for (const a of suggest.other_aps) {
      lines.push([a.ap_serial, a.ap_name, a.ap_group, "", "", "", a.done ? "yes" : "no", a.todo.join("; "), a.note]
        .map(csvCell).join(","));
    }
    const blob = new Blob([lines.join("\n") + "\n"], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `wlan-activator-${(venueName || "venue").replace(/[^\w-]+/g, "_")}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = "";
    const reader = new FileReader();
    reader.onload = (ev) => setCsvText((ev.target?.result as string) || "");
    reader.onerror = () => setError("Failed to read file");
    reader.readAsText(file);
  };

  const handlePlan = async () => {
    setError("");
    if (!activeControllerId) return setError("Select a controller first");
    if (needsEcSelection && !ecId) return setError("Select an MSP-EC first");
    if (!venueId) return setError("Select a venue first");
    if (planRows.length === 0) return setError("Add at least one row with an AP Group");

    setProcessing(true);
    try {
      const res = await apiFetch(`${API_BASE_URL}/wlan-activator/v2/plan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          controller_id: activeControllerId,
          venue_id: venueId,
          tenant_id: effectiveTenantId,
          activations: planRows,
        }),
      });
      if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        throw new Error(e.detail || "Failed to create plan");
      }
      const data = await res.json();
      setV2JobId(data.job_id);
      setShowPlanModal(true);
    } catch (e: any) {
      setError(e.message || "Failed to create plan");
    } finally {
      setProcessing(false);
    }
  };

  const inputCls =
    "px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 text-sm font-mono";
  const tabCls = (v: View) =>
    `px-2 py-1 rounded ${view === v ? "bg-blue-100 text-blue-800 font-medium" : "text-gray-600 hover:bg-gray-100"}`;

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <h1 className="text-2xl font-bold text-gray-900 mb-1">WLAN Activator</h1>
      <p className="text-sm text-gray-600 mb-6">
        Put APs into AP Groups and activate each group's WLANs <strong>directly on that AP Group</strong>, with an
        optional VLAN per group. It never activates on All AP Groups. Missing AP Groups are created.
      </p>

      {activeControllerId && needsEcSelection && (
        <div className="mb-6">
          <label className="block text-sm font-medium text-gray-700 mb-2">MSP-EC</label>
          <MspEcPicker
            controllerId={activeControllerId}
            ecId={ecId}
            ecName={ecName}
            onChange={(id, name) => { setEcId(id); setEcName(name); }}
          />
        </div>
      )}

      <div className="mb-6">
        <label className="block text-sm font-medium text-gray-700 mb-2">Venue</label>
        {!activeControllerId ? (
          <p className="text-sm text-amber-700">Select a controller first.</p>
        ) : needsEcSelection && !ecId ? (
          <p className="text-sm text-amber-700">Select an MSP-EC first.</p>
        ) : (
          <SingleVenueSelector
            controllerId={activeControllerId}
            tenantId={effectiveTenantId}
            onVenueSelect={(id: string | null, v: any) => { setVenueId(id); setVenueName(v?.name || null); }}
            selectedVenueId={venueId}
          />
        )}
      </div>

      {/* What R1 has, and what is left to do */}
      {venueId && (
        <div className="mb-6 p-4 border border-gray-200 rounded-lg">
          <h2 className="text-sm font-medium text-gray-800 mb-1">What's in R1, and what's left to do</h2>
          <p className="text-xs text-gray-500 mb-3">
            One row per unit. Units come from WLANs whose name or SSID fits the pattern, and band variants such as
            907-2.4 and 907-5 belong to unit 907. A unit's AP Group is wherever its WLAN is already active, otherwise
            a group named for it, otherwise a new one. A unit is <em>done</em> when every WLAN it should carry
            (including the property-wide ones) is active on its group and its APs are in a group that carries its
            WLAN. Units with no AP in this venue still count.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <label className="text-xs text-gray-600">
              WLAN pattern
              <input className={`${inputCls} w-full mt-1`} value={wlanPattern}
                onChange={(e) => setWlanPattern(e.target.value)} disabled={loadingSuggest} />
            </label>
            <label className="text-xs text-gray-600">
              {"{property}"}
              <input className={`${inputCls} w-full mt-1`} value={propertyName}
                onChange={(e) => setPropertyName(e.target.value)} disabled={loadingSuggest}
                placeholder={venueName || "venue name"} />
            </label>
            <label className="text-xs text-gray-600">
              AP Group pattern
              <input className={`${inputCls} w-full mt-1`} value={groupPattern}
                onChange={(e) => setGroupPattern(e.target.value)} disabled={loadingSuggest} />
            </label>
          </div>

          {/* Property-wide WLANs */}
          {suggest && (
            <div className="mt-3 p-3 bg-gray-50 border border-gray-200 rounded">
              <div className="text-xs font-medium text-gray-700 mb-1">
                Property-wide WLANs every AP Group should also carry
              </div>
              {suggest.property_wlans.length === 0 && extraWlans.length === 0 && (
                <p className="text-xs text-gray-500">None detected (none is active on most of this venue's AP Groups).</p>
              )}
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                {Array.from(new Set([...suggest.property_wlans.map((w) => w.name), ...extraWlans])).map((name) => {
                  const info = suggest.property_wlans.find((w) => w.name === name);
                  return (
                    <label key={name} className="text-xs text-gray-700 flex items-center gap-1">
                      <input type="checkbox" checked={extraWlans.includes(name)} onChange={() => toggleExtra(name)} />
                      <span className="font-mono">{name}</span>
                      {info && <span className="text-gray-400">on {info.groups_bound} groups</span>}
                    </label>
                  );
                })}
              </div>
              <div className="flex gap-2 mt-2">
                <input className={`${inputCls} flex-1`} value={extraInput} placeholder="Add another WLAN (name or SSID)"
                  onChange={(e) => setExtraInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") addExtra(); }} />
                <button onClick={addExtra} className="px-3 py-2 text-sm rounded-md border border-gray-300 bg-white hover:bg-gray-50">
                  Add
                </button>
              </div>
              {suggest.unknown_extra_wlans.length > 0 && (
                <p className="text-xs text-amber-700 mt-1">Not found: {suggest.unknown_extra_wlans.join(", ")}</p>
              )}
            </div>
          )}

          <div className="flex flex-wrap gap-2 mt-3">
            <button
              onClick={resuggest}
              disabled={loadingSuggest || processing}
              className="px-3 py-2 text-sm rounded-md border border-gray-300 text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50"
            >
              {loadingSuggest ? "Reading R1…" : "Re-check against R1"}
            </button>
            <button
              onClick={useTodo}
              disabled={todoUnits.length + todoOtherAps.length === 0}
              className="px-3 py-2 text-sm rounded-md border border-blue-300 text-blue-700 bg-blue-50 hover:bg-blue-100 disabled:opacity-50"
            >
              Use the {todoUnits.length} units that need work
            </button>
            <button
              onClick={downloadCsv}
              disabled={!suggest || suggest.units.length + suggest.other_aps.length === 0}
              title="Every AP with its guess, status and what it needs, to fix up in a spreadsheet and upload below"
              className="px-3 py-2 text-sm rounded-md border border-gray-300 text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50"
            >
              Download CSV
            </button>
          </div>

          {suggest && (
            <div className="mt-3 text-xs">
              <div className="mb-2 text-gray-700">
                {suggest.counts.units} units ({suggest.counts.units_without_aps} with no AP in this venue yet) ·{" "}
                {suggest.total_aps} APs ·{" "}
                <span className="text-green-700">{suggest.counts.units_done} units done</span> ·{" "}
                <span className="text-amber-700">{suggest.counts.units_todo} need work</span>
                {suggest.counts.other_aps > 0 && (
                  <span className="text-gray-500"> · {suggest.counts.other_aps} APs tied to no unit</span>
                )}
              </div>
              {suggest.counts.units === 0 && (
                <p className="text-amber-700 mb-1">
                  No WLAN name or SSID fits "{wlanPattern.replace("{property}", suggest.property_name)}". Check the
                  pattern and the {"{property}"} value.
                </p>
              )}
              {suggest.ambiguous_units.length > 0 && (
                <p className="text-amber-700 mb-1">
                  Units claimed by more than one WLAN (left out):{" "}
                  {suggest.ambiguous_units.slice(0, 5).map((u) => `${u.unit} (${u.networks.join(" / ")})`).join("; ")}
                </p>
              )}

              <div className="flex gap-1 mb-1">
                <button className={tabCls("todo")} onClick={() => setView("todo")}>Needs work ({suggest.counts.units_todo})</button>
                <button className={tabCls("done")} onClick={() => setView("done")}>Done ({suggest.counts.units_done})</button>
                <button className={tabCls("all")} onClick={() => setView("all")}>All ({suggest.counts.units})</button>
              </div>
              {visibleUnits.length === 0 ? (
                <p className="text-gray-500 p-2">{view === "todo" ? "Every unit is in place." : "No units."}</p>
              ) : (
                <div className="max-h-80 overflow-y-auto border border-gray-200 rounded bg-white">
                  <table className="min-w-full">
                    <thead className="bg-gray-50 sticky top-0">
                      <tr>
                        {["Unit", "AP Group", "WLANs", "APs", "what's needed"].map((h) => (
                          <th key={h} className="px-2 py-1 text-left font-medium text-gray-600">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {visibleUnits.map((u) => (
                        <tr key={u.unit} className="align-top">
                          <td className="px-2 py-1 font-mono text-gray-900">{u.unit}</td>
                          <td className="px-2 py-1 font-mono text-blue-700" title={u.group_note}>
                            {u.ap_groups.join(", ")}
                            {!u.group_exists && <span className="ml-1 text-green-700">(new)</span>}
                          </td>
                          <td className="px-2 py-1 font-mono text-gray-700">
                            {u.wlans.map((w) => w.split("@")[0]).join(", ")}
                            {u.network_vlan != null && <span className="text-gray-400"> · vlan {u.network_vlan}</span>}
                          </td>
                          <td className="px-2 py-1 font-mono text-gray-500">
                            {u.aps.length === 0 ? <span className="text-gray-400">none</span>
                              : u.aps.map((a) => a.ap_name || a.ap_serial).join(", ")}
                          </td>
                          <td className="px-2 py-1">
                            {u.done ? <span className="text-green-700">done</span>
                              : <span className="text-gray-800">{u.todo.join(" · ")}</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {todoOtherAps.length > 0 && (
                <div className="mt-3">
                  <div className="text-gray-700 mb-1">APs tied to no unit WLAN ({todoOtherAps.length})</div>
                  <div className="max-h-48 overflow-y-auto border border-gray-200 rounded bg-white">
                    <table className="min-w-full">
                      <tbody className="divide-y divide-gray-100">
                        {todoOtherAps.map((a) => (
                          <tr key={a.ap_serial || a.ap_name}>
                            <td className="px-2 py-1 font-mono text-gray-900">{a.ap_name || a.ap_serial}</td>
                            <td className="px-2 py-1 font-mono text-gray-500">{a.current_ap_group || "default"}</td>
                            <td className="px-2 py-1 font-mono text-blue-700">{a.ap_group}</td>
                            <td className="px-2 py-1 text-gray-700">{a.todo.length ? a.todo.join(" · ") : a.note}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* The list */}
      <div className="mb-4">
        <label className="block text-sm font-medium text-gray-700 mb-2">AP → AP Group → WLAN (→ VLAN) list</label>
        <input
          type="file"
          accept=".csv,.txt"
          onChange={handleFile}
          disabled={processing}
          className="mb-2 block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4
            file:rounded-md file:border-0 file:text-sm file:font-semibold
            file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100 disabled:opacity-50"
        />
        <textarea
          value={csvText}
          onChange={(e) => setCsvText(e.target.value)}
          disabled={processing}
          rows={10}
          placeholder={`${LIST_HEADER}\n123456789012,101-R550,101,101@Fieldhouse,2396\n,,102,102@Fieldhouse,\n\n…or just paste AP names, one per line`}
          className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm text-sm font-mono
            focus:ring-blue-500 focus:border-blue-500 disabled:bg-gray-100"
        />
        <p className="text-xs text-gray-500 mt-1">
          Columns <span className="font-mono">ap_serial / ap_name, ap_group, wlan, vlan</span>. With a header they are
          matched by name, so the downloaded CSV uploads as-is. Without one they are positional. A line with just an
          AP name gets its unit's AP Group and WLANs filled in from R1. A blank AP means the group is created and activated
          with no AP moved. <strong>vlan</strong> sets that WLAN's VLAN on that AP Group; leave it blank to inherit
          the network's VLAN. A WLAN is matched by network name first, then by SSID.
        </p>
        {extraWlans.length > 0 && (
          <label className="mt-2 flex items-center gap-2 text-sm text-gray-700">
            <input type="checkbox" checked={applyExtras} onChange={(e) => setApplyExtras(e.target.checked)} />
            Also activate {extraWlans.length} property-wide WLAN(s) on every AP Group in the list
            <span className="text-xs text-gray-500 font-mono">({extraWlans.join(", ")})</span>
          </label>
        )}
      </div>

      {(parsed.errors.length > 0 || parsed.skipped > 0 || unfilled.length > 0) && (
        <div className="mb-4 p-2 bg-amber-50 border border-amber-200 rounded text-xs text-amber-800">
          {unfilled.length > 0 && (
            <div>
              {unfilled.length} AP(s) with no AP Group found to fill in, so they are skipped. Add their group by hand:{" "}
              <span className="font-mono">{unfilled.slice(0, 8).join(", ")}{unfilled.length > 8 && " …"}</span>
            </div>
          )}
          {parsed.skipped > 0 && <div>{parsed.skipped} row(s) with no AP Group are skipped.</div>}
          {parsed.errors.slice(0, 5).map((e, i) => <div key={i}>{e}</div>)}
          {parsed.errors.length > 5 && <div>…and {parsed.errors.length - 5} more</div>}
        </div>
      )}

      {groups.length > 0 && (
        <div className="mb-4 p-3 bg-blue-50 border border-blue-200 rounded">
          <div className="text-sm text-blue-900 font-medium mb-2">
            {groups.length} AP Groups · {rows.filter((r) => r.ap_identifier).length} APs
          </div>
          <div className="max-h-56 overflow-y-auto border border-blue-200 rounded bg-white">
            <table className="min-w-full text-xs">
              <thead className="bg-blue-50 sticky top-0">
                <tr>
                  <th className="px-2 py-1 text-left font-medium text-blue-900">AP Group</th>
                  <th className="px-2 py-1 text-left font-medium text-blue-900">WLANs</th>
                  <th className="px-2 py-1 text-right font-medium text-blue-900">APs</th>
                  <th className="px-2 py-1 text-left font-medium text-blue-900">Members</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-blue-100">
                {groups.map((g) => (
                  <tr key={g.name}>
                    <td className="px-2 py-1 font-mono text-gray-900">
                      {g.name}
                      {suggest && !suggest.ap_groups.includes(g.name) && <span className="ml-1 text-green-700">(new)</span>}
                    </td>
                    <td className="px-2 py-1 font-mono text-gray-700">{g.wlans.join(", ")}</td>
                    <td className="px-2 py-1 text-right text-gray-700">{g.aps.length}</td>
                    <td className="px-2 py-1 font-mono text-gray-500 truncate max-w-xs">
                      {g.aps.slice(0, 3).join(", ")}
                      {g.aps.length > 3 && ` …+${g.aps.length - 3}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-blue-700 mt-2">
            The plan step checks each row against R1. Anything already in place, including its VLAN, is skipped. It
            also flags DPSK WLANs with no DPSK service and groups that would go over 15 SSIDs.
          </p>
        </div>
      )}

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">{error}</div>
      )}

      <button
        onClick={handlePlan}
        disabled={processing || !venueId || planRows.length === 0}
        className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium
          hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {processing ? "Planning…" : "Plan WLAN Activation"}
      </button>

      {lastResult && (
        <div className="mt-4 p-3 bg-gray-50 border border-gray-200 rounded text-sm text-gray-700">
          Last run: {lastResult.status}
        </div>
      )}

      {v2JobId && (
        <V2PlanConfirmModal
          jobId={v2JobId}
          isOpen={showPlanModal}
          workflowName="wlan_activator"
          apiPrefix="/wlan-activator/v2"
          onClose={() => setShowPlanModal(false)}
          onConfirm={(id: string) => {
            setShowPlanModal(false);
            setCurrentJobId(id);
            setShowJobModal(true);
          }}
        />
      )}

      {currentJobId && (
        <JobMonitorModal
          jobId={currentJobId}
          isOpen={showJobModal}
          onClose={() => setShowJobModal(false)}
          onJobComplete={(r: JobResult) => setLastResult(r)}
        />
      )}
    </div>
  );
}

export default WlanActivator;
