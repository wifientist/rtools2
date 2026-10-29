import { useState, useEffect, useMemo } from "react";
import { useAuth } from "@/context/AuthContext";
import SingleVenueSelector from "@/components/SingleVenueSelector";
import MspEcPicker from "@/components/MspEcPicker";
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
type TagSource = "select" | "csv";
type CsvInput = "upload" | "paste";

interface CsvTagRow {
  line: number;
  serial: string;
  name: string;
  venue: string;
  tags: string[];
}

interface CsvParseResult {
  rows: CsvTagRow[];
  skipped: string[];
  error: string;
}

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
// CSV (R1 AP import format)
// ============================================================================

// Quote-aware so a Description with commas or a quoted "a;b" Tags cell survives.
function parseCsvLines(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"' && text[i + 1] === '"') {
        field += '"';
        i++;
      } else if (c === '"') {
        inQuotes = false;
      } else {
        field += c;
      }
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ",") {
      row.push(field);
      field = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += c;
    }
  }
  if (field || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((r) => r.some((f) => f.trim()));
}

// Headers: Access Point Name, Serial Number, Venue, Description,
// Access Point Group, Tags, Latitude, Longitude, Global Untagged VLAN ID.
// Only Serial Number and Tags are required; the rest is shown for context.
function parseR1ApCsv(text: string): CsvParseResult {
  const lines = parseCsvLines(text.replace(/^\uFEFF/, ""));
  if (lines.length === 0) return { rows: [], skipped: [], error: "The file is empty." };

  const header = lines[0].map((h) => h.trim().toLowerCase());
  const find = (pred: (h: string) => boolean) => header.findIndex(pred);
  const serialIdx = find((h) => h.startsWith("serial"));
  const tagsIdx = find((h) => h === "tags" || h === "tag");
  const nameIdx = find((h) => h === "access point name" || h === "ap name" || h === "name");
  const venueIdx = find((h) => h === "venue");
  if (serialIdx < 0 || tagsIdx < 0) {
    return {
      rows: [],
      skipped: [],
      error: 'Header row needs "Serial Number" and "Tags" columns (R1 AP import format).',
    };
  }

  const rows: CsvTagRow[] = [];
  const skipped: string[] = [];
  const seen = new Map<string, number>();
  lines.slice(1).forEach((cells, i) => {
    const line = i + 2;
    const serial = (cells[serialIdx] || "").trim();
    if (!serial) {
      skipped.push(`Line ${line}: no serial number`);
      return;
    }
    // R1 separates tags with ";" -- commas are accepted too.
    const tags = Array.from(
      new Set(
        (cells[tagsIdx] || "")
          .split(/[;,]/)
          .map((t) => t.trim())
          .filter(Boolean)
      )
    );
    if (tags.length === 0) {
      skipped.push(`Line ${line}: ${serial} has no tags (left as is)`);
      return;
    }
    const prev = seen.get(serial.toUpperCase());
    if (prev !== undefined) {
      skipped.push(`Line ${line}: ${serial} already on line ${prev} (ignored)`);
      return;
    }
    seen.set(serial.toUpperCase(), line);
    rows.push({
      line,
      serial,
      name: nameIdx >= 0 ? (cells[nameIdx] || "").trim() : "",
      venue: venueIdx >= 0 ? (cells[venueIdx] || "").trim() : "",
      tags,
    });
  });

  return {
    rows,
    skipped,
    error: rows.length === 0 && skipped.length === 0 ? "No data rows found." : "",
  };
}

const IDENTIFIER_HEADERS = new Set([
  "serial", "serial number", "serial_number", "ap", "ap name", "name", "access point name",
]);

// Pasted lines: "<AP name or serial><sep><tags>", where <sep> is the first tab
// (pasted from a spreadsheet) or else the first comma, and the tags are split by
// tagDelimiter -- so tags may themselves be comma-separated. A pasted R1 import
// CSV (with its header row) is handed to the R1 parser instead.
function parsePastedTags(
  text: string,
  tagDelimiter: string,
  aps: VenueAP[]
): CsvParseResult {
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  if (lines.length === 0) return { rows: [], skipped: [], error: "" };

  const first = lines[0].toLowerCase();
  if (first.includes("serial number") && first.includes("tags")) {
    return parseR1ApCsv(text);
  }

  const delim = tagDelimiter === "\\t" ? "\t" : tagDelimiter;
  if (!delim) return { rows: [], skipped: [], error: "Enter a tag delimiter." };

  const bySerial = new Map(aps.map((ap) => [ap.serial.toUpperCase(), ap]));
  const byName = new Map<string, VenueAP[]>();
  aps.forEach((ap) => {
    const key = (ap.name || "").trim().toLowerCase();
    if (key) byName.set(key, [...(byName.get(key) || []), ap]);
  });

  const rows: CsvTagRow[] = [];
  const skipped: string[] = [];
  const seen = new Map<string, number>();
  lines.forEach((raw, i) => {
    const line = i + 1;
    const sepIdx = raw.includes("\t") ? raw.indexOf("\t") : raw.indexOf(",");
    const ident = (sepIdx < 0 ? raw : raw.slice(0, sepIdx)).trim().replace(/^"|"$/g, "");
    const rest = sepIdx < 0 ? "" : raw.slice(sepIdx + 1).trim().replace(/^"|"$/g, "");

    if (i === 0 && IDENTIFIER_HEADERS.has(ident.toLowerCase())) return; // header row

    // Serial first, then AP name (names are not case-sensitive here, but must be unique)
    let ap = bySerial.get(ident.toUpperCase());
    if (!ap) {
      const named = byName.get(ident.toLowerCase()) || [];
      if (named.length > 1) {
        skipped.push(`Line ${line}: "${ident}" matches ${named.length} APs in this venue -- use the serial`);
        return;
      }
      ap = named[0];
    }
    if (!ap) {
      skipped.push(`Line ${line}: "${ident}" is not an AP name or serial in this venue`);
      return;
    }

    const tags = Array.from(
      new Set(rest.split(delim).map((t) => t.trim()).filter(Boolean))
    );
    if (tags.length === 0) {
      skipped.push(`Line ${line}: ${ap.name || ap.serial} has no tags (left as is)`);
      return;
    }
    const prev = seen.get(ap.serial);
    if (prev !== undefined) {
      skipped.push(`Line ${line}: ${ap.name || ap.serial} already on line ${prev} (ignored)`);
      return;
    }
    seen.set(ap.serial, line);
    rows.push({ line, serial: ap.serial, name: ap.name || "", venue: "", tags });
  });

  return { rows, skipped, error: "" };
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

  // Tag source: one tag list for selected APs, or per-AP tags from a CSV
  const [tagSource, setTagSource] = useState<TagSource>("select");
  const [csvFileName, setCsvFileName] = useState("");
  const [csvParse, setCsvParse] = useState<CsvParseResult | null>(null);
  const [csvInput, setCsvInput] = useState<CsvInput>("upload");
  const [pasteText, setPasteText] = useState("");
  const [tagDelimiter, setTagDelimiter] = useState(";");

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
  // An MSP has no venues of its own -- pick the EC first, and its venues follow.
  const [ecId, setEcId] = useState<string | null>(null);
  const [ecName, setEcName] = useState<string | null>(null);
  const effectiveTenantId = needsEcSelection
    ? ecId
    : activeController?.r1_tenant_id || null;

  // ---- Venue selection ----
  const handleVenueSelect = (id: string | null, venue: any) => {
    setVenueId(id);
    setVenueName(venue?.name || null);
    setAps([]);
    setRowSelection({});
    setCsvFileName("");
    setCsvParse(null);
    setPasteText("");
    setPreview(null);
    setResult(null);
    setError("");
  };

  // A venue belongs to one tenant, so changing the EC (or controller) drops it.
  useEffect(() => {
    handleVenueSelect(null, null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeControllerId, ecId]);

  // Switching controllers leaves the old controller's EC behind.
  useEffect(() => {
    setEcId(null);
    setEcName(null);
  }, [activeControllerId]);

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

  // ---- CSV ----
  // Pasted names resolve against the loaded venue APs, so re-run when they change
  const pasteParse = useMemo(
    () => (pasteText.trim() ? parsePastedTags(pasteText, tagDelimiter, aps) : null),
    [pasteText, tagDelimiter, aps]
  );
  const activeParse = csvInput === "paste" ? pasteParse : csvParse;
  const csvRows = activeParse?.rows ?? [];

  const handleCsvFile = async (file: File | undefined) => {
    setPreview(null);
    if (!file) {
      setCsvFileName("");
      setCsvParse(null);
      return;
    }
    setCsvFileName(file.name);
    setCsvParse(parseR1ApCsv(await file.text()));
  };

  // CSV rows naming a different venue than the one selected
  const csvOtherVenueRows = useMemo(() => {
    if (!venueName) return [];
    return csvRows.filter(
      (r) => r.venue && r.venue.toLowerCase() !== venueName.toLowerCase()
    );
  }, [csvRows, venueName]);

  const canPreview =
    tagSource === "csv"
      ? csvRows.length > 0
      : selectedAps.length > 0 && parsedTags.length > 0;

  const buildTagRequest = () => {
    const base = {
      controller_id: activeControllerId,
      venue_id: venueId,
      tenant_id: effectiveTenantId,
      mode: tagMode,
    };
    if (tagSource === "csv") {
      return {
        ...base,
        ap_tags: Object.fromEntries(csvRows.map((r) => [r.serial, r.tags])),
      };
    }
    return {
      ...base,
      tags: parsedTags,
      ap_serials: selectedAps.map((ap) => ap.serial),
    };
  };

  // ---- Preview ----
  const handlePreview = async () => {
    if (!canPreview) return;

    setPreviewLoading(true);
    setPreview(null);
    setError("");

    try {
      const response = await apiFetch(`${API_BASE_URL}/bulk-ap-tagging/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildTagRequest()),
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
    if (!canPreview) return;

    setError("");

    try {
      const response = await apiFetch(`${API_BASE_URL}/bulk-ap-tagging/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildTagRequest()),
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
        Set, add, or remove tags across multiple APs in a venue -- the same
        tags on every selected AP, or each AP's own tags from an R1 AP import CSV.
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

        {isR1 && needsEcSelection && activeControllerId && (
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-2">MSP-EC</label>
            <MspEcPicker
              controllerId={activeControllerId}
              ecId={ecId}
              ecName={ecName}
              onChange={(id, name) => {
                setEcId(id);
                setEcName(name);
              }}
            />
          </div>
        )}

        {!isR1 ? (
          <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
            <p className="text-sm text-yellow-800">
              Please select a RuckusONE controller to use this tool.
            </p>
          </div>
        ) : needsEcSelection && !ecId ? (
          <p className="text-sm text-amber-700">Select an MSP-EC first.</p>
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

      {/* ============= SECTION 2: Tag source ============= */}
      {venueId && aps.length > 0 && (
        <div className="flex gap-2 mb-3">
          {(["select", "csv"] as TagSource[]).map((src) => (
            <button
              key={src}
              onClick={() => {
                setTagSource(src);
                setPreview(null);
              }}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition ${
                tagSource === src
                  ? "bg-blue-600 text-white"
                  : "bg-gray-100 text-gray-700 hover:bg-gray-200"
              }`}
            >
              {src === "select" ? "Same tags on selected APs" : "Per-AP tags from CSV"}
            </button>
          ))}
        </div>
      )}

      {venueId && aps.length > 0 && tagSource === "csv" && (
        <div className="bg-white rounded-lg shadow p-5 mb-5">
          <div className="flex items-center justify-between mb-1">
            <h2 className="text-lg font-semibold">2. Per-AP Tags</h2>
            <div className="flex gap-1">
              {(["upload", "paste"] as CsvInput[]).map((m) => (
                <button
                  key={m}
                  onClick={() => {
                    setCsvInput(m);
                    setPreview(null);
                  }}
                  className={`px-3 py-1 rounded text-xs font-medium ${
                    csvInput === m
                      ? "bg-blue-600 text-white"
                      : "bg-gray-100 text-gray-700 hover:bg-gray-200"
                  }`}
                >
                  {m === "upload" ? "Upload R1 CSV" : "Paste"}
                </button>
              ))}
            </div>
          </div>

          {csvInput === "paste" ? (
            <>
              <p className="text-xs text-gray-500 mb-3">
                One AP per line: <strong>AP name or serial</strong>, then a comma (or tab, when
                pasting from a spreadsheet), then its tags separated by the tag delimiter. A
                pasted R1 import CSV with its header row also works.
              </p>
              <div className="flex items-center gap-2 mb-2">
                <label className="text-sm text-gray-700">Tag delimiter</label>
                <input
                  type="text"
                  value={tagDelimiter}
                  onChange={(e) => {
                    setTagDelimiter(e.target.value);
                    setPreview(null);
                  }}
                  className="w-16 px-2 py-1 border border-gray-300 rounded text-sm font-mono"
                />
                <span className="text-xs text-gray-400">e.g. ; | / or \t for tab</span>
              </div>
              <textarea
                value={pasteText}
                onChange={(e) => {
                  setPasteText(e.target.value);
                  setPreview(null);
                }}
                rows={8}
                placeholder={`AP-101, unit101${tagDelimiter || ";"}floor1\n123456789014, unit103`}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono"
              />
            </>
          ) : (<>
          <p className="text-xs text-gray-500 mb-3">
            R1 AP import format (Access Point Name, Serial Number, Venue, Description,
            Access Point Group, Tags, ...). Only Serial Number and Tags are used: each
            AP is matched by serial in <strong>{venueName}</strong> and gets the tags on
            its own row. Separate multiple tags with ";". Rows with an empty Tags cell
            are left alone.
          </p>
          <input
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => {
              handleCsvFile(e.target.files?.[0]);
              e.target.value = "";
            }}
            className="text-sm"
          />
          {csvFileName && (
            <span className="ml-2 text-sm text-gray-600">{csvFileName}</span>
          )}
          </>)}

          {activeParse?.error && (
            <p className="mt-3 text-sm text-red-600">{activeParse.error}</p>
          )}

          {activeParse && !activeParse.error && (
            <div className="mt-3 text-sm text-gray-600">
              <strong>{csvRows.length}</strong> AP row(s) with tags
              {activeParse.skipped.length > 0 && `, ${activeParse.skipped.length} skipped`}
            </div>
          )}

          {csvOtherVenueRows.length > 0 && (
            <div className="mt-2 bg-yellow-50 border border-yellow-200 rounded p-2 text-xs text-yellow-700">
              {csvOtherVenueRows.length} row(s) name a venue other than {venueName}
              {" "}(e.g. "{csvOtherVenueRows[0].venue}"). Only APs actually in {venueName} are
              updated; the rest show as not found in the preview.
            </div>
          )}

          {activeParse && activeParse.skipped.length > 0 && (
            <details className="mt-2 text-xs text-gray-500" open={csvInput === "paste"}>
              <summary className="cursor-pointer">Skipped rows</summary>
              <ul className="mt-1 ml-4 list-disc">
                {activeParse.skipped.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            </details>
          )}

          {csvRows.length > 0 && (
            <div className="mt-3 overflow-x-auto max-h-[300px] overflow-y-auto border rounded">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 sticky top-0">
                  <tr>
                    {["Line", "Serial", "AP Name", "Tags"].map((h) => (
                      <th
                        key={h}
                        className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {csvRows.map((r) => (
                    <tr key={r.serial}>
                      <td className="px-3 py-2 text-xs text-gray-400">{r.line}</td>
                      <td className="px-3 py-2 font-mono text-xs">{r.serial}</td>
                      <td className="px-3 py-2">{r.name || "—"}</td>
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap gap-1">
                          {r.tags.map((t, i) => (
                            <span
                              key={i}
                              className="inline-block bg-green-100 text-green-700 text-xs px-1.5 py-0.5 rounded"
                            >
                              {t}
                            </span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ============= SECTION 2: AP Selection ============= */}
      {venueId && aps.length > 0 && tagSource === "select" && (
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
      {((tagSource === "select" && selectedAps.length > 0) ||
        (tagSource === "csv" && csvRows.length > 0)) && (
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
            {tagSource === "csv" ? (
              <>
                {tagMode === "set" &&
                  "Replace each AP's existing tags with the tags on its CSV row."}
                {tagMode === "add" &&
                  "Add each CSV row's tags to its AP, keeping any existing tags."}
                {tagMode === "remove" &&
                  "Remove each CSV row's tags from its AP (other tags are kept)."}
              </>
            ) : (
              <>
                {tagMode === "set" &&
                  "Replace all existing tags on selected APs with the tags below."}
                {tagMode === "add" &&
                  "Add the tags below to selected APs, keeping any existing tags."}
                {tagMode === "remove" &&
                  "Remove the tags below from selected APs (other tags are kept)."}
              </>
            )}
          </p>

          {tagSource === "select" && (<>
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
          </>)}

          {/* Preview button */}
          <button
            onClick={handlePreview}
            disabled={previewLoading || !canPreview}
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
