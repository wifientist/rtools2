import { useState, useEffect } from "react";
import { Clock, Play, Pause, RefreshCw, CheckCircle, XCircle, AlertCircle } from "lucide-react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

interface ScheduledJob {
  id: string;
  name: string;
  description?: string;
  owner_type?: string;
  owner_id?: string;
  trigger_type: string;
  trigger_config: Record<string, number>;
  enabled: boolean;
  paused: boolean;
  created_at?: string;
  last_run_at?: string;
  next_run_at?: string;
}

interface SchedulerOverview {
  total_jobs: number;
  active_jobs: number;
  paused_jobs: number;
  disabled_jobs: number;
  jobs: ScheduledJob[];
}

function formatTrigger(type: string, config: Record<string, number>): string {
  if (type === "interval") {
    if (config.minutes) return `Every ${config.minutes} min`;
    if (config.hours) return `Every ${config.hours} hr`;
    if (config.seconds) return `Every ${config.seconds} sec`;
  }
  if (type === "cron") {
    return `Cron: ${JSON.stringify(config)}`;
  }
  return type;
}

function formatRelativeTime(dateStr?: string, isPast: boolean = false): string {
  if (!dateStr) return "-";
  // Ensure UTC interpretation: append 'Z' if no timezone info present
  let normalizedDateStr = dateStr;
  if (!dateStr.endsWith('Z') && !dateStr.includes('+') && !dateStr.includes('-', 10)) {
    normalizedDateStr = dateStr + 'Z';
  }
  const date = new Date(normalizedDateStr);
  const now = new Date();
  const diffMs = date.getTime() - now.getTime();
  const diffMin = Math.round(diffMs / 60000);

  // For "last run" (isPast=true), always show past tense
  if (isPast || diffMin < 0) {
    const ago = Math.abs(diffMin);
    if (ago < 60) return `${ago} min ago`;
    if (ago < 1440) return `${Math.round(ago / 60)} hr ago`;
    return `${Math.round(ago / 1440)} days ago`;
  } else {
    if (diffMin < 60) return `in ${diffMin} min`;
    if (diffMin < 1440) return `in ${Math.round(diffMin / 60)} hr`;
    return `in ${Math.round(diffMin / 1440)} days`;
  }
}

function formatDateTime(dateStr?: string): string {
  if (!dateStr) return "-";
  // Ensure UTC interpretation: append 'Z' if no timezone info present
  let normalizedDateStr = dateStr;
  if (!dateStr.endsWith('Z') && !dateStr.includes('+') && !dateStr.includes('-', 10)) {
    normalizedDateStr = dateStr + 'Z';
  }
  return new Date(normalizedDateStr).toLocaleString();
}

export default function SchedulerTile() {
  const [overview, setOverview] = useState<SchedulerOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const fetchOverview = async () => {
    try {
      setLoading(true);
      const response = await fetch(`${API_BASE_URL}/scheduler/overview`, {
        credentials: "include",
      });
      if (!response.ok) {
        throw new Error(`HTTP error ${response.status}`);
      }
      const data = await response.json();
      setOverview(data);
      setError(null);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchOverview();
    // Refresh every 60 seconds
    const interval = setInterval(fetchOverview, 60000);
    return () => clearInterval(interval);
  }, []);

  const handleAction = async (jobId: string, action: "trigger" | "pause" | "resume") => {
    setActionLoading(`${jobId}-${action}`);
    try {
      const response = await fetch(`${API_BASE_URL}/scheduler/jobs/${jobId}/${action}`, {
        method: "POST",
        credentials: "include",
      });
      if (!response.ok) {
        throw new Error(`HTTP error ${response.status}`);
      }
      // Refresh the overview
      await fetchOverview();
    } catch (err: any) {
      alert(`Failed to ${action} job: ${err.message}`);
    } finally {
      setActionLoading(null);
    }
  };

  if (loading && !overview) {
    return (
      <div className="bg-white rounded-lg shadow-md p-6">
        <div className="flex items-center gap-3 mb-4">
          <div className="bg-violet-500 w-12 h-12 rounded-lg flex items-center justify-center text-white">
            <Clock size={24} />
          </div>
          <h3 className="text-xl font-semibold">Scheduler</h3>
        </div>
        <p className="text-gray-500">Loading...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-white rounded-lg shadow-md p-6">
        <div className="flex items-center gap-3 mb-4">
          <div className="bg-violet-500 w-12 h-12 rounded-lg flex items-center justify-center text-white">
            <Clock size={24} />
          </div>
          <h3 className="text-xl font-semibold">Scheduler</h3>
        </div>
        <p className="text-red-500">Error: {error}</p>
        <button
          onClick={fetchOverview}
          className="mt-2 text-sm text-indigo-600 hover:underline"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-lg shadow-md p-6">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="bg-violet-500 w-12 h-12 rounded-lg flex items-center justify-center text-white">
            <Clock size={24} />
          </div>
          <div>
            <h3 className="text-xl font-semibold">Scheduler</h3>
            <p className="text-gray-500 text-sm">Scheduled background jobs</p>
          </div>
        </div>
        <button
          onClick={fetchOverview}
          className="p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded"
          title="Refresh"
        >
          <RefreshCw size={18} className={loading ? "animate-spin" : ""} />
        </button>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-4 gap-3 mb-4">
        <div className="text-center p-2 bg-gray-50 rounded">
          <div className="text-2xl font-bold text-gray-700">{overview?.total_jobs || 0}</div>
          <div className="text-xs text-gray-500">Total</div>
        </div>
        <div className="text-center p-2 bg-green-50 rounded">
          <div className="text-2xl font-bold text-green-600">{overview?.active_jobs || 0}</div>
          <div className="text-xs text-gray-500">Active</div>
        </div>
        <div className="text-center p-2 bg-yellow-50 rounded">
          <div className="text-2xl font-bold text-yellow-600">{overview?.paused_jobs || 0}</div>
          <div className="text-xs text-gray-500">Paused</div>
        </div>
        <div className="text-center p-2 bg-gray-100 rounded">
          <div className="text-2xl font-bold text-gray-400">{overview?.disabled_jobs || 0}</div>
          <div className="text-xs text-gray-500">Disabled</div>
        </div>
      </div>

      {/* Expand/Collapse */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full text-sm text-indigo-600 hover:text-indigo-800 py-2 border-t border-gray-100"
      >
        {expanded ? "Hide Details" : `Show ${overview?.jobs.length || 0} Jobs`}
      </button>

      {/* Job Details */}
      {expanded && overview && (
        <div className="mt-4 space-y-3 max-h-96 overflow-y-auto">
          {overview.jobs.length === 0 ? (
            <p className="text-gray-500 text-center py-4">No scheduled jobs</p>
          ) : (
            overview.jobs.map((job) => (
              <div
                key={job.id}
                className={`p-3 rounded-lg border ${
                  !job.enabled
                    ? "bg-gray-50 border-gray-200"
                    : job.paused
                    ? "bg-yellow-50 border-yellow-200"
                    : "bg-white border-gray-200"
                }`}
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      {!job.enabled ? (
                        <XCircle size={16} className="text-gray-400" />
                      ) : job.paused ? (
                        <AlertCircle size={16} className="text-yellow-500" />
                      ) : (
                        <CheckCircle size={16} className="text-green-500" />
                      )}
                      <span className="font-medium text-sm">{job.name}</span>
                    </div>
                    {job.description && (
                      <p className="text-xs text-gray-500 mt-1 ml-6">{job.description}</p>
                    )}
                    <div className="mt-2 ml-6 grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                      <div>
                        <span className="text-gray-400">Schedule:</span>{" "}
                        <span className="text-gray-600">{formatTrigger(job.trigger_type, job.trigger_config)}</span>
                      </div>
                      <div>
                        <span className="text-gray-400">Owner:</span>{" "}
                        <span className="text-gray-600">{job.owner_type || "-"}</span>
                      </div>
                      <div>
                        <span className="text-gray-400">Last run:</span>{" "}
                        <span className="text-gray-600" title={formatDateTime(job.last_run_at)}>
                          {formatRelativeTime(job.last_run_at, true)}
                        </span>
                      </div>
                      <div>
                        <span className="text-gray-400">Next run:</span>{" "}
                        <span className="text-gray-600" title={formatDateTime(job.next_run_at)}>
                          {job.enabled && !job.paused ? formatRelativeTime(job.next_run_at) : "-"}
                        </span>
                      </div>
                      <div className="col-span-2">
                        <span className="text-gray-400">Created:</span>{" "}
                        <span className="text-gray-600">{formatDateTime(job.created_at)}</span>
                      </div>
                    </div>
                  </div>
                  <div className="flex gap-1 ml-2">
                    {job.enabled && (
                      <>
                        <button
                          onClick={() => handleAction(job.id, "trigger")}
                          disabled={actionLoading !== null}
                          className="p-1.5 text-indigo-600 hover:bg-indigo-50 rounded disabled:opacity-50"
                          title="Run Now"
                        >
                          <Play size={14} />
                        </button>
                        {job.paused ? (
                          <button
                            onClick={() => handleAction(job.id, "resume")}
                            disabled={actionLoading !== null}
                            className="p-1.5 text-green-600 hover:bg-green-50 rounded disabled:opacity-50"
                            title="Resume"
                          >
                            <Play size={14} />
                          </button>
                        ) : (
                          <button
                            onClick={() => handleAction(job.id, "pause")}
                            disabled={actionLoading !== null}
                            className="p-1.5 text-yellow-600 hover:bg-yellow-50 rounded disabled:opacity-50"
                            title="Pause"
                          >
                            <Pause size={14} />
                          </button>
                        )}
                      </>
                    )}
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
