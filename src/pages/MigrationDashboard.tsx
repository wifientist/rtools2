import { useState, useEffect, useMemo } from "react";
import { useAuth } from "@/context/AuthContext";
import { BarChart3, RefreshCw, Pencil, Check, ChevronUp, ChevronDown, AlertCircle, Wifi, MapPin, Building2, Target, ShieldX } from "lucide-react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

interface ECTenantStats {
  id: string;
  name: string;
  ap_count: number;
  venue_count: number;
  error: string | null;
}

interface DashboardData {
  total_aps: number;
  total_venues: number;
  total_ecs: number;
  errors: number;
  tenants: ECTenantStats[];
}

type SortField = "name" | "ap_count" | "venue_count";

function getMessage(pct: number): string {
  if (pct >= 100) return "Migration complete!";
  if (pct >= 90) return "Almost there!";
  if (pct >= 75) return "The finish line is in sight!";
  if (pct >= 50) return "Past the halfway mark!";
  if (pct >= 25) return "Making great progress!";
  return "The journey begins!";
}

const MigrationDashboard = () => {
  const { controllers, featureAccess } = useAuth();

  const mspControllers = controllers.filter(
    (c) => c.controller_subtype === "MSP"
  );

  const [controllerID, setControllerID] = useState<number | null>(null);
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState<number>(() => {
    const saved = localStorage.getItem("migration-dashboard-target");
    return saved ? parseInt(saved, 10) : 180000;
  });
  const [editingTarget, setEditingTarget] = useState(false);
  const [targetInput, setTargetInput] = useState("");
  const [sortField, setSortField] = useState<SortField>("ap_count");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [animatedPct, setAnimatedPct] = useState(0);

  // Auto-select first MSP controller
  useEffect(() => {
    if (mspControllers.length > 0 && !controllerID) {
      setControllerID(mspControllers[0].id);
    }
  }, [controllers]);

  // Fetch data when controller changes
  useEffect(() => {
    if (!controllerID) return;
    const abortController = new AbortController();

    setLoading(true);
    setError(null);
    setAnimatedPct(0);

    fetch(`${API_BASE_URL}/migration-dashboard/progress/${controllerID}`, {
      credentials: "include",
      signal: abortController.signal,
    })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((json) => {
        setData(json.data);
        setLoading(false);
      })
      .catch((err) => {
        if (err.name !== "AbortError") {
          setError(err.message);
          setLoading(false);
        }
      });

    return () => abortController.abort();
  }, [controllerID]);

  // Animate progress bar after data loads
  useEffect(() => {
    if (!data) return;
    const pct = Math.min((data.total_aps / target) * 100, 100);
    const timer = setTimeout(() => setAnimatedPct(pct), 100);
    return () => clearTimeout(timer);
  }, [data, target]);

  const percentage = data ? (data.total_aps / target) * 100 : 0;
  const remaining = data ? Math.max(target - data.total_aps, 0) : target;

  // Sorted tenants
  const sortedTenants = useMemo(() => {
    if (!data?.tenants) return [];
    return [...data.tenants].sort((a, b) => {
      const aVal = a[sortField];
      const bVal = b[sortField];
      if (typeof aVal === "string" && typeof bVal === "string") {
        return sortDir === "asc"
          ? aVal.localeCompare(bVal)
          : bVal.localeCompare(aVal);
      }
      return sortDir === "asc"
        ? (aVal as number) - (bVal as number)
        : (bVal as number) - (aVal as number);
    });
  }, [data?.tenants, sortField, sortDir]);

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir(field === "name" ? "asc" : "desc");
    }
  };

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) return null;
    return sortDir === "asc" ? (
      <ChevronUp size={14} className="inline ml-1" />
    ) : (
      <ChevronDown size={14} className="inline ml-1" />
    );
  };

  const saveTarget = () => {
    const val = parseInt(targetInput, 10);
    if (!isNaN(val) && val > 0) {
      setTarget(val);
      localStorage.setItem("migration-dashboard-target", String(val));
    }
    setEditingTarget(false);
  };

  if (!featureAccess.migration_dashboard) {
    return (
      <div className="max-w-4xl mx-auto py-16 text-center">
        <ShieldX size={48} className="mx-auto text-gray-300 mb-4" />
        <h2 className="text-xl font-semibold text-gray-700 mb-2">
          Access Restricted
        </h2>
        <p className="text-gray-500">
          The Migration Dashboard is restricted to authorized users.
        </p>
      </div>
    );
  }

  if (mspControllers.length === 0) {
    return (
      <div className="max-w-4xl mx-auto py-16 text-center">
        <BarChart3 size={48} className="mx-auto text-gray-300 mb-4" />
        <h2 className="text-xl font-semibold text-gray-700 mb-2">
          No MSP Controller Found
        </h2>
        <p className="text-gray-500">
          The Migration Dashboard requires an MSP-type RuckusONE controller.
          Add one on the Controllers page.
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">
            Migration Dashboard
          </h1>
          <p className="text-gray-500 mt-1">SZ to R1 Migration Progress</p>
        </div>
        <div className="flex items-center gap-3">
          {mspControllers.length > 1 && (
            <select
              value={controllerID ?? ""}
              onChange={(e) => setControllerID(parseInt(e.target.value))}
              className="px-3 py-2 border rounded-lg text-sm"
            >
              {mspControllers.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          )}
          <button
            onClick={() => {
              if (controllerID) {
                setData(null);
                // Trigger re-fetch by briefly clearing and setting
                const id = controllerID;
                setControllerID(null);
                setTimeout(() => setControllerID(id), 0);
              }
            }}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 bg-white border rounded-lg hover:bg-gray-50 text-sm disabled:opacity-50"
          >
            <RefreshCw size={16} className={loading ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6 flex items-center gap-3">
          <AlertCircle size={20} className="text-red-500 flex-shrink-0" />
          <p className="text-red-700">{error}</p>
        </div>
      )}

      {/* Loading state */}
      {loading && (
        <div className="text-center py-16">
          <RefreshCw
            size={32}
            className="mx-auto text-indigo-500 animate-spin mb-4"
          />
          <p className="text-gray-600 font-medium">
            Querying {mspControllers.find((c) => c.id === controllerID)?.name || "MSP"} tenants...
          </p>
          <p className="text-gray-400 text-sm mt-1">
            This may take a few seconds
          </p>
        </div>
      )}

      {/* Dashboard content */}
      {data && !loading && (
        <>
          {/* Hero Progress Section */}
          <div className="bg-white rounded-xl shadow-lg p-8 mb-6">
            {/* Target editor */}
            <div className="flex items-center justify-between mb-4">
              <div className="text-sm text-gray-500 flex items-center gap-2">
                <Target size={14} />
                {editingTarget ? (
                  <span className="flex items-center gap-1">
                    Target:{" "}
                    <input
                      type="number"
                      value={targetInput}
                      onChange={(e) => setTargetInput(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && saveTarget()}
                      onBlur={saveTarget}
                      autoFocus
                      className="w-28 px-2 py-0.5 border rounded text-sm"
                    />
                    <button
                      onClick={saveTarget}
                      className="text-green-600 hover:text-green-700"
                    >
                      <Check size={14} />
                    </button>
                  </span>
                ) : (
                  <span
                    className="cursor-pointer hover:text-gray-700 group"
                    onClick={() => {
                      setTargetInput(String(target));
                      setEditingTarget(true);
                    }}
                  >
                    Target: {target.toLocaleString()} APs
                    <Pencil
                      size={12}
                      className="inline ml-1 opacity-0 group-hover:opacity-100 transition"
                    />
                  </span>
                )}
              </div>
              <div className="text-sm font-medium text-gray-600">
                {getMessage(percentage)}
              </div>
            </div>

            {/* Big progress bar */}
            <div className="relative">
              <div className="w-full bg-gray-100 rounded-full h-12 overflow-hidden">
                <div
                  className={`h-12 rounded-full transition-all duration-1000 ease-out ${
                    percentage >= 100
                      ? "bg-gradient-to-r from-green-500 to-emerald-400"
                      : "bg-gradient-to-r from-blue-600 to-indigo-500"
                  }`}
                  style={{ width: `${Math.min(animatedPct, 100)}%` }}
                />
              </div>
              <div className="absolute inset-0 flex items-center justify-center">
                <span className="text-lg font-bold drop-shadow-sm mix-blend-difference text-white">
                  {data.total_aps.toLocaleString()} / {target.toLocaleString()}{" "}
                  APs ({percentage.toFixed(1)}%)
                </span>
              </div>
              {/* Milestone markers */}
              {[25, 50, 75].map((m) => (
                <div
                  key={m}
                  className="absolute top-0 h-12 border-l border-gray-300 border-dashed opacity-40"
                  style={{ left: `${m}%` }}
                />
              ))}
            </div>
          </div>

          {/* Metrics Strip */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <MetricCard
              icon={<Wifi size={24} />}
              label="Total APs"
              value={data.total_aps.toLocaleString()}
              color="blue"
            />
            <MetricCard
              icon={<MapPin size={24} />}
              label="Total Venues"
              value={data.total_venues.toLocaleString()}
              color="purple"
            />
            <MetricCard
              icon={<Building2 size={24} />}
              label="EC Tenants"
              value={data.total_ecs.toLocaleString()}
              color="teal"
            />
            <MetricCard
              icon={<Target size={24} />}
              label="Remaining"
              value={remaining.toLocaleString()}
              color="amber"
            />
          </div>

          {/* Errors banner */}
          {data.errors > 0 && (
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mb-6 flex items-center gap-2 text-sm text-amber-700">
              <AlertCircle size={16} />
              {data.errors} tenant{data.errors > 1 ? "s" : ""} returned errors
              during query
            </div>
          )}

          {/* EC Breakdown Table */}
          <div className="bg-white rounded-xl shadow overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-100">
              <h2 className="text-lg font-semibold text-gray-800">
                Tenant Breakdown
              </h2>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      #
                    </th>
                    <th
                      className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-700"
                      onClick={() => handleSort("name")}
                    >
                      EC Tenant <SortIcon field="name" />
                    </th>
                    <th
                      className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-700"
                      onClick={() => handleSort("ap_count")}
                    >
                      APs <SortIcon field="ap_count" />
                    </th>
                    <th
                      className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-700"
                      onClick={() => handleSort("venue_count")}
                    >
                      Venues <SortIcon field="venue_count" />
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Share
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {sortedTenants.map((tenant, idx) => {
                    const share =
                      data.total_aps > 0
                        ? (tenant.ap_count / data.total_aps) * 100
                        : 0;
                    return (
                      <tr
                        key={tenant.id}
                        className="hover:bg-gray-50 transition-colors"
                      >
                        <td className="px-6 py-3 text-sm text-gray-400">
                          {idx + 1}
                        </td>
                        <td className="px-6 py-3 text-sm font-medium text-gray-800">
                          {tenant.name}
                          {tenant.error && (
                            <span
                              className="ml-2 text-red-400"
                              title={tenant.error}
                            >
                              <AlertCircle
                                size={14}
                                className="inline -mt-0.5"
                              />
                            </span>
                          )}
                        </td>
                        <td className="px-6 py-3 text-sm text-right font-mono text-gray-700">
                          {tenant.ap_count.toLocaleString()}
                        </td>
                        <td className="px-6 py-3 text-sm text-right font-mono text-gray-700">
                          {tenant.venue_count.toLocaleString()}
                        </td>
                        <td className="px-6 py-3">
                          <div className="flex items-center gap-2">
                            <div className="w-24 bg-gray-100 rounded-full h-2">
                              <div
                                className="h-2 rounded-full bg-indigo-400"
                                style={{
                                  width: `${Math.min(share, 100)}%`,
                                }}
                              />
                            </div>
                            <span className="text-xs text-gray-500 w-12">
                              {share.toFixed(1)}%
                            </span>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
};

// Metric card component
function MetricCard({
  icon,
  label,
  value,
  color,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  color: "blue" | "purple" | "teal" | "amber";
}) {
  const colors = {
    blue: "bg-blue-50 text-blue-600",
    purple: "bg-purple-50 text-purple-600",
    teal: "bg-teal-50 text-teal-600",
    amber: "bg-amber-50 text-amber-600",
  };

  return (
    <div className="bg-white rounded-xl shadow p-5">
      <div
        className={`w-10 h-10 rounded-lg flex items-center justify-center mb-3 ${colors[color]}`}
      >
        {icon}
      </div>
      <div className="text-2xl font-bold text-gray-900">{value}</div>
      <div className="text-sm text-gray-500 mt-1">{label}</div>
    </div>
  );
}

export default MigrationDashboard;
