import { useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { apiFetch } from "@/utils/api";
import { useAuth } from "@/context/AuthContext";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";
const DISMISS_KEY = "r1DeprecationBannerDismissed";

interface BannerItem {
  severity: "critical" | "high";
  method: string;
  path: string;
  why?: string;
  replacement?: string | null;
  days_left?: number | null;
  source?: "live" | "spec";
}

interface BannerData {
  audience: "admin" | "user" | "none";
  total: number;
  critical: number;
  items: BannerItem[];
  spec_file?: string;
  spec_stale?: boolean;
}

function readDismissed(): string | null {
  try {
    return localStorage.getItem(DISMISS_KEY);
  } catch {
    return null;
  }
}

/**
 * A strip across the top when R1 has deprecated (or retired) calls our tools
 * make. Fetched on navigation, never on a timer. Admins see the calls; other
 * users, when the backend lets them see it at all, are told to tell an admin.
 */
const R1DeprecationBanner = () => {
  const { isAuthenticated, userRole } = useAuth();
  const location = useLocation();
  const [data, setData] = useState<BannerData | null>(null);
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState<string | null>(readDismissed);

  useEffect(() => {
    if (!isAuthenticated) {
      setData(null);
      return;
    }
    const controller = new AbortController();
    apiFetch(`${API_BASE_URL}/r1-deprecations/banner`, { signal: controller.signal })
      .then((res) => (res.ok ? res.json() : null))
      .then((json) => json && setData(json))
      .catch(() => {});
    return () => controller.abort();
  }, [isAuthenticated, location.pathname]);

  const fingerprint = useMemo(
    () => (data ? data.items.map((i) => `${i.severity}:${i.method} ${i.path}`).join("|") : ""),
    [data],
  );

  if (!data || data.total === 0 || data.audience === "none" || dismissed === fingerprint) return null;

  const dismiss = () => {
    try {
      localStorage.setItem(DISMISS_KEY, fingerprint);
    } catch {
      /* private window: dismissal lasts until reload */
    }
    setDismissed(fingerprint);
  };

  const critical = data.critical > 0;
  const tone = critical ? "bg-red-600 text-white" : "bg-amber-400 text-gray-900";
  const names = data.items.slice(0, 3).map((i) => `${i.method} ${i.path}`).join(", ");
  const more = data.total > 3 ? ` and ${data.total - 3} more` : "";

  return (
    <div className={`${tone} text-sm`} role="alert">
      <div className="flex items-start gap-3 px-4 py-2">
        <span className="font-semibold shrink-0">{critical ? "R1 API removed" : "R1 API deprecation"}</span>
        <span className="flex-1 min-w-0">
          {data.audience === "admin" ? (
            <>
              {data.total} call{data.total === 1 ? "" : "s"} our tools make {critical ? "include one R1 no longer answers" : "are deprecated or due for removal"}: {names}{more}.
            </>
          ) : (
            <>Some R1 calls these tools use are deprecated or about to be. Please tell your admin: {names}{more}.</>
          )}
        </span>
        {data.audience === "admin" && (
          <button onClick={() => setOpen((o) => !o)} className="underline shrink-0">
            {open ? "Hide" : "Details"}
          </button>
        )}
        <button onClick={dismiss} className="shrink-0 opacity-80 hover:opacity-100" aria-label="Dismiss">
          ✕
        </button>
      </div>
      {open && data.audience === "admin" && (
        <div className="bg-white text-gray-800 border-b border-gray-200 max-h-72 overflow-y-auto">
          <table className="min-w-full text-xs">
            <tbody>
              {data.items.map((i) => (
                <tr key={`${i.method} ${i.path}`} className="border-t border-gray-100 align-top">
                  <td className="px-4 py-1 font-semibold uppercase whitespace-nowrap">{i.severity}</td>
                  <td className="px-2 py-1 font-mono whitespace-nowrap">{i.method} {i.path}</td>
                  <td className="px-2 py-1">
                    {i.why}
                    {i.source === "live" && <span className="ml-1 text-gray-500">(seen on a live response)</span>}
                  </td>
                  <td className="px-2 py-1 font-mono text-gray-600">{i.replacement ? `→ ${i.replacement}` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="px-4 py-2 text-xs text-gray-500">
            Checked against {data.spec_file}
            {data.spec_stale && " (getting old: upload a newer R1 spec to api/specs)"}.
            {data.total > data.items.length && ` Showing ${data.items.length} of ${data.total}.`}
            {userRole === "super" && (
              <>
                {" "}
                <Link to="/r1-call-timing" className="underline">Full report</Link>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default R1DeprecationBanner;
