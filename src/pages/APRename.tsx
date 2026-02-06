import { useState, useEffect } from "react";
import { useAuth } from "@/context/AuthContext";
import SingleVenueSelector from "@/components/SingleVenueSelector";
import JobMonitorModal from "@/components/JobMonitorModal";
import type { JobResult } from "@/components/JobMonitorModal";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

type RenameMode = "csv" | "regex" | "template";

interface APRenameItem {
  serial_number: string;
  current_name: string;
  new_name: string;
}

interface PreviewResponse {
  mode: RenameMode;
  total_aps: number;
  rename_count: number;
  unchanged_count: number;
  renames: APRenameItem[];
  unchanged: Array<{ serial: string; name: string; reason: string }>;
  errors: string[];
}

interface VenueAP {
  serial: string;
  name: string;
  model: string;
  status: string;
  ap_group_name: string;
}

function APRename() {
  const {
    activeControllerId,
    activeControllerType,
    activeControllerSubtype,
    controllers,
  } = useAuth();

  // Venue selection
  const [venueId, setVenueId] = useState<string | null>(null);
  const [venueName, setVenueName] = useState<string | null>(null);

  // Get effective tenant ID
  const activeController = controllers.find((c) => c.id === activeControllerId);
  const needsEcSelection = activeControllerSubtype === "MSP";
  const effectiveTenantId = needsEcSelection
    ? null
    : activeController?.r1_tenant_id || null;

  // Mode selection
  const [mode, setMode] = useState<RenameMode>("csv");

  // CSV mode state
  const [csvInput, setCsvInput] = useState<string>("");
  const [csvMappings, setCsvMappings] = useState<Array<{ serial_number: string; new_name: string }>>([]);
  const [csvError, setCsvError] = useState<string>("");

  // Regex mode state
  const [regexPattern, setRegexPattern] = useState<string>("");
  const [regexReplacement, setRegexReplacement] = useState<string>("");
  const [regexFilter, setRegexFilter] = useState<string>("");

  // Template mode state
  const [template, setTemplate] = useState<string>("{prefix}-{seq:03d}");
  const [templateVars, setTemplateVars] = useState<Record<string, string>>({ prefix: "AP" });
  const [templateStartSeq, setTemplateStartSeq] = useState<number>(1);
  const [templateFilter, setTemplateFilter] = useState<string>("");
  const [templateSortBy, setTemplateSortBy] = useState<string>("name");

  // Preview state
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  // Apply state
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState("");

  // Job monitor state
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [showJobModal, setShowJobModal] = useState(false);
  const [result, setResult] = useState<any>(null);

  // Venue APs (for display)
  const [venueAps, setVenueAps] = useState<VenueAP[]>([]);

  // Handle venue selection
  const handleVenueSelect = (id: string | null, venue: any) => {
    setVenueId(id);
    setVenueName(venue?.name || null);
    setPreview(null);
    setResult(null);
    setVenueAps([]);
  };

  // Fetch venue APs when venue changes
  useEffect(() => {
    if (!venueId || !activeControllerId) return;

    const fetchAps = async () => {
      try {
        const url = effectiveTenantId
          ? `${API_BASE_URL}/ap-rename/${activeControllerId}/venue/${venueId}/aps?tenant_id=${effectiveTenantId}`
          : `${API_BASE_URL}/ap-rename/${activeControllerId}/venue/${venueId}/aps`;

        const response = await fetch(url, { credentials: "include" });
        if (response.ok) {
          const data = await response.json();
          setVenueAps(data.aps || []);
        }
      } catch (err) {
        console.error("Failed to fetch APs:", err);
      }
    };

    fetchAps();
  }, [venueId, activeControllerId, effectiveTenantId]);

  // Download CSV
  const handleDownloadCSV = async () => {
    if (!venueId || !activeControllerId) return;

    const url = effectiveTenantId
      ? `${API_BASE_URL}/ap-rename/${activeControllerId}/venue/${venueId}/download-csv?tenant_id=${effectiveTenantId}`
      : `${API_BASE_URL}/ap-rename/${activeControllerId}/venue/${venueId}/download-csv`;

    window.open(url, "_blank");
  };

  // Parse CSV input
  const parseCSV = () => {
    setCsvError("");
    const lines = csvInput.trim().split("\n");
    const mappings: Array<{ serial_number: string; new_name: string }> = [];

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line) continue;

      // Skip header row
      if (
        i === 0 &&
        (line.toLowerCase().includes("serial") ||
          line.toLowerCase().includes("name"))
      ) {
        continue;
      }

      const parts = line.split(",").map((p) => p.trim());
      if (parts.length < 2) {
        setCsvError(`Line ${i + 1}: Expected at least 2 columns`);
        return;
      }

      // Handle 2-column (serial, new_name) or 3-column (serial, current, new_name) format
      const serial_number = parts[0];
      const new_name = parts.length >= 3 ? parts[2] : parts[1];

      if (!serial_number || !new_name) {
        setCsvError(`Line ${i + 1}: Missing serial or new name`);
        return;
      }

      mappings.push({ serial_number, new_name });
    }

    if (mappings.length === 0) {
      setCsvError("No valid mappings found");
      return;
    }

    setCsvMappings(mappings);
  };

  // Preview changes
  const handlePreview = async () => {
    if (!venueId || !activeControllerId) return;

    setPreviewLoading(true);
    setError("");
    setPreview(null);

    try {
      const body: any = {
        controller_id: activeControllerId,
        venue_id: venueId,
        mode,
      };

      if (effectiveTenantId) {
        body.tenant_id = effectiveTenantId;
      }

      if (mode === "csv") {
        if (csvMappings.length === 0) {
          throw new Error("Please parse CSV mappings first");
        }
        body.csv_input = { mappings: csvMappings };
      } else if (mode === "regex") {
        if (!regexPattern) {
          throw new Error("Please enter a regex pattern");
        }
        body.regex_input = {
          pattern: regexPattern,
          replacement: regexReplacement,
          filter_pattern: regexFilter || null,
        };
      } else if (mode === "template") {
        if (!template) {
          throw new Error("Please enter a template pattern");
        }
        body.template_input = {
          template,
          variables: templateVars,
          start_seq: templateStartSeq,
          filter_pattern: templateFilter || null,
          sort_by: templateSortBy,
        };
      }

      const response = await fetch(`${API_BASE_URL}/ap-rename/preview`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || "Preview failed");
      }

      const data = await response.json();
      setPreview(data);
    } catch (err: any) {
      setError(err.message || "Preview failed");
    } finally {
      setPreviewLoading(false);
    }
  };

  // Apply renames
  const handleApply = async () => {
    if (!preview || preview.renames.length === 0) return;

    setApplying(true);
    setError("");

    try {
      const body: any = {
        controller_id: activeControllerId,
        venue_id: venueId,
        renames: preview.renames,
        max_concurrent: 10,
      };

      if (effectiveTenantId) {
        body.tenant_id = effectiveTenantId;
      }

      const response = await fetch(`${API_BASE_URL}/ap-rename/apply`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || "Apply failed");
      }

      const data = await response.json();
      setCurrentJobId(data.job_id);
      setShowJobModal(true);
    } catch (err: any) {
      setError(err.message || "Apply failed");
    } finally {
      setApplying(false);
    }
  };

  // Handle job completion
  const handleJobComplete = (jobResult: JobResult) => {
    setResult({
      summary: jobResult.summary,
      job_id: jobResult.job_id,
    });
    setPreview(null);
  };

  const canPreview =
    venueId &&
    ((mode === "csv" && csvMappings.length > 0) ||
      (mode === "regex" && regexPattern) ||
      (mode === "template" && template));

  return (
    <div className="container mx-auto py-6 px-4">
      <h1 className="text-2xl font-bold mb-2">AP Rename Tool</h1>
      <p className="text-gray-600 mb-6">
        Bulk rename access points using CSV mapping, regex patterns, or templates.
      </p>

      {/* Venue Selection */}
      <div className="bg-white rounded-lg shadow p-4 mb-6">
        <h2 className="text-lg font-semibold mb-4">1. Select Venue</h2>

        {activeControllerType !== "RuckusONE" ? (
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
          <div className="mt-3 p-3 bg-green-50 border border-green-200 rounded-lg flex justify-between items-center">
            <span className="text-sm font-medium text-green-800">
              Selected: {venueName} ({venueAps.length} APs)
            </span>
            <button
              onClick={handleDownloadCSV}
              className="text-sm text-blue-600 hover:text-blue-800 underline"
            >
              Download Current Names CSV
            </button>
          </div>
        )}
      </div>

      {/* Error Display */}
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded mb-6">
          {error}
        </div>
      )}

      {venueId && (
        <>
          {/* Mode Selection */}
          <div className="bg-white rounded-lg shadow p-4 mb-6">
            <h2 className="text-lg font-semibold mb-4">2. Choose Rename Mode</h2>

            <div className="flex gap-4 mb-4">
              <button
                onClick={() => setMode("csv")}
                className={`px-4 py-2 rounded-lg border-2 ${
                  mode === "csv"
                    ? "border-blue-500 bg-blue-50 text-blue-700"
                    : "border-gray-200 hover:border-gray-300"
                }`}
              >
                CSV Mapping
              </button>
              <button
                onClick={() => setMode("regex")}
                className={`px-4 py-2 rounded-lg border-2 ${
                  mode === "regex"
                    ? "border-blue-500 bg-blue-50 text-blue-700"
                    : "border-gray-200 hover:border-gray-300"
                }`}
              >
                Regex Replace
              </button>
              <button
                onClick={() => setMode("template")}
                className={`px-4 py-2 rounded-lg border-2 ${
                  mode === "template"
                    ? "border-blue-500 bg-blue-50 text-blue-700"
                    : "border-gray-200 hover:border-gray-300"
                }`}
              >
                Template Pattern
              </button>
            </div>

            {/* CSV Mode */}
            {mode === "csv" && (
              <div className="space-y-4">
                <p className="text-sm text-gray-600">
                  Paste CSV with columns: <code className="bg-gray-100 px-1 rounded">serial_number, new_name</code>
                  {" "}or download the current names, edit, and paste back.
                </p>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <textarea
                      value={csvInput}
                      onChange={(e) => setCsvInput(e.target.value)}
                      placeholder="serial_number,new_name&#10;ABC123,Building-A-AP001&#10;DEF456,Building-A-AP002"
                      className="w-full h-48 px-3 py-2 border border-gray-300 rounded-lg font-mono text-sm"
                    />
                    {csvError && (
                      <p className="text-sm text-red-600 mt-1">{csvError}</p>
                    )}
                    <div className="flex gap-2 mt-2">
                      <button
                        onClick={parseCSV}
                        disabled={!csvInput.trim()}
                        className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 disabled:opacity-50 text-sm"
                      >
                        Parse CSV
                      </button>
                      <button
                        onClick={() => {
                          setCsvInput("");
                          setCsvMappings([]);
                          setCsvError("");
                        }}
                        className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm"
                      >
                        Clear
                      </button>
                    </div>
                  </div>

                  <div>
                    {csvMappings.length > 0 ? (
                      <div className="border border-green-200 bg-green-50 rounded-lg p-3">
                        <div className="text-sm font-medium text-green-800 mb-2">
                          {csvMappings.length} mapping(s) parsed
                        </div>
                        <div className="max-h-40 overflow-y-auto">
                          <table className="w-full text-xs">
                            <thead>
                              <tr className="text-left text-gray-600">
                                <th className="pb-1">Serial</th>
                                <th className="pb-1">New Name</th>
                              </tr>
                            </thead>
                            <tbody>
                              {csvMappings.slice(0, 10).map((m, i) => (
                                <tr key={i} className="border-t border-green-200">
                                  <td className="py-1 font-mono">{m.serial_number}</td>
                                  <td className="py-1">{m.new_name}</td>
                                </tr>
                              ))}
                              {csvMappings.length > 10 && (
                                <tr className="border-t border-green-200 text-gray-500">
                                  <td colSpan={2} className="py-1">
                                    ... and {csvMappings.length - 10} more
                                  </td>
                                </tr>
                              )}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    ) : (
                      <div className="border border-gray-200 bg-gray-50 rounded-lg p-3 text-sm text-gray-500">
                        No mappings parsed yet.
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* Regex Mode */}
            {mode === "regex" && (
              <div className="space-y-4">
                <p className="text-sm text-gray-600">
                  Use regex to find and replace patterns in existing AP names.
                  Supports backreferences like <code className="bg-gray-100 px-1 rounded">\1</code>, <code className="bg-gray-100 px-1 rounded">\2</code>.
                </p>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Find Pattern (regex)
                    </label>
                    <input
                      type="text"
                      value={regexPattern}
                      onChange={(e) => setRegexPattern(e.target.value)}
                      placeholder="e.g., ^AP-(\d+)$ or OldPrefix"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg font-mono text-sm"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Replace With
                    </label>
                    <input
                      type="text"
                      value={regexReplacement}
                      onChange={(e) => setRegexReplacement(e.target.value)}
                      placeholder="e.g., Building-A-AP-\1 or NewPrefix"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg font-mono text-sm"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Filter (optional) - Only rename APs matching this pattern
                  </label>
                  <input
                    type="text"
                    value={regexFilter}
                    onChange={(e) => setRegexFilter(e.target.value)}
                    placeholder="e.g., ^Unit-1 (only APs starting with Unit-1)"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg font-mono text-sm"
                  />
                </div>

                <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-sm">
                  <strong>Examples:</strong>
                  <ul className="list-disc list-inside mt-1 space-y-1 text-gray-700">
                    <li><code>AP-(\d+)</code> → <code>Building-A-AP-\1</code> : AP-001 becomes Building-A-AP-001</li>
                    <li><code>^OLD</code> → <code>NEW</code> : OLD-AP becomes NEW-AP</li>
                    <li><code>-(\d+)-(\d+)$</code> → <code>-FL\1-AP\2</code> : Unit-1-5 becomes Unit-FL1-AP5</li>
                  </ul>
                </div>
              </div>
            )}

            {/* Template Mode */}
            {mode === "template" && (
              <div className="space-y-4">
                <p className="text-sm text-gray-600">
                  Generate new names from a template pattern. APs are sorted and assigned sequential numbers.
                </p>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Template Pattern
                    </label>
                    <input
                      type="text"
                      value={template}
                      onChange={(e) => setTemplate(e.target.value)}
                      placeholder="{prefix}-{seq:03d}"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg font-mono text-sm"
                    />
                    <p className="text-xs text-gray-500 mt-1">
                      Variables: {"{prefix}"}, {"{seq}"}, {"{seq:03d}"} (zero-padded), {"{serial}"}, {"{current_name}"}
                    </p>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Prefix Variable
                    </label>
                    <input
                      type="text"
                      value={templateVars.prefix || ""}
                      onChange={(e) =>
                        setTemplateVars({ ...templateVars, prefix: e.target.value })
                      }
                      placeholder="AP"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Start Sequence
                    </label>
                    <input
                      type="number"
                      min="0"
                      value={templateStartSeq}
                      onChange={(e) => setTemplateStartSeq(parseInt(e.target.value) || 1)}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Sort By
                    </label>
                    <select
                      value={templateSortBy}
                      onChange={(e) => setTemplateSortBy(e.target.value)}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                    >
                      <option value="name">Current Name</option>
                      <option value="serial">Serial Number</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Filter (optional)
                    </label>
                    <input
                      type="text"
                      value={templateFilter}
                      onChange={(e) => setTemplateFilter(e.target.value)}
                      placeholder="Regex filter"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg font-mono text-sm"
                    />
                  </div>
                </div>

                <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-sm">
                  <strong>Template Examples:</strong>
                  <ul className="list-disc list-inside mt-1 space-y-1 text-gray-700">
                    <li><code>{"{prefix}-{seq:03d}"}</code> → AP-001, AP-002, AP-003...</li>
                    <li><code>Building-A-FL1-{"{seq}"}</code> → Building-A-FL1-1, Building-A-FL1-2...</li>
                    <li><code>{"{prefix}-{serial}"}</code> → AP-ABC123, AP-DEF456...</li>
                  </ul>
                </div>
              </div>
            )}
          </div>

          {/* Preview Button */}
          <div className="bg-white rounded-lg shadow p-4 mb-6">
            <h2 className="text-lg font-semibold mb-4">3. Preview & Apply</h2>

            <button
              onClick={handlePreview}
              disabled={!canPreview || previewLoading}
              className="px-6 py-2 bg-yellow-500 text-white rounded hover:bg-yellow-600 disabled:opacity-50"
            >
              {previewLoading ? "Loading Preview..." : "Preview Changes"}
            </button>

            {!canPreview && (
              <p className="text-sm text-gray-500 mt-2">
                {mode === "csv" && "Parse CSV mappings first"}
                {mode === "regex" && "Enter a regex pattern"}
                {mode === "template" && "Enter a template pattern"}
              </p>
            )}
          </div>

          {/* Preview Results */}
          {preview && (
            <div className="bg-white rounded-lg shadow p-4 mb-6">
              <h2 className="text-lg font-semibold mb-4">Preview Results</h2>

              {/* Summary */}
              <div className="grid grid-cols-3 gap-4 mb-4">
                <div className="text-center p-3 bg-blue-50 rounded-lg">
                  <div className="text-2xl font-bold text-blue-600">{preview.total_aps}</div>
                  <div className="text-sm text-gray-600">Total APs</div>
                </div>
                <div className="text-center p-3 bg-green-50 rounded-lg">
                  <div className="text-2xl font-bold text-green-600">{preview.rename_count}</div>
                  <div className="text-sm text-gray-600">To Rename</div>
                </div>
                <div className="text-center p-3 bg-gray-50 rounded-lg">
                  <div className="text-2xl font-bold text-gray-600">{preview.unchanged_count}</div>
                  <div className="text-sm text-gray-600">Unchanged</div>
                </div>
              </div>

              {/* Errors */}
              {preview.errors.length > 0 && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4">
                  <h3 className="font-medium text-red-700 mb-2">Errors:</h3>
                  <ul className="text-sm text-red-600 list-disc list-inside">
                    {preview.errors.map((e, i) => (
                      <li key={i}>{e}</li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Renames Table */}
              {preview.renames.length > 0 && (
                <div className="mb-4">
                  <h3 className="font-medium text-green-700 mb-2">
                    APs to Rename ({preview.renames.length}):
                  </h3>
                  <div className="max-h-64 overflow-y-auto border border-gray-200 rounded-lg">
                    <table className="w-full text-sm">
                      <thead className="bg-gray-50 sticky top-0">
                        <tr>
                          <th className="px-3 py-2 text-left">Serial</th>
                          <th className="px-3 py-2 text-left">Current Name</th>
                          <th className="px-3 py-2 text-center">→</th>
                          <th className="px-3 py-2 text-left">New Name</th>
                        </tr>
                      </thead>
                      <tbody>
                        {preview.renames.map((r, i) => (
                          <tr key={i} className="border-t">
                            <td className="px-3 py-2 font-mono text-xs">{r.serial_number}</td>
                            <td className="px-3 py-2 text-red-600">{r.current_name}</td>
                            <td className="px-3 py-2 text-center text-gray-400">→</td>
                            <td className="px-3 py-2 text-green-600 font-medium">{r.new_name}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Apply Button */}
              {preview.renames.length > 0 && (
                <button
                  onClick={handleApply}
                  disabled={applying}
                  className="px-6 py-2 bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50 font-medium"
                >
                  {applying ? "Starting..." : `Apply ${preview.renames.length} Renames`}
                </button>
              )}

              {preview.renames.length === 0 && (
                <p className="text-gray-500">No changes to apply.</p>
              )}
            </div>
          )}

          {/* Results */}
          {result && (
            <div className="bg-green-50 border border-green-200 rounded-lg shadow p-4 mb-6">
              <h2 className="text-lg font-semibold mb-4 text-green-800">Rename Complete</h2>
              <div className="grid grid-cols-3 gap-4">
                <div className="text-center">
                  <div className="text-2xl font-bold text-green-600">
                    {result.summary?.renamed || 0}
                  </div>
                  <div className="text-sm text-gray-600">Renamed</div>
                </div>
                <div className="text-center">
                  <div className="text-2xl font-bold text-red-600">
                    {result.summary?.failed || 0}
                  </div>
                  <div className="text-sm text-gray-600">Failed</div>
                </div>
                <div className="text-center">
                  <div className="text-2xl font-bold">
                    {result.summary?.total_requested || 0}
                  </div>
                  <div className="text-sm text-gray-600">Total</div>
                </div>
              </div>
            </div>
          )}
        </>
      )}

      {/* Job Monitor Modal */}
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

export default APRename;
