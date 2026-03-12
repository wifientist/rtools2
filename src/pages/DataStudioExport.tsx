import { useState, useEffect } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "@/utils/api";
import { BarChart3, Plus, Play, Trash2, RefreshCw, CheckCircle, XCircle, Clock, ChevronDown, ChevronRight, Eye, Mail } from "lucide-react";

const API = import.meta.env.VITE_API_BASE_URL || "/api";

// ── Types ───────────────────────────────────────────────────

interface TenantConfig {
  tenant_id: string;
  tenant_name: string;
}

interface ExportConfig {
  id: number;
  company_id: number;
  company_name: string;
  report_name: string;
  tenant_configs: TenantConfig[];
  interval_minutes: number;
  retention_count: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

interface CompanyOption {
  id: number;
  name: string;
}

interface ExportRun {
  id: number;
  config_id: number;
  tenant_id: string;
  tenant_name: string;
  status: string;
  error_message: string | null;
  screenshot_s3_key: string | null;
  s3_key: string | null;
  shared_file_id: number | null;
  file_size_bytes: number | null;
  filename: string | null;
  started_at: string;
  completed_at: string | null;
  duration_seconds: number | null;
}

// ── Component ───────────────────────────────────────────────

const DataStudioExport = () => {
  const [configs, setConfigs] = useState<ExportConfig[]>([]);
  const [companies, setCompanies] = useState<CompanyOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Create/Edit modal state
  const [showModal, setShowModal] = useState(false);
  const [editingConfig, setEditingConfig] = useState<ExportConfig | null>(null);
  const [formData, setFormData] = useState({
    company_id: 0,
    web_username: "",
    web_password: "",
    report_name: "",
    tenant_configs: [] as TenantConfig[],
    interval_minutes: 60,
    retention_count: 24,
  });
  const [saving, setSaving] = useState(false);
  const [testingLogin, setTestingLogin] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; error?: string } | null>(null);

  // Tenant entry
  const [newTenantId, setNewTenantId] = useState("");
  const [newTenantName, setNewTenantName] = useState("");

  // Run history
  const [expandedConfig, setExpandedConfig] = useState<number | null>(null);
  const [runs, setRuns] = useState<ExportRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);

  // Email modal
  const [emailConfig, setEmailConfig] = useState<ExportConfig | null>(null);
  const [emailRecipients, setEmailRecipients] = useState("");
  const [emailSending, setEmailSending] = useState(false);

  // ── Data Fetching ─────────────────────────────────────────

  const fetchConfigs = async () => {
    setLoading(true);
    try {
      const [configData, companyData] = await Promise.all([
        apiGet<ExportConfig[]>(`${API}/data-studio-export/configs`),
        apiGet<CompanyOption[]>(`${API}/admin/companies`),
      ]);
      setConfigs(configData);
      setCompanies(companyData);
      setError("");
    } catch (err: any) {
      setError(err.message || "Failed to load configs");
    } finally {
      setLoading(false);
    }
  };

  const fetchRuns = async (configId: number) => {
    setRunsLoading(true);
    try {
      const data = await apiGet<{ total: number; runs: ExportRun[] }>(`${API}/data-studio-export/configs/${configId}/runs?limit=50`);
      setRuns(data.runs);
    } catch (err: any) {
      setError(err.message || "Failed to load run history");
    } finally {
      setRunsLoading(false);
    }
  };

  useEffect(() => { fetchConfigs(); }, []);

  // ── Handlers ──────────────────────────────────────────────

  const openCreateModal = () => {
    setEditingConfig(null);
    setFormData({
      company_id: companies.length > 0 ? companies[0].id : 0,
      web_username: "",
      web_password: "",
      report_name: "",
      tenant_configs: [],
      interval_minutes: 60,
      retention_count: 24,
    });
    setTestResult(null);
    setShowModal(true);
  };

  const openEditModal = (config: ExportConfig) => {
    setEditingConfig(config);
    setFormData({
      company_id: config.company_id,
      web_username: "",
      web_password: "",
      report_name: config.report_name,
      tenant_configs: config.tenant_configs,
      interval_minutes: config.interval_minutes,
      retention_count: config.retention_count,
    });
    setTestResult(null);
    setShowModal(true);
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      if (editingConfig) {
        const updateData: any = {
          report_name: formData.report_name,
          tenant_configs: formData.tenant_configs,
          interval_minutes: formData.interval_minutes,
          retention_count: formData.retention_count,
        };
        if (formData.web_username) updateData.web_username = formData.web_username;
        if (formData.web_password) updateData.web_password = formData.web_password;
        await apiPut(`${API}/data-studio-export/configs/${editingConfig.id}`, updateData);
      } else {
        await apiPost(`${API}/data-studio-export/configs`, formData);
      }
      setShowModal(false);
      await fetchConfigs();
    } catch (err: any) {
      setError(err.message || "Failed to save config");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (config: ExportConfig) => {
    if (!confirm(`Delete export config for "${config.report_name}"? This will also delete all run history.`)) return;
    try {
      await apiDelete(`${API}/data-studio-export/configs/${config.id}`);
      await fetchConfigs();
    } catch (err: any) {
      setError(err.message || "Failed to delete config");
    }
  };

  const handleTrigger = async (config: ExportConfig) => {
    try {
      await apiPost(`${API}/data-studio-export/configs/${config.id}/trigger`, {});
      setError("");
      alert("Export triggered. Check run history for results.");
    } catch (err: any) {
      setError(err.message || "Failed to trigger export");
    }
  };

  const handleToggleEnabled = async (config: ExportConfig) => {
    try {
      await apiPut(`${API}/data-studio-export/configs/${config.id}`, { enabled: !config.enabled });
      await fetchConfigs();
    } catch (err: any) {
      setError(err.message || "Failed to toggle config");
    }
  };

  const handleTestLogin = async () => {
    if (!formData.web_username || !formData.web_password) {
      setTestResult({ success: false, error: "Enter username and password first" });
      return;
    }
    setTestingLogin(true);
    setTestResult(null);
    try {
      const result = await apiPost<{ success: boolean; error?: string }>(`${API}/data-studio-export/test-login`, {
        web_username: formData.web_username,
        web_password: formData.web_password,
      });
      setTestResult(result);
    } catch (err: any) {
      setTestResult({ success: false, error: err.message || "Test failed" });
    } finally {
      setTestingLogin(false);
    }
  };

  const addTenant = () => {
    if (!newTenantId.trim()) return;
    setFormData(prev => ({
      ...prev,
      tenant_configs: [...prev.tenant_configs, { tenant_id: newTenantId.trim(), tenant_name: newTenantName.trim() || newTenantId.trim() }],
    }));
    setNewTenantId("");
    setNewTenantName("");
  };

  const removeTenant = (index: number) => {
    setFormData(prev => ({
      ...prev,
      tenant_configs: prev.tenant_configs.filter((_, i) => i !== index),
    }));
  };

  const toggleExpand = (configId: number) => {
    if (expandedConfig === configId) {
      setExpandedConfig(null);
      setRuns([]);
    } else {
      setExpandedConfig(configId);
      fetchRuns(configId);
    }
  };

  const handleEmail = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!emailConfig) return;
    const recipients = emailRecipients.split(",").map(r => r.trim()).filter(Boolean);
    if (recipients.length === 0) return;

    setEmailSending(true);
    try {
      const result = await apiPost<{ status: string; attachments: number }>(
        `${API}/data-studio-export/${emailConfig.id}/email`,
        { recipients }
      );
      alert(`Email sent with ${result.attachments} CSV${result.attachments !== 1 ? "s" : ""} attached.`);
      setEmailConfig(null);
      setEmailRecipients("");
    } catch (err: any) {
      setError(err.message || "Failed to send email");
    } finally {
      setEmailSending(false);
    }
  };

  const formatBytes = (bytes: number | null) => {
    if (!bytes) return "—";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1048576).toFixed(1)} MB`;
  };

  const formatDate = (iso: string | null) => {
    if (!iso) return "—";
    // Backend stores UTC but may omit the Z suffix — ensure we parse as UTC
    const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
    return d.toLocaleString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: "UTC",
    }) + " UTC";
  };

  // ── Render ────────────────────────────────────────────────

  return (
    <div className="max-w-6xl mx-auto p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center space-x-3">
          <BarChart3 size={28} className="text-indigo-600" />
          <h1 className="text-2xl font-bold text-gray-800">Data Studio Export</h1>
        </div>
        <div className="flex space-x-2">
          <button onClick={fetchConfigs} className="px-3 py-2 text-gray-600 hover:bg-gray-100 rounded-lg" title="Refresh">
            <RefreshCw size={18} />
          </button>
          <button onClick={openCreateModal} className="flex items-center space-x-1 px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700">
            <Plus size={18} />
            <span>New Config</span>
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded-lg flex justify-between items-center">
          <span>{error}</span>
          <button onClick={() => setError("")} className="text-red-500 hover:text-red-700">&times;</button>
        </div>
      )}

      {/* Config List */}
      {loading ? (
        <div className="text-center py-12 text-gray-500">Loading...</div>
      ) : configs.length === 0 ? (
        <div className="text-center py-12 text-gray-500">
          <BarChart3 size={48} className="mx-auto mb-3 text-gray-300" />
          <p>No export configurations yet.</p>
          <p className="text-sm mt-1">Create one to start exporting Data Studio reports automatically.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {configs.map(config => (
            <div key={config.id} className="bg-white border border-gray-200 rounded-lg shadow-sm">
              {/* Config Row */}
              <div className="flex items-center justify-between p-4">
                <div className="flex items-center space-x-4 flex-1 min-w-0">
                  <button onClick={() => toggleExpand(config.id)} className="text-gray-400 hover:text-gray-600">
                    {expandedConfig === config.id ? <ChevronDown size={20} /> : <ChevronRight size={20} />}
                  </button>
                  <div className="min-w-0">
                    <div className="font-medium text-gray-800 truncate">{config.report_name}</div>
                    <div className="text-sm text-gray-500">
                      {config.company_name} &middot; {config.tenant_configs.length} tenant{config.tenant_configs.length !== 1 ? "s" : ""} &middot; every {config.interval_minutes}min &middot; keep {config.retention_count}
                    </div>
                  </div>
                </div>
                <div className="flex items-center space-x-2">
                  {/* Enabled toggle */}
                  <button
                    onClick={() => handleToggleEnabled(config)}
                    className={`px-3 py-1 text-xs font-medium rounded-full ${config.enabled ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"}`}
                  >
                    {config.enabled ? "Enabled" : "Disabled"}
                  </button>
                  <button onClick={() => handleTrigger(config)} className="p-2 text-indigo-600 hover:bg-indigo-50 rounded" title="Run now" disabled={!config.enabled}>
                    <Play size={16} />
                  </button>
                  <button onClick={() => openEditModal(config)} className="p-2 text-gray-500 hover:bg-gray-100 rounded" title="Edit">
                    <Eye size={16} />
                  </button>
                  <button onClick={() => { setEmailConfig(config); setEmailRecipients(""); }} className="p-2 text-blue-500 hover:bg-blue-50 rounded" title="Email latest exports">
                    <Mail size={16} />
                  </button>
                  <button onClick={() => handleDelete(config)} className="p-2 text-red-500 hover:bg-red-50 rounded" title="Delete">
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>

              {/* Run History (expanded) */}
              {expandedConfig === config.id && (
                <div className="border-t border-gray-100 p-4 bg-gray-50">
                  <h3 className="text-sm font-semibold text-gray-700 mb-3">Recent Export Runs</h3>
                  {runsLoading ? (
                    <div className="text-sm text-gray-500 py-4 text-center">Loading history...</div>
                  ) : runs.length === 0 ? (
                    <div className="text-sm text-gray-500 py-4 text-center">No export runs yet.</div>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="text-left text-gray-500 border-b border-gray-200">
                            <th className="pb-2 pr-4">Tenant</th>
                            <th className="pb-2 pr-4">Status</th>
                            <th className="pb-2 pr-4">Time</th>
                            <th className="pb-2 pr-4">Duration</th>
                            <th className="pb-2 pr-4">Size</th>
                            <th className="pb-2">File</th>
                          </tr>
                        </thead>
                        <tbody>
                          {runs.map(run => (
                            <tr key={run.id} className="border-b border-gray-100">
                              <td className="py-2 pr-4 text-gray-700">{run.tenant_name || run.tenant_id}</td>
                              <td className="py-2 pr-4">
                                {run.status === "success" ? (
                                  <span className="flex items-center space-x-1 text-green-600"><CheckCircle size={14} /><span>Success</span></span>
                                ) : run.status === "failed" ? (
                                  <span className="flex items-center space-x-1 text-red-600" title={run.error_message || ""}>
                                    <XCircle size={14} /><span>Failed</span>
                                  </span>
                                ) : (
                                  <span className="flex items-center space-x-1 text-gray-500"><Clock size={14} /><span>{run.status}</span></span>
                                )}
                              </td>
                              <td className="py-2 pr-4 text-gray-500">{formatDate(run.started_at)}</td>
                              <td className="py-2 pr-4 text-gray-500">{run.duration_seconds ? `${run.duration_seconds.toFixed(1)}s` : "—"}</td>
                              <td className="py-2 pr-4 text-gray-500">{formatBytes(run.file_size_bytes)}</td>
                              <td className="py-2">
                                {run.filename ? (
                                  <span className="text-indigo-600 text-xs">{run.filename}</span>
                                ) : run.error_message ? (
                                  <span className="text-red-500 text-xs truncate max-w-[200px] inline-block" title={run.error_message}>
                                    {run.error_message.slice(0, 60)}
                                  </span>
                                ) : "—"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Create/Edit Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-lg w-full max-h-[90vh] overflow-y-auto">
            <form onSubmit={handleSave}>
              <div className="p-6 space-y-4">
                <h2 className="text-lg font-bold text-gray-800">
                  {editingConfig ? "Edit Export Config" : "New Export Config"}
                </h2>

                {/* Company */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Company</label>
                  <select
                    value={formData.company_id}
                    onChange={e => setFormData(prev => ({ ...prev, company_id: parseInt(e.target.value) }))}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                    required
                    disabled={!!editingConfig}
                  >
                    <option value={0}>Select company...</option>
                    {companies.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                  </select>
                </div>

                {/* Credentials */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Web Username {editingConfig && <span className="text-gray-400">(leave blank to keep)</span>}
                    </label>
                    <input
                      type="text"
                      value={formData.web_username}
                      onChange={e => setFormData(prev => ({ ...prev, web_username: e.target.value }))}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                      placeholder="user@example.com"
                      required={!editingConfig}
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Web Password {editingConfig && <span className="text-gray-400">(leave blank to keep)</span>}
                    </label>
                    <input
                      type="password"
                      value={formData.web_password}
                      onChange={e => setFormData(prev => ({ ...prev, web_password: e.target.value }))}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                      required={!editingConfig}
                    />
                  </div>
                </div>

                {/* Test Login */}
                <div className="flex items-center space-x-3">
                  <button type="button" onClick={handleTestLogin} disabled={testingLogin}
                    className="px-3 py-1 text-sm bg-gray-100 text-gray-700 rounded hover:bg-gray-200 disabled:opacity-50">
                    {testingLogin ? "Testing..." : "Test Login"}
                  </button>
                  {testResult && (
                    <span className={`text-sm ${testResult.success ? "text-green-600" : "text-red-600"}`}>
                      {testResult.success ? "Login successful" : testResult.error}
                    </span>
                  )}
                </div>

                {/* Report Name */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Report Name</label>
                  <input
                    type="text"
                    value={formData.report_name}
                    onChange={e => setFormData(prev => ({ ...prev, report_name: e.target.value }))}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                    placeholder="AP Performance Report"
                    required
                  />
                </div>

                {/* Tenants */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Tenants</label>
                  <div className="space-y-2 mb-2">
                    {formData.tenant_configs.map((tc, i) => (
                      <div key={i} className="flex items-center justify-between bg-gray-50 rounded px-3 py-1.5 text-sm">
                        <span><strong>{tc.tenant_name}</strong> <span className="text-gray-400">({tc.tenant_id})</span></span>
                        <button type="button" onClick={() => removeTenant(i)} className="text-red-400 hover:text-red-600">&times;</button>
                      </div>
                    ))}
                  </div>
                  <div className="flex space-x-2">
                    <input type="text" value={newTenantId} onChange={e => setNewTenantId(e.target.value)}
                      placeholder="Tenant ID" className="flex-1 border border-gray-300 rounded px-2 py-1 text-sm" />
                    <input type="text" value={newTenantName} onChange={e => setNewTenantName(e.target.value)}
                      placeholder="Tenant Name" className="flex-1 border border-gray-300 rounded px-2 py-1 text-sm" />
                    <button type="button" onClick={addTenant} className="px-3 py-1 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300">
                      Add
                    </button>
                  </div>
                </div>

                {/* Schedule & Retention */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Interval</label>
                    <select
                      value={formData.interval_minutes}
                      onChange={e => setFormData(prev => ({ ...prev, interval_minutes: parseInt(e.target.value) }))}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                    >
                      <option value={30}>Every 30 minutes</option>
                      <option value={60}>Every hour</option>
                      <option value={120}>Every 2 hours</option>
                      <option value={240}>Every 4 hours</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Keep last N exports</label>
                    <input
                      type="number"
                      value={formData.retention_count}
                      onChange={e => setFormData(prev => ({ ...prev, retention_count: parseInt(e.target.value) || 24 }))}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                      min={1}
                      max={168}
                    />
                  </div>
                </div>
              </div>

              {/* Modal Footer */}
              <div className="flex justify-end space-x-3 p-4 border-t border-gray-200 bg-gray-50 rounded-b-xl">
                <button type="button" onClick={() => setShowModal(false)} className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg text-sm">
                  Cancel
                </button>
                <button type="submit" disabled={saving} className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 text-sm disabled:opacity-50">
                  {saving ? "Saving..." : editingConfig ? "Update" : "Create"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Email Modal */}
      {emailConfig && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-md w-full">
            <form onSubmit={handleEmail}>
              <div className="p-6 space-y-4">
                <h2 className="text-lg font-bold text-gray-800">Email Latest Exports</h2>
                <p className="text-sm text-gray-500">
                  Send the latest CSV export per tenant for <strong>{emailConfig.report_name}</strong> ({emailConfig.company_name}).
                </p>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Recipients</label>
                  <input
                    type="text"
                    value={emailRecipients}
                    onChange={e => setEmailRecipients(e.target.value)}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                    placeholder="user@example.com, other@example.com"
                    required
                  />
                  <p className="text-xs text-gray-400 mt-1">Comma-separated email addresses</p>
                </div>
              </div>
              <div className="flex justify-end space-x-3 p-4 border-t border-gray-200 bg-gray-50 rounded-b-xl">
                <button type="button" onClick={() => setEmailConfig(null)} className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg text-sm">
                  Cancel
                </button>
                <button type="submit" disabled={emailSending} className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm disabled:opacity-50">
                  {emailSending ? "Sending..." : "Send Email"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default DataStudioExport;
