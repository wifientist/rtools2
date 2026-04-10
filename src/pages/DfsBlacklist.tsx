import { useState, useEffect, useCallback } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "@/utils/api";
import { useAuth } from "@/context/AuthContext";
import {
  Shield, Plus, Play, Trash2, RefreshCw,
  Pencil, Radio, X, Send,
} from "lucide-react";

const API = import.meta.env.VITE_API_BASE_URL || "/api";

// ── Types ───────────────────────────────────────────────────

interface ZoneItem {
  id: string;
  name: string;
}

interface ThresholdItem {
  count: number;
  backoff_hours: number;
}

interface Thresholds {
  hourly?: ThresholdItem;
  daily?: ThresholdItem;
  weekly?: ThresholdItem;
}

interface DfsConfig {
  id: number;
  controller_id: number;
  controller_name: string;
  zones: ZoneItem[];
  ap_groups: ZoneItem[];
  thresholds: Thresholds;
  event_filters: Record<string, unknown>[] | null;
  slack_configured: boolean;
  enabled: boolean;
  owner_id: number;
  created_at: string;
  updated_at: string;
}

interface DfsEventItem {
  id: number;
  sz_event_id: string | null;
  event_code: number | null;
  event_type: string | null;
  category: string | null;
  severity: string | null;
  activity: string | null;
  channel: number | null;
  ap_mac: string | null;
  ap_name: string | null;
  event_timestamp: string | null;
}

interface BlacklistEntry {
  id: number;
  channel: number;
  zone_id: string | null;
  zone_name: string | null;
  threshold_type: string;
  event_count: number;
  blacklisted_at: string;
  reentry_at: string;
  reentry_completed_at: string | null;
  status: string;
}

interface AuditEntry {
  id: number;
  action: string;
  details: Record<string, unknown>;
  created_at: string;
}

interface DashboardData {
  config: DfsConfig;
  active_blacklist: BlacklistEntry[];
  channel_stats: Record<number, Record<string, number>>;
  thresholds: Thresholds;
  recent_audit: AuditEntry[];
}

// ── Component ───────────────────────────────────────────────

const DfsBlacklist = () => {
  const auth = useAuth();
  const [configs, setConfigs] = useState<DfsConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Config detail view
  const [selectedConfigId, setSelectedConfigId] = useState<number | null>(null);
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [dashboardLoading, setDashboardLoading] = useState(false);

  // Tab state for detail view
  const [activeTab, setActiveTab] = useState<"dashboard" | "events" | "blacklist" | "audit">("dashboard");

  // Events list
  const [events, setEvents] = useState<DfsEventItem[]>([]);
  const [eventsTotal, setEventsTotal] = useState(0);

  // Blacklist entries
  const [blacklistEntries, setBlacklistEntries] = useState<BlacklistEntry[]>([]);
  const [blacklistTotal, setBlacklistTotal] = useState(0);

  // Audit logs
  const [auditLogs, setAuditLogs] = useState<AuditEntry[]>([]);
  const [auditTotal, setAuditTotal] = useState(0);

  // Create/Edit modal
  const [showModal, setShowModal] = useState(false);
  const [editingConfig, setEditingConfig] = useState<DfsConfig | null>(null);

  // Form state
  // SmartZone controllers from auth context
  const szControllers = (auth.controllers || []).filter(c => c.controller_type === "SmartZone");
  const defaultSzId = szControllers.find(c => c.id === auth.activeControllerId)?.id ?? szControllers[0]?.id ?? 0;

  const [formControllerId, setFormControllerId] = useState<number>(defaultSzId);
  const [formZones, setFormZones] = useState("");
  const [formApGroups, setFormApGroups] = useState("");
  const [formHourlyCount, setFormHourlyCount] = useState(3);
  const [formHourlyBackoff, setFormHourlyBackoff] = useState(6);
  const [formDailyCount, setFormDailyCount] = useState(8);
  const [formDailyBackoff, setFormDailyBackoff] = useState(48);
  const [formWeeklyCount, setFormWeeklyCount] = useState(15);
  const [formWeeklyBackoff, setFormWeeklyBackoff] = useState(168);
  const [formSlackUrl, setFormSlackUrl] = useState("");
  const [formEventFilters, setFormEventFilters] = useState("");
  const [formEnabled, setFormEnabled] = useState(true);

  const [triggering, setTriggering] = useState<number | null>(null);

  // Fetch configs
  const fetchConfigs = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiGet<DfsConfig[]>(`${API}/dfs-blacklist/configs`);
      setConfigs(data);
      setError("");
    } catch (err: any) {
      setError(err.message || "Failed to load configs");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchConfigs();
  }, [fetchConfigs]);

  // Fetch dashboard for selected config
  const fetchDashboard = useCallback(async (configId: number) => {
    setDashboardLoading(true);
    try {
      const data = await apiGet<DashboardData>(`${API}/dfs-blacklist/configs/${configId}/dashboard`);
      setDashboard(data);
    } catch (err: any) {
      setError(err.message || "Failed to load dashboard");
    } finally {
      setDashboardLoading(false);
    }
  }, []);

  const fetchEvents = async (configId: number) => {
    try {
      const data = await apiGet<{ total: number; events: DfsEventItem[] }>(`${API}/dfs-blacklist/configs/${configId}/events?limit=50`);
      setEvents(data.events);
      setEventsTotal(data.total);
    } catch {}
  };

  const fetchBlacklist = async (configId: number) => {
    try {
      const data = await apiGet<{ total: number; entries: BlacklistEntry[] }>(`${API}/dfs-blacklist/configs/${configId}/blacklist?limit=50`);
      setBlacklistEntries(data.entries);
      setBlacklistTotal(data.total);
    } catch {}
  };

  const fetchAudit = async (configId: number) => {
    try {
      const data = await apiGet<{ total: number; logs: AuditEntry[] }>(`${API}/dfs-blacklist/configs/${configId}/audit?limit=50`);
      setAuditLogs(data.logs);
      setAuditTotal(data.total);
    } catch {}
  };

  const selectConfig = (configId: number) => {
    setSelectedConfigId(configId);
    setActiveTab("dashboard");
    fetchDashboard(configId);
  };

  useEffect(() => {
    if (!selectedConfigId) return;
    if (activeTab === "events") fetchEvents(selectedConfigId);
    if (activeTab === "blacklist") fetchBlacklist(selectedConfigId);
    if (activeTab === "audit") fetchAudit(selectedConfigId);
  }, [activeTab, selectedConfigId]);

  // Create / Update config
  const handleSave = async () => {
    try {
      let zones: ZoneItem[] = [];
      let ap_groups: ZoneItem[] = [];
      try {
        zones = JSON.parse(formZones);
      } catch {
        setError("Invalid JSON for zones");
        return;
      }
      try {
        ap_groups = formApGroups ? JSON.parse(formApGroups) : [];
      } catch {
        setError("Invalid JSON for AP groups");
        return;
      }

      const payload = {
        controller_id: formControllerId,
        zones,
        ap_groups,
        thresholds: {
          hourly: { count: formHourlyCount, backoff_hours: formHourlyBackoff },
          daily: { count: formDailyCount, backoff_hours: formDailyBackoff },
          weekly: { count: formWeeklyCount, backoff_hours: formWeeklyBackoff },
        },
        event_filters: formEventFilters ? JSON.parse(formEventFilters) : null,
        slack_webhook_url: formSlackUrl || null,
        enabled: formEnabled,
      };

      if (editingConfig) {
        await apiPut(`${API}/dfs-blacklist/configs/${editingConfig.id}`, payload);
      } else {
        await apiPost(`${API}/dfs-blacklist/configs`, payload);
      }

      setShowModal(false);
      setEditingConfig(null);
      fetchConfigs();
    } catch (err: any) {
      setError(err.message || "Failed to save config");
    }
  };

  const handleDelete = async (configId: number) => {
    if (!confirm("Delete this DFS blacklist config and all related data?")) return;
    try {
      await apiDelete(`${API}/dfs-blacklist/configs/${configId}`);
      if (selectedConfigId === configId) {
        setSelectedConfigId(null);
        setDashboard(null);
      }
      fetchConfigs();
    } catch (err: any) {
      setError(err.message || "Failed to delete config");
    }
  };

  const handleTrigger = async (configId: number) => {
    setTriggering(configId);
    try {
      await apiPost(`${API}/dfs-blacklist/configs/${configId}/trigger`);
      if (selectedConfigId === configId) {
        fetchDashboard(configId);
      }
    } catch (err: any) {
      setError(err.message || "Failed to trigger job");
    } finally {
      setTriggering(null);
    }
  };

  const handleRemoveBlacklist = async (entryId: number) => {
    if (!selectedConfigId) return;
    try {
      await apiDelete(`${API}/dfs-blacklist/configs/${selectedConfigId}/blacklist/${entryId}`);
      fetchBlacklist(selectedConfigId);
      fetchDashboard(selectedConfigId);
    } catch (err: any) {
      setError(err.message || "Failed to remove blacklist entry");
    }
  };

  const openCreateModal = () => {
    setEditingConfig(null);
    setFormControllerId(defaultSzId);
    setFormZones("[]");
    setFormApGroups("[]");
    setFormHourlyCount(3);
    setFormHourlyBackoff(6);
    setFormDailyCount(8);
    setFormDailyBackoff(48);
    setFormWeeklyCount(15);
    setFormWeeklyBackoff(168);
    setFormSlackUrl("");
    setFormEventFilters("");
    setFormEnabled(true);
    setShowModal(true);
  };

  const openEditModal = (config: DfsConfig) => {
    setEditingConfig(config);
    setFormControllerId(config.controller_id);
    setFormZones(JSON.stringify(config.zones, null, 2));
    setFormApGroups(JSON.stringify(config.ap_groups, null, 2));
    setFormHourlyCount(config.thresholds.hourly?.count ?? 3);
    setFormHourlyBackoff(config.thresholds.hourly?.backoff_hours ?? 6);
    setFormDailyCount(config.thresholds.daily?.count ?? 8);
    setFormDailyBackoff(config.thresholds.daily?.backoff_hours ?? 48);
    setFormWeeklyCount(config.thresholds.weekly?.count ?? 15);
    setFormWeeklyBackoff(config.thresholds.weekly?.backoff_hours ?? 168);
    setFormSlackUrl("");  // Webhook URL is not returned by API for security; re-enter to change
    setFormEventFilters(config.event_filters ? JSON.stringify(config.event_filters, null, 2) : "");
    setFormEnabled(config.enabled);
    setShowModal(true);
  };

  const testSlack = async () => {
    if (!formSlackUrl) return;
    try {
      await apiPost(`${API}/dfs-blacklist/test-slack?webhook_url=${encodeURIComponent(formSlackUrl)}`);
      alert("Test message sent!");
    } catch (err: any) {
      alert("Slack test failed: " + (err.message || "Unknown error"));
    }
  };

  // ── Render ──────────────────────────────────────────────

  const statusBadge = (status: string) => {
    const colors: Record<string, string> = {
      active: "bg-red-900/50 text-red-300 border-red-700",
      expired: "bg-gray-800 text-gray-400 border-gray-600",
      manually_removed: "bg-yellow-900/50 text-yellow-300 border-yellow-700",
    };
    return (
      <span className={`px-2 py-0.5 text-xs rounded border ${colors[status] || "bg-gray-800 text-gray-400 border-gray-600"}`}>
        {status}
      </span>
    );
  };

  return (
    <div className="container mx-auto p-6 text-gray-100">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Shield className="text-purple-400" size={28} />
          <div>
            <h1 className="text-2xl font-bold">DFS Blacklist</h1>
            <p className="text-sm text-gray-400">Monitor DFS radar events and manage channel blacklisting on SmartZone</p>
          </div>
          <span className="ml-2 px-2 py-0.5 text-xs bg-purple-900/50 text-purple-300 border border-purple-700 rounded">ALPHA</span>
        </div>
        <button onClick={openCreateModal} className="flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-lg transition-colors">
          <Plus size={16} /> New Config
        </button>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-900/30 border border-red-700 text-red-300 rounded-lg flex justify-between items-center">
          <span>{error}</span>
          <button onClick={() => setError("")}><X size={16} /></button>
        </div>
      )}

      <div className="flex gap-6">
        {/* Left panel: Config list */}
        <div className="w-80 flex-shrink-0">
          <div className="bg-gray-800 rounded-lg border border-gray-700">
            <div className="p-3 border-b border-gray-700 flex items-center justify-between">
              <span className="text-sm font-medium text-gray-300">Configurations</span>
              <button onClick={fetchConfigs} className="text-gray-400 hover:text-white"><RefreshCw size={14} /></button>
            </div>
            {loading ? (
              <div className="p-4 text-center text-gray-500">Loading...</div>
            ) : configs.length === 0 ? (
              <div className="p-4 text-center text-gray-500">No configs yet</div>
            ) : (
              <div className="divide-y divide-gray-700">
                {configs.map(c => (
                  <div
                    key={c.id}
                    className={`p-3 cursor-pointer hover:bg-gray-700/50 transition-colors ${selectedConfigId === c.id ? "bg-gray-700/80 border-l-2 border-purple-500" : ""}`}
                    onClick={() => selectConfig(c.id)}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium truncate">{c.controller_name}</span>
                      <span className={`w-2 h-2 rounded-full ${c.enabled ? "bg-green-500" : "bg-gray-500"}`} />
                    </div>
                    <div className="text-xs text-gray-400 mt-1">
                      {c.zones.length} zone{c.zones.length !== 1 ? "s" : ""} &middot; {c.ap_groups.length} AP group{c.ap_groups.length !== 1 ? "s" : ""}
                    </div>
                    <div className="flex gap-1 mt-2">
                      <button onClick={(e) => { e.stopPropagation(); handleTrigger(c.id); }} className="p-1 text-gray-400 hover:text-green-400" title="Run now">
                        {triggering === c.id ? <RefreshCw size={14} className="animate-spin" /> : <Play size={14} />}
                      </button>
                      <button onClick={(e) => { e.stopPropagation(); openEditModal(c); }} className="p-1 text-gray-400 hover:text-blue-400" title="Edit">
                        <Pencil size={14} />
                      </button>
                      <button onClick={(e) => { e.stopPropagation(); handleDelete(c.id); }} className="p-1 text-gray-400 hover:text-red-400" title="Delete">
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right panel: Detail view */}
        <div className="flex-1 min-w-0">
          {!selectedConfigId ? (
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-12 text-center text-gray-500">
              <Radio size={48} className="mx-auto mb-4 opacity-30" />
              <p>Select a configuration to view details</p>
            </div>
          ) : dashboardLoading ? (
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-12 text-center text-gray-500">Loading...</div>
          ) : (
            <div className="space-y-4">
              {/* Tabs */}
              <div className="flex gap-1 bg-gray-800 rounded-lg border border-gray-700 p-1">
                {(["dashboard", "events", "blacklist", "audit"] as const).map(tab => (
                  <button
                    key={tab}
                    onClick={() => setActiveTab(tab)}
                    className={`px-4 py-2 text-sm rounded-md transition-colors capitalize ${activeTab === tab ? "bg-gray-700 text-white" : "text-gray-400 hover:text-gray-200"}`}
                  >
                    {tab}
                  </button>
                ))}
              </div>

              {/* Dashboard Tab */}
              {activeTab === "dashboard" && dashboard && (
                <div className="space-y-4">
                  {/* Active Blacklist */}
                  <div className="bg-gray-800 rounded-lg border border-gray-700">
                    <div className="p-3 border-b border-gray-700">
                      <span className="text-sm font-medium text-gray-300">Active Blacklist ({dashboard.active_blacklist.length})</span>
                    </div>
                    {dashboard.active_blacklist.length === 0 ? (
                      <div className="p-4 text-center text-gray-500 text-sm">No channels currently blacklisted</div>
                    ) : (
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="text-left text-gray-400 border-b border-gray-700">
                            <th className="px-3 py-2">Channel</th>
                            <th className="px-3 py-2">Threshold</th>
                            <th className="px-3 py-2">Events</th>
                            <th className="px-3 py-2">Blacklisted</th>
                            <th className="px-3 py-2">Re-entry</th>
                            <th className="px-3 py-2"></th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-700">
                          {dashboard.active_blacklist.map(e => (
                            <tr key={e.id} className="hover:bg-gray-700/30">
                              <td className="px-3 py-2 font-mono text-red-300">{e.channel}</td>
                              <td className="px-3 py-2">{e.threshold_type}</td>
                              <td className="px-3 py-2">{e.event_count}</td>
                              <td className="px-3 py-2 text-xs text-gray-400">{new Date(e.blacklisted_at).toLocaleString()}</td>
                              <td className="px-3 py-2 text-xs text-gray-400">{new Date(e.reentry_at).toLocaleString()}</td>
                              <td className="px-3 py-2">
                                <button onClick={() => handleRemoveBlacklist(e.id)} className="text-xs text-yellow-400 hover:text-yellow-300">Remove</button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>

                  {/* Channel Stats */}
                  <div className="bg-gray-800 rounded-lg border border-gray-700">
                    <div className="p-3 border-b border-gray-700">
                      <span className="text-sm font-medium text-gray-300">Channel Event Counts</span>
                    </div>
                    {Object.keys(dashboard.channel_stats).length === 0 ? (
                      <div className="p-4 text-center text-gray-500 text-sm">No DFS events recorded yet</div>
                    ) : (
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="text-left text-gray-400 border-b border-gray-700">
                            <th className="px-3 py-2">Channel</th>
                            <th className="px-3 py-2">Last Hour</th>
                            <th className="px-3 py-2">Last 24h</th>
                            <th className="px-3 py-2">Last 7d</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-700">
                          {Object.entries(dashboard.channel_stats)
                            .sort(([a], [b]) => Number(a) - Number(b))
                            .map(([ch, stats]) => {
                              const t = dashboard.thresholds;
                              const hourlyOver = t.hourly && (stats.hourly ?? 0) >= t.hourly.count;
                              const dailyOver = t.daily && (stats.daily ?? 0) >= t.daily.count;
                              const weeklyOver = t.weekly && (stats.weekly ?? 0) >= t.weekly.count;
                              return (
                                <tr key={ch} className="hover:bg-gray-700/30">
                                  <td className="px-3 py-2 font-mono">{ch}</td>
                                  <td className={`px-3 py-2 ${hourlyOver ? "text-red-400 font-bold" : ""}`}>
                                    {stats.hourly ?? 0}{t.hourly ? ` / ${t.hourly.count}` : ""}
                                  </td>
                                  <td className={`px-3 py-2 ${dailyOver ? "text-red-400 font-bold" : ""}`}>
                                    {stats.daily ?? 0}{t.daily ? ` / ${t.daily.count}` : ""}
                                  </td>
                                  <td className={`px-3 py-2 ${weeklyOver ? "text-red-400 font-bold" : ""}`}>
                                    {stats.weekly ?? 0}{t.weekly ? ` / ${t.weekly.count}` : ""}
                                  </td>
                                </tr>
                              );
                            })}
                        </tbody>
                      </table>
                    )}
                  </div>

                  {/* Recent Audit */}
                  <div className="bg-gray-800 rounded-lg border border-gray-700">
                    <div className="p-3 border-b border-gray-700">
                      <span className="text-sm font-medium text-gray-300">Recent Activity</span>
                    </div>
                    <div className="divide-y divide-gray-700">
                      {dashboard.recent_audit.map(a => (
                        <div key={a.id} className="px-3 py-2 flex items-center justify-between text-sm">
                          <span className="text-gray-300">{a.action.replace(/_/g, " ")}</span>
                          <span className="text-xs text-gray-500">{new Date(a.created_at).toLocaleString()}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* Events Tab */}
              {activeTab === "events" && (
                <div className="bg-gray-800 rounded-lg border border-gray-700">
                  <div className="p-3 border-b border-gray-700">
                    <span className="text-sm font-medium text-gray-300">DFS Events ({eventsTotal})</span>
                  </div>
                  {events.length === 0 ? (
                    <div className="p-4 text-center text-gray-500 text-sm">No events found</div>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="text-left text-gray-400 border-b border-gray-700">
                            <th className="px-3 py-2">Time</th>
                            <th className="px-3 py-2">Code</th>
                            <th className="px-3 py-2">Channel</th>
                            <th className="px-3 py-2">AP</th>
                            <th className="px-3 py-2">Severity</th>
                            <th className="px-3 py-2">Activity</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-700">
                          {events.map(e => (
                            <tr key={e.id} className="hover:bg-gray-700/30">
                              <td className="px-3 py-2 text-xs text-gray-400 whitespace-nowrap">{e.event_timestamp ? new Date(e.event_timestamp).toLocaleString() : "—"}</td>
                              <td className="px-3 py-2 font-mono">{e.event_code ?? "—"}</td>
                              <td className="px-3 py-2 font-mono text-yellow-300">{e.channel ?? "—"}</td>
                              <td className="px-3 py-2 text-xs">{e.ap_name || e.ap_mac || "—"}</td>
                              <td className="px-3 py-2">
                                <span className={`text-xs ${e.severity === "Critical" ? "text-red-400" : e.severity === "Major" ? "text-orange-400" : "text-gray-400"}`}>
                                  {e.severity || "—"}
                                </span>
                              </td>
                              <td className="px-3 py-2 text-xs text-gray-400 max-w-md truncate">{e.activity || "—"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}

              {/* Blacklist Tab */}
              {activeTab === "blacklist" && (
                <div className="bg-gray-800 rounded-lg border border-gray-700">
                  <div className="p-3 border-b border-gray-700">
                    <span className="text-sm font-medium text-gray-300">Blacklist History ({blacklistTotal})</span>
                  </div>
                  {blacklistEntries.length === 0 ? (
                    <div className="p-4 text-center text-gray-500 text-sm">No blacklist entries</div>
                  ) : (
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-left text-gray-400 border-b border-gray-700">
                          <th className="px-3 py-2">Channel</th>
                          <th className="px-3 py-2">Threshold</th>
                          <th className="px-3 py-2">Events</th>
                          <th className="px-3 py-2">Blacklisted</th>
                          <th className="px-3 py-2">Re-entry</th>
                          <th className="px-3 py-2">Status</th>
                          <th className="px-3 py-2"></th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-700">
                        {blacklistEntries.map(e => (
                          <tr key={e.id} className="hover:bg-gray-700/30">
                            <td className="px-3 py-2 font-mono">{e.channel}</td>
                            <td className="px-3 py-2">{e.threshold_type}</td>
                            <td className="px-3 py-2">{e.event_count}</td>
                            <td className="px-3 py-2 text-xs text-gray-400">{new Date(e.blacklisted_at).toLocaleString()}</td>
                            <td className="px-3 py-2 text-xs text-gray-400">{new Date(e.reentry_at).toLocaleString()}</td>
                            <td className="px-3 py-2">{statusBadge(e.status)}</td>
                            <td className="px-3 py-2">
                              {e.status === "active" && (
                                <button onClick={() => handleRemoveBlacklist(e.id)} className="text-xs text-yellow-400 hover:text-yellow-300">Remove</button>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              )}

              {/* Audit Tab */}
              {activeTab === "audit" && (
                <div className="bg-gray-800 rounded-lg border border-gray-700">
                  <div className="p-3 border-b border-gray-700">
                    <span className="text-sm font-medium text-gray-300">Audit Trail ({auditTotal})</span>
                  </div>
                  {auditLogs.length === 0 ? (
                    <div className="p-4 text-center text-gray-500 text-sm">No audit entries</div>
                  ) : (
                    <div className="divide-y divide-gray-700">
                      {auditLogs.map(a => (
                        <div key={a.id} className="px-3 py-2">
                          <div className="flex items-center justify-between">
                            <span className="text-sm text-gray-300 font-medium">{a.action.replace(/_/g, " ")}</span>
                            <span className="text-xs text-gray-500">{new Date(a.created_at).toLocaleString()}</span>
                          </div>
                          {a.details && (
                            <pre className="mt-1 text-xs text-gray-500 overflow-x-auto">{JSON.stringify(a.details, null, 2)}</pre>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Create/Edit Modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="bg-gray-800 border border-gray-700 rounded-lg w-full max-w-2xl max-h-[90vh] overflow-y-auto p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-bold">{editingConfig ? "Edit Config" : "New DFS Blacklist Config"}</h2>
              <button onClick={() => setShowModal(false)} className="text-gray-400 hover:text-white"><X size={20} /></button>
            </div>

            <div className="space-y-4">
              {/* Controller */}
              {!editingConfig && (
                <div>
                  <label className="block text-sm text-gray-400 mb-1">SmartZone Controller</label>
                  {szControllers.length === 0 ? (
                    <p className="text-sm text-yellow-400">No SmartZone controllers found. Add one in Controllers first.</p>
                  ) : (
                    <select
                      value={formControllerId}
                      onChange={e => setFormControllerId(Number(e.target.value))}
                      className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm"
                    >
                      {szControllers.map(c => (
                        <option key={c.id} value={c.id}>{c.name} (ID: {c.id})</option>
                      ))}
                    </select>
                  )}
                </div>
              )}

              {/* Zones */}
              <div>
                <label className="block text-sm text-gray-400 mb-1">Zones (JSON array of {`{id, name}`})</label>
                <textarea
                  value={formZones}
                  onChange={e => setFormZones(e.target.value)}
                  rows={3}
                  className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm font-mono"
                />
              </div>

              {/* AP Groups */}
              <div>
                <label className="block text-sm text-gray-400 mb-1">AP Groups (JSON array, optional)</label>
                <textarea
                  value={formApGroups}
                  onChange={e => setFormApGroups(e.target.value)}
                  rows={2}
                  className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm font-mono"
                />
              </div>

              {/* Thresholds */}
              <div>
                <label className="block text-sm text-gray-400 mb-2">Thresholds</label>
                <div className="grid grid-cols-3 gap-3">
                  {[
                    { label: "Hourly", count: formHourlyCount, setCount: setFormHourlyCount, backoff: formHourlyBackoff, setBackoff: setFormHourlyBackoff },
                    { label: "Daily", count: formDailyCount, setCount: setFormDailyCount, backoff: formDailyBackoff, setBackoff: setFormDailyBackoff },
                    { label: "Weekly", count: formWeeklyCount, setCount: setFormWeeklyCount, backoff: formWeeklyBackoff, setBackoff: setFormWeeklyBackoff },
                  ].map(t => (
                    <div key={t.label} className="bg-gray-900 border border-gray-600 rounded p-3">
                      <div className="text-xs text-gray-400 mb-2 font-medium">{t.label}</div>
                      <div className="space-y-2">
                        <div>
                          <label className="text-xs text-gray-500">Events</label>
                          <input type="number" value={t.count} onChange={e => t.setCount(Number(e.target.value))} className="w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm" />
                        </div>
                        <div>
                          <label className="text-xs text-gray-500">Backoff (hours)</label>
                          <input type="number" value={t.backoff} onChange={e => t.setBackoff(Number(e.target.value))} className="w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm" />
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Event Filters */}
              <div>
                <label className="block text-sm text-gray-400 mb-1">Event Filters (JSON, optional — passed to SZ extraFilters)</label>
                <textarea
                  value={formEventFilters}
                  onChange={e => setFormEventFilters(e.target.value)}
                  rows={3}
                  className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm font-mono"
                  placeholder='[{"type": "SEVERITY", "value": "Major"}]'
                />
              </div>

              {/* Slack Webhook */}
              <div>
                <label className="block text-sm text-gray-400 mb-1">Slack Webhook URL</label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={formSlackUrl}
                    onChange={e => setFormSlackUrl(e.target.value)}
                    className="flex-1 bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm"
                    placeholder="https://hooks.slack.com/services/..."
                  />
                  <button onClick={testSlack} className="px-3 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm flex items-center gap-1">
                    <Send size={14} /> Test
                  </button>
                </div>
              </div>

              {/* Enabled */}
              <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
                <input
                  type="checkbox"
                  checked={formEnabled}
                  onChange={e => setFormEnabled(e.target.checked)}
                  className="rounded"
                />
                Enabled
              </label>
            </div>

            {/* Actions */}
            <div className="flex justify-end gap-3 mt-6 pt-4 border-t border-gray-700">
              <button onClick={() => setShowModal(false)} className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded-lg text-sm">
                Cancel
              </button>
              <button onClick={handleSave} className="px-4 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-lg text-sm">
                {editingConfig ? "Update" : "Create"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default DfsBlacklist;
