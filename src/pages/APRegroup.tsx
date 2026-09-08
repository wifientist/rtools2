import { useState, useMemo, useEffect } from "react";
import { useAuth } from "@/context/AuthContext";
import SingleVenueSelector from "@/components/SingleVenueSelector";
import JobMonitorModal from "@/components/JobMonitorModal";
import V2PlanConfirmModal from "@/components/V2PlanConfirmModal";
import type { JobResult } from "@/components/JobMonitorModal";
import { apiFetch } from "@/utils/api";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

interface Row {
  ap_identifier: string;
  ap_group_name: string;
}

/**
 * Parse "ap_identifier,ap_group_name".
 *
 * Also accepts the 3-column list the Cloudpath importer produces
 * (unit,ap_identifier,ap_group_name) so it can be pasted straight across —
 * there the AP is column 2 and the group is column 3.
 */
function parseCsv(text: string): { rows: Row[]; errors: string[] } {
  const rows: Row[] = [];
  const errors: string[] = [];
  const lines = text.split("\n");

  lines.forEach((raw, i) => {
    const line = raw.trim();
    if (!line || line.startsWith("#")) return;
    const parts = line.split(",").map((p) => p.trim().replace(/^["']|["']$/g, ""));
    const first = (parts[0] || "").toLowerCase();
    if (i === 0 && (first === "ap_identifier" || first === "unit_number" || first === "unit")) {
      return; // header
    }
    let ap = "";
    let group = "";
    if (parts.length >= 3) {
      ap = parts[1];
      group = parts[2];
    } else if (parts.length === 2) {
      ap = parts[0];
      group = parts[1];
    } else {
      errors.push(`Line ${i + 1}: expected 2 columns, got ${parts.length}`);
      return;
    }
    if (!ap || !group) {
      errors.push(`Line ${i + 1}: missing AP or AP Group`);
      return;
    }
    rows.push({ ap_identifier: ap, ap_group_name: group });
  });

  return { rows, errors };
}

interface VenueAp {
  serial: string | null;
  name: string | null;
  model?: string | null;
  status?: string | null;
}

/**
 * Pull the unit out of an AP name by stripping a known prefix/postfix.
 *
 * This is the inverse of the Cloudpath importer, which has unit numbers and
 * builds names. Here the venue already has the names, so the unit is whatever
 * sits between the two fixtures. Returns null when the name doesn't fit,
 * so non-conforming APs are reported rather than silently mangled.
 */
function unitFromApName(
  apName: string, prefix: string, postfix: string,
): string | null {
  let s = apName;
  if (prefix) {
    if (!s.startsWith(prefix)) return null;
    s = s.slice(prefix.length);
  }
  if (postfix) {
    if (!s.endsWith(postfix)) return null;
    s = s.slice(0, s.length - postfix.length);
  }
  s = s.trim();
  return s || null;
}

/**
 * Guess the postfix shared by most AP names, e.g. "@PropertyName".
 * Only suggests one when a clear majority agrees, so a mixed venue is left
 * alone rather than half-matched.
 */
function detectApPostfix(aps: VenueAp[]): string {
  const tally = new Map<string, number>();
  let withAt = 0;
  for (const ap of aps) {
    const n = ap.name || "";
    const at = n.lastIndexOf("@");
    if (at <= 0) continue;
    withAt++;
    const suffix = n.slice(at);
    tally.set(suffix, (tally.get(suffix) || 0) + 1);
  }
  let best = "", bestN = 0;
  tally.forEach((n, suffix) => { if (n > bestN) { best = suffix; bestN = n; } });
  return withAt > 0 && bestN >= withAt / 2 ? best : "";
}

function APRegroup() {
  const { activeControllerId, activeControllerSubtype, controllers } = useAuth();

  const [venueId, setVenueId] = useState<string | null>(null);
  const [venueName, setVenueName] = useState<string | null>(null);
  const [csvText, setCsvText] = useState("");

  // Venue inventory, so the list can be seeded from what already exists
  // rather than typed out.
  const [venueAps, setVenueAps] = useState<VenueAp[]>([]);
  const [existingGroups, setExistingGroups] = useState<string[]>([]);
  const [loadingInventory, setLoadingInventory] = useState(false);

  // AP names and AP Group names are separate conventions and need not agree,
  // so each gets its own prefix/postfix — same split as the Cloudpath tool.
  const [apPrefix, setApPrefix] = useState("");
  const [apPostfix, setApPostfix] = useState("");
  const [groupPrefix, setGroupPrefix] = useState("");
  const [groupPostfix, setGroupPostfix] = useState("");
  const [error, setError] = useState("");
  const [processing, setProcessing] = useState(false);

  const [v2JobId, setV2JobId] = useState<string | null>(null);
  const [showPlanModal, setShowPlanModal] = useState(false);
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [showJobModal, setShowJobModal] = useState(false);
  const [lastResult, setLastResult] = useState<JobResult | null>(null);

  const activeController = controllers.find((c: any) => c.id === activeControllerId);
  const needsEcSelection = activeControllerSubtype === "MSP";
  const effectiveTenantId = needsEcSelection ? null : activeController?.r1_tenant_id || null;

  const { rows, errors } = useMemo(() => parseCsv(csvText), [csvText]);

  // Group preview — the same shape the backend plans: one unit per AP Group.
  const groups = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const r of rows) {
      if (!m.has(r.ap_group_name)) m.set(r.ap_group_name, []);
      m.get(r.ap_group_name)!.push(r.ap_identifier);
    }
    return Array.from(m.entries())
      .map(([name, aps]) => ({ name, aps }))
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  }, [rows]);

  // Pull the venue's APs and AP Groups so the list can be generated from
  // reality instead of guessed at.
  const loadInventory = async (resetPatterns = false) => {
    if (!activeControllerId || !venueId) return;
    setError("");
    setLoadingInventory(true);
    if (resetPatterns) {
      // Naming conventions are per-venue, so a newly chosen venue starts
      // clean and lets detection run again instead of inheriting the last.
      setApPrefix("");
      setApPostfix("");
      setVenueAps([]);
      setExistingGroups([]);
    }
    try {
      const qs = effectiveTenantId ? `?tenant_id=${effectiveTenantId}` : "";
      const res = await apiFetch(
        `${API_BASE_URL}/ap-regroup/v2/${activeControllerId}/venue/${venueId}/inventory${qs}`,
      );
      if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        throw new Error(e.detail || "Failed to load venue inventory");
      }
      const data = await res.json();
      const aps: VenueAp[] = data.aps || [];
      setVenueAps(aps);
      setExistingGroups((data.ap_groups || []).map((g: any) => g.name));
      // Suggest the postfix the AP names already share, so the common case
      // needs no typing at all.
      const detected = detectApPostfix(aps);
      if (detected && (resetPatterns || (!apPrefix && !apPostfix))) {
        setApPostfix(detected);
      }
    } catch (e: any) {
      setError(e.message || "Failed to load venue inventory");
    } finally {
      setLoadingInventory(false);
    }
  };

  // Every AP whose name fits the pattern, with the AP Group it would land in.
  const generated = useMemo(() => {
    const matched: { ap: string; unit: string; group: string }[] = [];
    const skipped: string[] = [];
    for (const ap of venueAps) {
      const name = ap.name || "";
      if (!name) continue;
      const unit = unitFromApName(name, apPrefix, apPostfix);
      if (!unit) {
        skipped.push(name);
        continue;
      }
      matched.push({
        ap: name,
        unit,
        group: `${groupPrefix}${unit}${groupPostfix}`,
      });
    }
    return { matched, skipped };
  }, [venueAps, apPrefix, apPostfix, groupPrefix, groupPostfix]);

  const applyGenerated = () => {
    const lines = [
      "ap_identifier,ap_group_name",
      ...generated.matched.map((m) => `${m.ap},${m.group}`),
    ];
    setCsvText(lines.join("\n"));
  };

  // Selecting a venue is the trigger -- no extra click. An empty venue just
  // yields an empty list, which is a fine resting state.
  useEffect(() => {
    if (!venueId || !activeControllerId) {
      setVenueAps([]);
      setExistingGroups([]);
      return;
    }
    loadInventory(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [venueId, activeControllerId, effectiveTenantId]);

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
    if (!venueId) return setError("Select a venue first");
    if (rows.length === 0) return setError("Add at least one ap_identifier,ap_group_name row");

    setProcessing(true);
    try {
      const res = await apiFetch(`${API_BASE_URL}/ap-regroup/v2/plan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          controller_id: activeControllerId,
          venue_id: venueId,
          tenant_id: effectiveTenantId,
          ap_assignments: rows,
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

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <h1 className="text-2xl font-bold text-gray-900 mb-1">AP Regroup</h1>
      <p className="text-sm text-gray-600 mb-6">
        Move APs into AP Groups from a CSV. Any AP Group that doesn't exist yet
        is created. APs are matched by <strong>serial number or name</strong>.
      </p>

      {/* Venue */}
      <div className="mb-6">
        <label className="block text-sm font-medium text-gray-700 mb-2">Venue</label>
        {!activeControllerId ? (
          <p className="text-sm text-amber-700">Select a controller first.</p>
        ) : (
          <SingleVenueSelector
            controllerId={activeControllerId}
            tenantId={effectiveTenantId}
            onVenueSelect={(id: string | null, v: any) => {
              setVenueId(id);
              setVenueName(v?.name || null);
            }}
            selectedVenueId={venueId}
          />
        )}
        {venueId && venueName && (
          <p className="mt-2 text-sm text-green-800 bg-green-50 border border-green-200 rounded p-2">
            Target venue: <strong>{venueName}</strong>
          </p>
        )}
      </div>

      {/* Build from the venue */}
      {venueId && (
        <div className="mb-6 p-4 border border-gray-200 rounded-lg">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-gray-800">
              Build the list from this venue
            </h2>
            <button
              onClick={() => loadInventory(false)}
              disabled={processing || loadingInventory}
              title="Re-read the venue's APs and AP Groups"
              className="px-3 py-2 text-sm rounded-md border border-gray-300 text-gray-700
                bg-white hover:bg-gray-50 disabled:opacity-50"
            >
              {loadingInventory ? "Loading…" : "Refresh"}
            </button>
          </div>

          {venueAps.length === 0 ? (
            <p className="text-xs text-gray-500">
              {loadingInventory
                ? "Reading the venue's APs and AP Groups…"
                : "No APs found in this venue — paste a list below instead."}
            </p>
          ) : (
            <>
              <p className="text-xs text-gray-600 mb-3">
                {venueAps.length} APs, {existingGroups.length} existing AP Groups.
              </p>

              <label className="block text-sm font-medium text-gray-700 mb-2">
                AP Name Pattern <span className="font-normal text-gray-500">
                  — strip these to find the unit
                </span>
              </label>
              <div className="flex items-center gap-2">
                <input
                  type="text" value={apPrefix}
                  onChange={(e) => setApPrefix(e.target.value)}
                  placeholder="Prefix" disabled={processing}
                  className="flex-1 px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 text-sm"
                />
                <span className="text-gray-500 font-mono text-sm">{'{unit}'}</span>
                <input
                  type="text" value={apPostfix}
                  onChange={(e) => setApPostfix(e.target.value)}
                  placeholder="Postfix (e.g. @propertyName)" disabled={processing}
                  className="flex-1 px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 text-sm"
                />
              </div>

              <label className="block text-sm font-medium text-gray-700 mb-2 mt-4">
                AP Group Naming <span className="font-normal text-gray-500">
                  — built from that unit
                </span>
              </label>
              <div className="flex items-center gap-2">
                <input
                  type="text" value={groupPrefix}
                  onChange={(e) => setGroupPrefix(e.target.value)}
                  placeholder="Prefix (e.g. Unit-)" disabled={processing}
                  className="flex-1 px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 text-sm"
                />
                <span className="text-gray-500 font-mono text-sm">{'{unit}'}</span>
                <input
                  type="text" value={groupPostfix}
                  onChange={(e) => setGroupPostfix(e.target.value)}
                  placeholder="Postfix (e.g. -APs)" disabled={processing}
                  className="flex-1 px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 text-sm"
                />
                <button
                  onClick={applyGenerated}
                  disabled={processing || generated.matched.length === 0}
                  title="Replace the list below with these patterns"
                  className="shrink-0 px-3 py-2 text-sm rounded-md border border-blue-300
                    text-blue-700 bg-blue-50 hover:bg-blue-100 disabled:opacity-50"
                >
                  Update list
                </button>
              </div>

              {/* Live preview: AP -> unit -> AP Group, and what it will do */}
              <div className="mt-3 text-xs">
                <div className="mb-1 text-gray-700">
                  <strong>{generated.matched.length}</strong> APs match the
                  pattern
                  {generated.skipped.length > 0 && (
                    <span className="text-amber-700">
                      {" "}· {generated.skipped.length} do not and are left alone
                    </span>
                  )}
                </div>
                {generated.matched.length > 0 && (
                  <div className="max-h-48 overflow-y-auto border border-gray-200 rounded bg-white">
                    <table className="min-w-full">
                      <thead className="bg-gray-50 sticky top-0">
                        <tr>
                          <th className="px-2 py-1 text-left font-medium text-gray-600">AP name</th>
                          <th className="px-2 py-1 text-left font-medium text-gray-600">unit</th>
                          <th className="px-2 py-1 text-left font-medium text-gray-600">→ AP Group</th>
                          <th className="px-2 py-1 text-left font-medium text-gray-600">exists?</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-100">
                        {generated.matched.slice(0, 100).map((m) => (
                          <tr key={m.ap}>
                            <td className="px-2 py-1 font-mono text-gray-900">{m.ap}</td>
                            <td className="px-2 py-1 font-mono text-gray-600">{m.unit}</td>
                            <td className="px-2 py-1 font-mono text-blue-700">{m.group}</td>
                            <td className="px-2 py-1">
                              {existingGroups.includes(m.group) ? (
                                <span className="text-gray-500">existing</span>
                              ) : (
                                <span className="text-green-700">will create</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {generated.skipped.length > 0 && (
                  <p className="mt-1 text-amber-700 font-mono truncate">
                    skipped: {generated.skipped.slice(0, 6).join(", ")}
                    {generated.skipped.length > 6 && ` …+${generated.skipped.length - 6}`}
                  </p>
                )}
              </div>
            </>
          )}
        </div>
      )}

      {/* CSV */}
      <div className="mb-4">
        <label className="block text-sm font-medium text-gray-700 mb-2">
          AP → AP Group list
        </label>
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
          placeholder={"ap_identifier,ap_group_name\nR350-ABC123,Building-1\n1-101@propertyName,1-101-APs"}
          className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm text-sm font-mono
            focus:ring-blue-500 focus:border-blue-500 disabled:bg-gray-100"
        />
        <p className="text-xs text-gray-500 mt-1">
          Two columns: <span className="font-mono">ap_identifier,ap_group_name</span>.
          A three-column list from the Cloudpath importer also works — its AP is
          column 2 and its AP Group column 3.
        </p>
      </div>

      {errors.length > 0 && (
        <div className="mb-4 p-2 bg-amber-50 border border-amber-200 rounded text-xs text-amber-800">
          {errors.slice(0, 5).map((e, i) => <div key={i}>{e}</div>)}
          {errors.length > 5 && <div>…and {errors.length - 5} more</div>}
        </div>
      )}

      {/* Preview — one row per AP Group, matching how the plan is built */}
      {groups.length > 0 && (
        <div className="mb-4 p-3 bg-blue-50 border border-blue-200 rounded">
          <div className="text-sm text-blue-900 font-medium mb-2">
            {rows.length} APs into {groups.length} AP Groups
          </div>
          <div className="max-h-56 overflow-y-auto border border-blue-200 rounded bg-white">
            <table className="min-w-full text-xs">
              <thead className="bg-blue-50 sticky top-0">
                <tr>
                  <th className="px-2 py-1 text-left font-medium text-blue-900">AP Group</th>
                  <th className="px-2 py-1 text-right font-medium text-blue-900">APs</th>
                  <th className="px-2 py-1 text-left font-medium text-blue-900">Members</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-blue-100">
                {groups.map((g) => (
                  <tr key={g.name}>
                    <td className="px-2 py-1 font-mono text-gray-900">{g.name}</td>
                    <td className="px-2 py-1 text-right text-gray-700">{g.aps.length}</td>
                    <td className="px-2 py-1 font-mono text-gray-500 truncate max-w-md">
                      {g.aps.slice(0, 4).join(", ")}
                      {g.aps.length > 4 && ` …+${g.aps.length - 4}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-blue-700 mt-2">
            Which APs actually exist, and which AP Groups are new, is resolved
            against the venue in the plan step.
          </p>
        </div>
      )}

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
          {error}
        </div>
      )}

      <button
        onClick={handlePlan}
        disabled={processing || !venueId || rows.length === 0}
        className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium
          hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {processing ? "Planning…" : "Plan AP Regroup"}
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
          workflowName="ap_regroup"
          apiPrefix="/ap-regroup/v2"
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

export default APRegroup;
