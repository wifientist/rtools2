import { useState, useEffect, useMemo } from "react";
import { useAuth } from "@/context/AuthContext";
import SingleVenueSelector from "@/components/SingleVenueSelector";
import JobMonitorModal from "@/components/JobMonitorModal";
import type { JobResult } from "@/components/JobMonitorModal";
import {
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  flexRender,
  createColumnHelper,
  type FilterFn,
} from "@tanstack/react-table";
import { apiFetch } from "@/utils/api";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

// ============================================================================
// Types
// ============================================================================

type TagMode = "set" | "add" | "remove";

interface VenueAP {
  serial: string;
  name: string;
  model: string;
  status: string;
  ap_group_name: string;
  tags: string[];
}

interface APTagPreview {
  serial_number: string;
  ap_name: string;
  current_tags: string[];
  new_tags: string[];
  tags_added: string[];
  tags_removed: string[];
  changed: boolean;
  error: string | null;
}

interface TagPreviewResponse {
  mode: TagMode;
  total_aps: number;
  changed_count: number;
  unchanged_count: number;
  error_count: number;
  previews: APTagPreview[];
  warnings: string[];
}

// ============================================================================
// Component
// ============================================================================

const columnHelper = createColumnHelper<VenueAP>();

export default function BulkAPTagging() {
  const {
    activeControllerId,
    activeControllerType,
    activeControllerSubtype,
    controllers,
  } = useAuth();

  // Venue selection
  const [venueId, setVenueId] = useState<string | null>(null);
  const [venueName, setVenueName] = useState<string | null>(null);

  // AP data
  const [aps, setAps] = useState<VenueAP[]>([]);
  const [loading, setLoading] = useState(false);

  // Filtering
  const [globalFilter, setGlobalFilter] = useState("");
  const [regexFilter, setRegexFilter] = useState("");
  const [regexError, setRegexError] = useState("");
  const [apGroupFilter, setApGroupFilter] = useState("all");

  // Table selection
  const [rowSelection, setRowSelection] = useState<Record<string, boolean>>({});

  // Tag operation
  const [tagMode, setTagMode] = useState<TagMode>("set");
  const [tagInput, setTagInput] = useState("");

  // Preview / Apply
  const [preview, setPreview] = useState<TagPreviewResponse | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [error, setError] = useState("");

  // Job monitor
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [showJobModal, setShowJobModal] = useState(false);
  const [result, setResult] = useState<any>(null);

  // Controller info
  const activeController = controllers.find((c) => c.id === activeControllerId);
  const needsEcSelection = activeControllerSubtype === "MSP";
  const effectiveTenantId = needsEcSelection
    ? null
    : activeController?.r1_tenant_id || null;

  // ---- Venue selection ----
  const handleVenueSelect = (id: string | null, venue: any) => {
    setVenueId(id);
    setVenueName(venue?.name || null);
    setAps([]);
    setRowSelection({});
    setPreview(null);
    setResult(null);
    setError("");
  };

  // ---- Fetch APs when venue changes ----
  useEffect(() => {
    if (!venueId || !activeControllerId) return;

    const fetchAps = async () => {
      setLoading(true);
      try {
        const url = effectiveTenantId
          ? `${API_BASE_URL}/bulk-ap-tagging/${activeControllerId}/venue/${venueId}/aps?tenant_id=${effectiveTenantId}`
          : `${API_BASE_URL}/bulk-ap-tagging/${activeControllerId}/venue/${venueId}/aps`;

        const response = await apiFetch(url);
        if (response.ok) {
          const data = await response.json();
          setAps(data.aps || []);
        } else {
          const err = await response.json().catch(() => ({}));
          setError(err.detail || "Failed to fetch APs");
        }
      } catch (err) {
        console.error("Failed to fetch APs:", err);
        setError("Failed to fetch APs");
      } finally {
        setLoading(false);
      }
    };

    fetchAps();
  }, [venueId, activeControllerId, effectiveTenantId]);

  // ---- Unique AP groups for dropdown ----
  const apGroups = useMemo(() => {
    const groups = new Set<string>();
    aps.forEach((ap) => {
      if (ap.ap_group_name) groups.add(ap.ap_group_name);
    });
    return Array.from(groups).sort();
  }, [aps]);

  // ---- Filtered APs (regex + AP group applied before table) ----
  const filteredAps = useMemo(() => {
    let result = aps;

    // AP Group filter
    if (apGroupFilter !== "all") {
      result = result.filter((ap) => ap.ap_group_name === apGroupFilter);
    }

    // Regex filter on AP name
    if (regexFilter) {
      try {
        const regex = new RegExp(regexFilter, "i");
        result = result.filter((ap) => regex.test(ap.name || ""));
        setRegexError("");
      } catch {
        // invalid regex, show all
        setRegexError("Invalid regex");
      }
    } else {
      setRegexError("");
    }

    return result;
  }, [aps, apGroupFilter, regexFilter]);

  // ---- Custom global filter that searches across text columns + tags ----
  const globalFilterFn: FilterFn<VenueAP> = (row, _columnId, filterValue) => {
    const search = (filterValue as string).toLowerCase();
    const ap = row.original;
    return (
      (ap.name || "").toLowerCase().includes(search) ||
      (ap.serial || "").toLowerCase().includes(search) ||
      (ap.model || "").toLowerCase().includes(search) ||
      (ap.ap_group_name || "").toLowerCase().includes(search) ||
      (ap.tags || []).some((t) => t.toLowerCase().includes(search))
    );
  };

  // ---- TanStack Table ----
  const columns = useMemo(
    () => [
      columnHelper.display({
        id: "select",
        header: ({ table }) => (
          <input
            type="checkbox"
            checked={table.getIsAllPageRowsSelected()}
            onChange={table.getToggleAllPageRowsSelectedHandler()}
          />
        ),
        cell: ({ row }) => (
          <input
            type="checkbox"
            checked={row.getIsSelected()}
            onChange={row.getToggleSelectedHandler()}
          />
        ),
        size: 40,
      }),
      columnHelper.accessor("name", { header: "AP Name", size: 200 }),
      columnHelper.accessor("serial", { header: "Serial", size: 140 }),
      columnHelper.accessor("model", { header: "Model", size: 120 }),
      columnHelper.accessor("status", {
        header: "Status",
        size: 80,
        cell: (info) => {
          const s = info.getValue();
          const color = s === "Online" ? "text-green-600" : "text-gray-400";
          return <span className={`text-xs font-medium ${color}`}>{s}</span>;
        },
      }),
      columnHelper.accessor("ap_group_name", { header: "AP Group", size: 140 }),
      columnHelper.accessor("tags", {
        header: "Tags",
        size: 250,
        cell: (info) => {
          const tags = info.getValue() || [];
          if (tags.length === 0)
            return <span className="text-gray-300 text-xs">—</span>;
          return (
            <div className="flex flex-wrap gap-1">
              {tags.map((tag, i) => (
                <span
                  key={i}
                  className="inline-block bg-blue-100 text-blue-700 text-xs px-1.5 py-0.5 rounded"
                >
                  {tag}
                </span>
              ))}
            </div>
          );
        },
      }),
    ],
    []
  );

  const table = useReactTable({
    data: filteredAps,
    columns,
    state: { rowSelection, globalFilter },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onGlobalFilterChange: setGlobalFilter,
    globalFilterFn,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  // ---- Selected APs ----
  const selectedAps = useMemo(() => {
    return Object.keys(rowSelection)
      .filter((k) => rowSelection[k])
      .map((idx) => filteredAps[parseInt(idx)])
      .filter(Boolean);
  }, [rowSelection, filteredAps]);

  // ---- Tag distribution across selected APs ----
  const tagDistribution = useMemo(() => {
    const counts: Record<string, number> = {};
    selectedAps.forEach((ap) => {
      (ap.tags || []).forEach((tag) => {
        counts[tag] = (counts[tag] || 0) + 1;
      });
    });
    return Object.entries(counts).sort((a, b) => b[1] - a[1]);
  }, [selectedAps]);

  // ---- Parse tag input ----
  const parsedTags = useMemo(() => {
    return tagInput
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
  }, [tagInput]);

  // ---- Preview ----
  const handlePreview = async () => {
    if (selectedAps.length === 0 || parsedTags.length === 0) return;

    setPreviewLoading(true);
    setPreview(null);
    setError("");

    try {
      const response = await apiFetch(`${API_BASE_URL}/bulk-ap-tagging/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          controller_id: activeControllerId,
          venue_id: venueId,
          tenant_id: effectiveTenantId,
          mode: tagMode,
          tags: parsedTags,
          ap_serials: selectedAps.map((ap) => ap.serial),
        }),
      });

      if (response.ok) {
        const data = await response.json();
        setPreview(data);
      } else {
        const err = await response.json().catch(() => ({}));
        setError(err.detail || "Preview failed");
      }
    } catch (err) {
      setError("Preview failed");
    } finally {
      setPreviewLoading(false);
    }
  };

  // ---- Apply ----
  const handleApply = async () => {
    if (selectedAps.length === 0 || parsedTags.length === 0) return;

    setError("");

    try {
      const response = await apiFetch(`${API_BASE_URL}/bulk-ap-tagging/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          controller_id: activeControllerId,
          venue_id: venueId,
          tenant_id: effectiveTenantId,
          mode: tagMode,
          tags: parsedTags,
          ap_serials: selectedAps.map((ap) => ap.serial),
        }),
      });

      if (response.ok) {
        const data = await response.json();
        setCurrentJobId(data.job_id);
        setShowJobModal(true);
      } else {
        const err = await response.json().catch(() => ({}));
        setError(err.detail || "Apply failed");
      }
    } catch (err) {
      setError("Apply failed");
    }
  };

  // ---- Job completion ----
  const handleJobComplete = (jobResult: JobResult) => {
    setResult({
      summary: jobResult.summary,
      job_id: jobResult.job_id,
    });
    setPreview(null);
  };

  // ---- Render ----
  const isR1 = activeControllerType === "RuckusONE";

  return (
    <div className="max-w-7xl mx-auto p-6">
      <h1 className="text-2xl font-bold mb-1">Bulk AP Tagging</h1>
      <p className="text-sm text-gray-500 mb-5">
        Set, add, or remove tags across multiple APs in a venue.
      </p>

      {/* Error banner */}
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

      {/* Result banner */}
      {result && (
        <div className="mb-4 bg-green-50 border border-green-200 rounded-lg p-3 flex items-center justify-between">
          <p className="text-sm text-green-700">
            Tagging job completed.{" "}
            {result.summary?.updated !== undefined &&
              `Updated: ${result.summary.updated}, Failed: ${result.summary.failed}, Unchanged: ${result.summary.unchanged}`}
          </p>
          <button
            onClick={() => setResult(null)}
            className="text-green-400 hover:text-green-600 text-xs"
          >
            dismiss
          </button>
        </div>
      )}

      {/* ============= SECTION 1: Venue Selection ============= */}
      <div className="bg-white rounded-lg shadow p-5 mb-5">
        <h2 className="text-lg font-semibold mb-3">1. Select Venue</h2>

        {!isR1 ? (
          <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
            <p className="text-sm text-yellow-800">
              Please select a RuckusONE controller to use this tool.
            </p>
          </div>
        ) : (
          <SingleVenueSelector
            controllerId={activeControllerId}
            tenantId={effectiveTenantId}
            onVenueSelect={handleVenueSelect}
            selectedVenueId={venueId}
          />
        )}

        {venueId && venueName && (
          <div className="mt-3 text-sm text-gray-600">
            Selected: <strong>{venueName}</strong> ({aps.length} APs)
          </div>
        )}
      </div>

      {/* ============= SECTION 2: AP Selection ============= */}
      {venueId && aps.length > 0 && (
        <div className="bg-white rounded-lg shadow p-5 mb-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">2. Select APs</h2>
            <div className="flex items-center gap-3">
              <span className="text-sm text-gray-500">
                {selectedAps.length} of {filteredAps.length} selected
                {filteredAps.length !== aps.length &&
                  ` (${aps.length} total)`}
              </span>
              {selectedAps.length > 0 && (
                <button
                  onClick={() => setRowSelection({})}
                  className="text-xs text-gray-500 hover:text-gray-700 underline"
                >
                  Deselect All
                </button>
              )}
            </div>
          </div>

          {/* Filter controls */}
          <div className="flex flex-wrap gap-3 mb-3">
            <input
              type="text"
              placeholder="Search name, serial, model, tags..."
              value={globalFilter}
              onChange={(e) => setGlobalFilter(e.target.value)}
              className="flex-1 min-w-[200px] px-3 py-2 border border-gray-300 rounded-lg text-sm"
            />
            <div className="flex items-center gap-1">
              <input
                type="text"
                placeholder="Regex on AP name..."
                value={regexFilter}
                onChange={(e) => {
                  setRegexFilter(e.target.value);
                  setRowSelection({});
                }}
                className={`w-48 px-3 py-2 border rounded-lg text-sm ${
                  regexError ? "border-red-300" : "border-gray-300"
                }`}
              />
              {regexError && (
                <span className="text-xs text-red-500">{regexError}</span>
              )}
            </div>
            <select
              value={apGroupFilter}
              onChange={(e) => {
                setApGroupFilter(e.target.value);
                setRowSelection({});
              }}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
            >
              <option value="all">All AP Groups</option>
              {apGroups.map((g) => (
                <option key={g} value={g}>
                  {g}
                </option>
              ))}
            </select>
          </div>

          {/* AP Table */}
          {loading ? (
            <div className="text-center py-8 text-gray-400">Loading APs...</div>
          ) : filteredAps.length === 0 ? (
            <div className="text-center py-8 text-gray-400">
              No APs match the current filters.
            </div>
          ) : (
            <div className="overflow-x-auto max-h-[400px] overflow-y-auto border rounded">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 sticky top-0">
                  {table.getHeaderGroups().map((hg) => (
                    <tr key={hg.id}>
                      {hg.headers.map((header) => (
                        <th
                          key={header.id}
                          className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider"
                          style={{ width: header.getSize() }}
                        >
                          {header.isPlaceholder
                            ? null
                            : flexRender(
                                header.column.columnDef.header,
                                header.getContext()
                              )}
                        </th>
                      ))}
                    </tr>
                  ))}
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {table.getRowModel().rows.map((row) => (
                    <tr
                      key={row.id}
                      className={`hover:bg-gray-50 cursor-pointer ${
                        row.getIsSelected() ? "bg-blue-50" : ""
                      }`}
                      onClick={row.getToggleSelectedHandler()}
                    >
                      {row.getVisibleCells().map((cell) => (
                        <td key={cell.id} className="px-3 py-2 text-gray-700">
                          {flexRender(
                            cell.column.columnDef.cell,
                            cell.getContext()
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ============= SECTION 3: Tag Operation ============= */}
      {selectedAps.length > 0 && (
        <div className="bg-white rounded-lg shadow p-5 mb-5">
          <h2 className="text-lg font-semibold mb-3">3. Tag Operation</h2>

          {/* Mode selector */}
          <div className="flex gap-2 mb-4">
            {(["set", "add", "remove"] as TagMode[]).map((m) => (
              <button
                key={m}
                onClick={() => {
                  setTagMode(m);
                  setPreview(null);
                }}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition ${
                  tagMode === m
                    ? "bg-blue-600 text-white"
                    : "bg-gray-100 text-gray-700 hover:bg-gray-200"
                }`}
              >
                {m === "set"
                  ? "Set (Replace All)"
                  : m === "add"
                  ? "Add (Merge)"
                  : "Remove"}
              </button>
            ))}
          </div>

          {/* Mode description */}
          <p className="text-xs text-gray-500 mb-3">
            {tagMode === "set" &&
              "Replace all existing tags on selected APs with the tags below."}
            {tagMode === "add" &&
              "Add the tags below to selected APs, keeping any existing tags."}
            {tagMode === "remove" &&
              "Remove the tags below from selected APs (other tags are kept)."}
          </p>

          {/* Tag input */}
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Tags (comma-separated)
            </label>
            <input
              type="text"
              value={tagInput}
              onChange={(e) => {
                setTagInput(e.target.value);
                setPreview(null);
              }}
              placeholder="e.g. lobby, floor-1, outdoor"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
            />
            {parsedTags.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-2">
                {parsedTags.map((tag, i) => (
                  <span
                    key={i}
                    className="inline-block bg-green-100 text-green-700 text-xs px-2 py-0.5 rounded"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Tag distribution */}
          {tagDistribution.length > 0 && (
            <div className="mb-4 p-3 bg-gray-50 rounded-lg">
              <div className="text-xs font-medium text-gray-500 mb-1">
                Current tags across {selectedAps.length} selected AP(s):
              </div>
              <div className="flex flex-wrap gap-1">
                {tagDistribution.map(([tag, count]) => (
                  <span
                    key={tag}
                    className="inline-block bg-blue-50 text-blue-600 text-xs px-2 py-0.5 rounded"
                  >
                    {count}x {tag}
                  </span>
                ))}
              </div>
            </div>
          )}
          {tagDistribution.length === 0 && selectedAps.length > 0 && (
            <div className="mb-4 p-3 bg-gray-50 rounded-lg">
              <div className="text-xs text-gray-400">
                No existing tags on selected APs.
              </div>
            </div>
          )}

          {/* Preview button */}
          <button
            onClick={handlePreview}
            disabled={previewLoading || parsedTags.length === 0}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {previewLoading ? "Previewing..." : "Preview Changes"}
          </button>
        </div>
      )}

      {/* ============= SECTION 4: Preview Results ============= */}
      {preview && (
        <div className="bg-white rounded-lg shadow p-5 mb-5">
          <h2 className="text-lg font-semibold mb-3">4. Preview</h2>

          {/* Summary */}
          <div className="flex gap-4 mb-3 text-sm">
            <span className="text-gray-600">
              Total: <strong>{preview.total_aps}</strong>
            </span>
            <span className="text-green-600">
              Will change: <strong>{preview.changed_count}</strong>
            </span>
            <span className="text-gray-400">
              Unchanged: <strong>{preview.unchanged_count}</strong>
            </span>
            {preview.error_count > 0 && (
              <span className="text-red-600">
                Errors: <strong>{preview.error_count}</strong>
              </span>
            )}
          </div>

          {/* Warnings */}
          {preview.warnings.length > 0 && (
            <div className="mb-3 bg-yellow-50 border border-yellow-200 rounded p-2">
              {preview.warnings.map((w, i) => (
                <p key={i} className="text-xs text-yellow-700">
                  {w}
                </p>
              ))}
            </div>
          )}

          {/* Preview table */}
          {preview.previews.length > 0 && (
            <div className="overflow-x-auto max-h-[300px] overflow-y-auto border rounded mb-4">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 sticky top-0">
                  <tr>
                    <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                      AP Name
                    </th>
                    <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                      Serial
                    </th>
                    <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                      Current Tags
                    </th>
                    <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                      New Tags
                    </th>
                    <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                      Status
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {preview.previews.map((p) => (
                    <tr
                      key={p.serial_number}
                      className={
                        p.error
                          ? "bg-red-50"
                          : !p.changed
                          ? "bg-gray-50 text-gray-400"
                          : ""
                      }
                    >
                      <td className="px-3 py-2">{p.ap_name}</td>
                      <td className="px-3 py-2 font-mono text-xs">
                        {p.serial_number}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap gap-1">
                          {p.current_tags.length === 0 ? (
                            <span className="text-gray-300 text-xs">—</span>
                          ) : (
                            p.current_tags.map((t, i) => (
                              <span
                                key={i}
                                className={`inline-block text-xs px-1.5 py-0.5 rounded ${
                                  p.tags_removed.includes(t)
                                    ? "bg-red-100 text-red-600 line-through"
                                    : "bg-gray-100 text-gray-600"
                                }`}
                              >
                                {t}
                              </span>
                            ))
                          )}
                        </div>
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap gap-1">
                          {p.new_tags.length === 0 ? (
                            <span className="text-gray-300 text-xs">—</span>
                          ) : (
                            p.new_tags.map((t, i) => (
                              <span
                                key={i}
                                className={`inline-block text-xs px-1.5 py-0.5 rounded ${
                                  p.tags_added.includes(t)
                                    ? "bg-green-100 text-green-700 font-medium"
                                    : "bg-gray-100 text-gray-600"
                                }`}
                              >
                                {t}
                              </span>
                            ))
                          )}
                        </div>
                      </td>
                      <td className="px-3 py-2 text-xs">
                        {p.error ? (
                          <span className="text-red-600">{p.error}</span>
                        ) : p.changed ? (
                          <span className="text-green-600">Will change</span>
                        ) : (
                          <span className="text-gray-400">No change</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Apply button */}
          {preview.changed_count > 0 && (
            <button
              onClick={handleApply}
              className="px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700"
            >
              Apply Changes to {preview.changed_count} AP(s)
            </button>
          )}
        </div>
      )}

      {/* Job Monitor */}
      {currentJobId && (
        <JobMonitorModal
          jobId={currentJobId}
          isOpen={showJobModal}
          onClose={() => setShowJobModal(false)}
          onJobComplete={handleJobComplete}
        />
      )}
    </div>
  );
}
