import { useEffect, useMemo, useState } from "react";

// ============================================================================
// Bulk WLAN Edit — the first section: what each network type actually has
// (captive portal, DPSK pool, RADIUS), read-only, plus the captive portal
// fields that can be changed. Integration keys arrive only as fingerprints;
// "Show" asks the server for one network's key.
// ============================================================================

export interface GuestProfile {
  guestNetworkType: string | null;
  portalUrl: string | null;
  redirectUrl: string | null;
  provider: string | null;
  authType: string | null;
  encryptMacIp: boolean | null;
  keyId: string | null;
  keyMatchesSaved: boolean | null;
  walledGardens: string[];
  sessionTimeout: number | null;
  gracePeriod: number | null;
  bypassCNA: boolean | null;
  macBypass: boolean | null;
  enableDhcp: boolean | null;
}

export interface NetworkProfile {
  kind: string;
  type: string | null;
  wlanSecurity: string | null;
  aaaAuthType?: string;
  guest?: GuestProfile;
  radius?: { auth?: string; accounting?: string };
  dpskServices?: string[];
}

export interface ProfiledNetwork {
  name: string;
  ssid: string;
  kind?: string;
  profile?: NetworkProfile;
}

// Captive portal fields: WISPr only, or every captive portal network.
export const WISPR_FIELDS = ["portalUrl", "portalRedirectUrl", "portalWalledGardens", "portalEncryptMacIp", "portalKey"];
export const GUEST_FIELDS = ["portalSessionTimeout", "portalGracePeriod", "bypassCNA", "bypassCPUsingMacAddressAuthentication"];

/** Whether a network of this kind has the field (non-portal fields: always). */
export function fieldApplies(field: string, kind: string | undefined): boolean {
  if (WISPR_FIELDS.includes(field)) return kind === "wispr";
  if (GUEST_FIELDS.includes(field)) return kind === "wispr" || kind === "guest";
  return true;
}

export const PORTAL_KEY_LABELS: Record<string, string> = {
  saved: "Saved key",
  other: "Different key",
  none: "No key",
};

const KIND_LABELS: Record<string, string> = {
  wispr: "WISPr captive portal",
  guest: "Other captive portal",
  dpsk: "DPSK",
  aaa: "Enterprise (AAA)",
  psk: "PSK",
  open: "Open",
  hotspot20: "Hotspot 2.0",
};
const KIND_ORDER = ["wispr", "guest", "dpsk", "aaa", "psk", "open", "hotspot20"];

const GUEST_TYPE_LABELS: Record<string, string> = {
  ClickThrough: "Click-through",
  SelfSignIn: "Self sign-in",
  HostApproval: "Host approval",
  GuestPass: "Guest pass",
  WISPr: "WISPr",
  Cloudpath: "Cloudpath",
  Workflow: "Workflow",
  Directory: "Directory",
  SAML: "SAML",
};

// ---- Value distribution ----

interface Bucket { key: string; label: React.ReactNode; names: string[] }

function buckets<T>(rows: { name: string; value: T }[], keyOf: (v: T) => string,
                    labelOf: (v: T) => React.ReactNode): Bucket[] {
  const map = new Map<string, Bucket>();
  for (const r of rows) {
    const k = keyOf(r.value);
    if (!map.has(k)) map.set(k, { key: k, label: labelOf(r.value), names: [] });
    map.get(k)!.names.push(r.name);
  }
  return [...map.values()].sort((a, b) => b.names.length - a.names.length);
}

function Names({ names }: { names: string[] }) {
  const [all, setAll] = useState(false);
  const shown = all ? names : names.slice(0, 5);
  return (
    <span className="text-gray-500">
      {[...shown].sort().join(", ")}
      {names.length > 5 && (
        <button type="button" className="ml-1 text-blue-600 hover:underline" onClick={() => setAll(!all)}>
          {all ? "less" : `+${names.length - 5} more`}
        </button>
      )}
    </span>
  );
}

/** One setting: its value when every network agrees, else "Multiple values" that expands. */
function ValueRow({ label, list, total }: { label: string; list: Bucket[]; total: number }) {
  const [open, setOpen] = useState(false);
  if (!list.length) return null;
  const same = list.length === 1;
  return (
    <div className="py-1.5 border-b border-gray-100 text-sm">
      <div className="flex gap-3">
        <div className="w-48 flex-shrink-0 text-gray-600">{label}</div>
        <div className="flex-1 min-w-0 break-words">
          {same ? (
            <>
              {list[0].label}
              {list[0].names.length < total && (
                <span className="text-xs text-gray-400"> ({list[0].names.length} of {total})</span>
              )}
            </>
          ) : (
            <button type="button" onClick={() => setOpen(!open)}
              className="text-amber-700 hover:underline">
              Multiple values ({list.length}) {open ? "▴" : "▾"}
            </button>
          )}
        </div>
      </div>
      {!same && open && (
        <div className="ml-48 pl-3 mt-1 space-y-1.5 text-xs">
          {list.map((b) => (
            <div key={b.key} className="border-l-2 border-amber-200 pl-2">
              <div className="text-gray-800 break-words">
                {b.label} <span className="text-gray-400">— {b.names.length} network{b.names.length === 1 ? "" : "s"}</span>
              </div>
              <div className="mt-0.5"><Names names={b.names} /></div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const show = (v: any, empty = "Not set") =>
  v === null || v === undefined || v === "" ? <span className="text-gray-400">{empty}</span> : String(v);
const onOff = (v: boolean | null | undefined) =>
  v === null || v === undefined ? <span className="text-gray-400">Not set</span> : v ? "Enabled" : "Disabled";

function ListValue({ items, empty = "None" }: { items: string[]; empty?: string }) {
  const [all, setAll] = useState(false);
  if (!items.length) return <span className="text-gray-400">{empty}</span>;
  const shown = all ? items : items.slice(0, 8);
  return (
    <span className="inline-flex flex-wrap gap-1 align-middle">
      {shown.map((x) => (
        <code key={x} className="px-1.5 py-0.5 bg-gray-100 rounded text-xs">{x}</code>
      ))}
      {items.length > 8 && (
        <button type="button" className="text-xs text-blue-600 hover:underline" onClick={() => setAll(!all)}>
          {all ? "less" : `+${items.length - 8} more`}
        </button>
      )}
    </span>
  );
}

// ---- Integration key row ----

function KeyRow({ nets, keyLetters, onReveal }: {
  nets: { id: string; name: string; g: GuestProfile }[];
  keyLetters: Record<string, string>;
  onReveal: (networkId: string) => Promise<string | null>;
}) {
  const [revealed, setRevealed] = useState<Record<string, string | null>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const groups = useMemo(() => {
    const m = new Map<string, { keyId: string | null; matches: boolean | null; nets: typeof nets }>();
    for (const n of nets) {
      const k = n.g.keyId || "__none__";
      if (!m.has(k)) m.set(k, { keyId: n.g.keyId, matches: n.g.keyMatchesSaved, nets: [] });
      m.get(k)!.nets.push(n);
    }
    return [...m.values()].sort((a, b) => b.nets.length - a.nets.length);
  }, [nets]);

  const reveal = async (keyId: string, networkId: string) => {
    if (keyId in revealed) {
      setRevealed(({ [keyId]: _, ...rest }) => rest);
      return;
    }
    setBusy(keyId);
    try {
      const key = await onReveal(networkId);
      setRevealed((r) => ({ ...r, [keyId]: key }));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="py-1.5 border-b border-gray-100 text-sm">
      <div className="flex gap-3">
        <div className="w-48 flex-shrink-0 text-gray-600">Integration key</div>
        <div className="flex-1 min-w-0 space-y-1.5">
          {groups.map((grp) => (
            <div key={grp.keyId || "none"}>
              <div className="flex flex-wrap items-center gap-2">
                {grp.keyId ? (
                  <>
                    <span className="font-medium">Key {keyLetters[grp.keyId]}</span>
                    {grp.matches === true && (
                      <span className="text-xs px-1.5 py-0.5 rounded bg-green-50 text-green-700 border border-green-200">matches the saved key</span>
                    )}
                    {grp.matches === false && (
                      <span className="text-xs px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200">differs from the saved key</span>
                    )}
                    <button type="button" disabled={busy === grp.keyId}
                      className="text-xs text-blue-600 hover:underline disabled:text-gray-400"
                      onClick={() => reveal(grp.keyId!, grp.nets[0].id)}>
                      {grp.keyId in revealed ? "Hide" : busy === grp.keyId ? "..." : "Show"}
                    </button>
                    {grp.keyId in revealed && (
                      <code className="px-1.5 py-0.5 bg-gray-100 rounded text-xs select-all">{revealed[grp.keyId] ?? "(none)"}</code>
                    )}
                  </>
                ) : (
                  <span className="text-gray-400">No key</span>
                )}
                {groups.length > 1 && (
                  <span className="text-xs text-gray-400">— {grp.nets.length} network{grp.nets.length === 1 ? "" : "s"}</span>
                )}
              </div>
              {groups.length > 1 && (
                <div className="text-xs mt-0.5"><Names names={grp.nets.map((n) => n.name)} /></div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ---- Overview ----

export function TypeOverview({ networks, onReveal }: {
  networks: Record<string, ProfiledNetwork>;
  onReveal: (networkId: string) => Promise<string | null>;
}) {
  const byKind = useMemo(() => {
    const m: Record<string, { id: string; name: string; p: NetworkProfile }[]> = {};
    for (const [id, n] of Object.entries(networks)) {
      if (!n.profile) continue;
      (m[n.profile.kind] ||= []).push({ id, name: n.name, p: n.profile });
    }
    return m;
  }, [networks]);
  const kinds = Object.keys(byKind).sort(
    (a, b) => (KIND_ORDER.indexOf(a) + 1 || 99) - (KIND_ORDER.indexOf(b) + 1 || 99));

  // Letters for keys, most-used first, stable across the whole selection.
  const keyLetters = useMemo(() => {
    const counts = new Map<string, number>();
    for (const n of Object.values(networks)) {
      const k = n.profile?.guest?.keyId;
      if (k) counts.set(k, (counts.get(k) || 0) + 1);
    }
    const out: Record<string, string> = {};
    [...counts.entries()].sort((a, b) => b[1] - a[1])
      .forEach(([k], i) => { out[k] = i < 26 ? String.fromCharCode(65 + i) : `#${i + 1}`; });
    return out;
  }, [networks]);

  if (!kinds.length) return null;

  const row = <T,>(label: string, nets: { name: string; p: NetworkProfile }[], get: (p: NetworkProfile) => T,
                   labelOf: (v: T) => React.ReactNode, keyOf: (v: T) => string = (v) => JSON.stringify(v ?? null)) => (
    <ValueRow label={label} total={nets.length}
      list={buckets(nets.map((n) => ({ name: n.name, value: get(n.p) })), keyOf, labelOf)} />
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2 text-xs">
        {kinds.map((k) => (
          <span key={k} className="px-2 py-1 rounded-full bg-gray-100 text-gray-700">
            {byKind[k].length} {KIND_LABELS[k] || k}
          </span>
        ))}
      </div>

      {kinds.map((kind) => {
        const nets = byKind[kind];
        const guestNets = nets.filter((n) => n.p.guest).map((n) => ({ id: n.id, name: n.name, g: n.p.guest! }));
        return (
          <div key={kind} className="border border-gray-200 rounded-md">
            <div className="px-3 py-2 bg-gray-50 border-b border-gray-200 text-sm font-semibold text-gray-700">
              {KIND_LABELS[kind] || kind} <span className="font-normal text-gray-500">— {nets.length} network{nets.length === 1 ? "" : "s"}</span>
            </div>
            <div className="px-3 py-1">
              {kind === "wispr" && (
                <>
                  {row("Portal URL", nets, (p) => p.guest?.portalUrl ?? null, (v) => show(v))}
                  {row("Redirect URL", nets, (p) => p.guest?.redirectUrl ?? null, (v) => show(v))}
                  {row("Provider", nets, (p) => p.guest?.provider ?? null, (v) => show(v))}
                  <KeyRow nets={guestNets} keyLetters={keyLetters} onReveal={onReveal} />
                  {row("MAC & IP encryption", nets, (p) => p.guest?.encryptMacIp ?? null, onOff)}
                  {row("Authentication", nets, (p) => p.guest?.authType ?? null,
                    (v) => (v === "ALWAYS_ACCEPT" ? "Always accept" : v === "RADIUS" ? "RADIUS" : show(v)))}
                  {nets.some((n) => n.p.radius) && (
                    <>
                      {row("RADIUS auth profile", nets, (p) => p.radius?.auth ?? null, (v) => show(v, "None"))}
                      {row("RADIUS accounting profile", nets, (p) => p.radius?.accounting ?? null, (v) => show(v, "None"))}
                    </>
                  )}
                  {row("Walled garden", nets, (p) => p.guest?.walledGardens ?? [], (v) => <ListValue items={v} />)}
                </>
              )}
              {kind === "guest" &&
                row("Portal type", nets, (p) => p.guest?.guestNetworkType ?? null,
                  (v) => show(v ? GUEST_TYPE_LABELS[v] || v : null))}
              {(kind === "wispr" || kind === "guest") && (
                <>
                  {row("Session timeout", nets, (p) => p.guest?.sessionTimeout ?? null, (v) => (v == null ? show(v) : `${v} min`))}
                  {row("Grace period", nets, (p) => p.guest?.gracePeriod ?? null, (v) => (v == null ? show(v) : `${v} min`))}
                  {row("Bypass CNA", nets, (p) => p.guest?.bypassCNA ?? null, onOff)}
                  {row("MAC auth bypass", nets, (p) => p.guest?.macBypass ?? null, onOff)}
                  {row("R1 DHCP service", nets, (p) => p.guest?.enableDhcp ?? null, onOff)}
                </>
              )}
              {kind === "dpsk" &&
                row("DPSK pool", nets, (p) => p.dpskServices ?? null,
                  (v) => (v === null ? show(null, "Unknown") : v.length ? v.join(", ") : <span className="text-red-600">No pool linked</span>))}
              {kind === "aaa" && (
                <>
                  {row("Auth type", nets, (p) => p.aaaAuthType ?? null, (v) => show(v))}
                  {row("RADIUS auth profile", nets, (p) => p.radius?.auth ?? null, (v) => show(v, "None"))}
                  {row("RADIUS accounting profile", nets, (p) => p.radius?.accounting ?? null, (v) => show(v, "None"))}
                </>
              )}
              {row("Security", nets, (p) => p.wlanSecurity ?? null, (v) => show(v))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---- Captive portal editor ----
// Module-level inputs (not defined inside the page) so typing keeps focus.

function Current({ summary }: { summary?: Record<string, number> }) {
  if (!summary) return null;
  return (
    <div className="text-xs text-gray-400 mt-0.5 truncate">
      Current: {Object.entries(summary).map(([k, c]) => `${c}x ${k}`).join(", ")}
    </div>
  );
}

function Row({ label, note, summary, children }: {
  label: string; note?: string; summary?: Record<string, number>; children: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3 py-2 border-b border-gray-100">
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-gray-700">
          {label} {note && <span className="text-xs font-normal text-gray-400">{note}</span>}
        </div>
        <Current summary={summary} />
      </div>
      <div className="flex-shrink-0">{children}</div>
    </div>
  );
}

function Tri({ value, onChange }: { value: boolean | undefined; onChange: (v: boolean | undefined) => void }) {
  const opts: [string, boolean | undefined][] = [["No Change", undefined], ["Enable", true], ["Disable", false]];
  return (
    <div className="flex gap-1">
      {opts.map(([label, v]) => {
        const active = value === v;
        return (
          <button key={label} type="button" onClick={() => onChange(v)}
            className={`px-3 py-1 text-xs rounded ${active
              ? v === undefined ? "bg-gray-200 text-gray-700" : v ? "bg-green-600 text-white" : "bg-red-600 text-white"
              : "bg-gray-100 text-gray-400 hover:bg-gray-200"}`}>
            {label}
          </button>
        );
      })}
    </div>
  );
}

function Num({ value, onChange, min, max }: {
  value: number | undefined; onChange: (v: number | undefined) => void; min: number; max: number;
}) {
  return (
    <div className="flex items-center gap-1">
      <input type="number" min={min} max={max} placeholder="No Change" value={value ?? ""}
        onChange={(e) => onChange(e.target.value ? parseInt(e.target.value) : undefined)}
        className="w-28 px-2 py-1 text-sm border rounded" />
      <span className="text-xs text-gray-500">min</span>
    </div>
  );
}

function Url({ value, onChange }: { value: string | undefined; onChange: (v: string | undefined) => void }) {
  const bad = !!value && !/^https?:\/\/\S+$/.test(value);
  return (
    <input type="text" placeholder="No Change" value={value ?? ""}
      onChange={(e) => onChange(e.target.value.trim() || undefined)}
      className={`w-96 max-w-full px-2 py-1 text-sm border rounded ${bad ? "border-red-400" : ""}`} />
  );
}

function WalledGarden({ value, onChange, current }: {
  value: string[] | undefined; onChange: (v: string[] | undefined) => void; current: string[] | null;
}) {
  // Keep the raw text while typing; the list is derived from it.
  const [text, setText] = useState(value ? value.join("\n") : "");
  // Cleared from outside (changes reset after a job): clear the box too.
  useEffect(() => {
    if (value === undefined) setText((t) => (t.trim() ? "" : t));
  }, [value]);
  const set = (t: string) => {
    setText(t);
    const items = t.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);
    onChange(items.length ? items : undefined);
  };
  return (
    <div className="flex flex-col items-end gap-1">
      <textarea rows={4} placeholder="No Change — one entry per line" value={text}
        onChange={(e) => set(e.target.value)}
        className="w-96 max-w-full px-2 py-1 text-sm border rounded font-mono" />
      {current && (
        <button type="button" className="text-xs text-blue-600 hover:underline" onClick={() => set(current.join("\n"))}>
          Start from the current list
        </button>
      )}
    </div>
  );
}

export interface PortalChanges {
  portalKey?: "saved";
  portalEncryptMacIp?: boolean;
  portalUrl?: string;
  portalRedirectUrl?: string;
  portalWalledGardens?: string[];
  portalSessionTimeout?: number;
  portalGracePeriod?: number;
  bypassCNA?: boolean;
  bypassCPUsingMacAddressAuthentication?: boolean;
}

export function CaptivePortalEditor({ networks, changes, setField, summary, hasSavedKey }: {
  networks: Record<string, ProfiledNetwork>;
  changes: PortalChanges;
  setField: (field: keyof PortalChanges, value: any) => void;
  summary: Record<string, Record<string, number>>;
  hasSavedKey: boolean;
}) {
  const all = Object.values(networks);
  const wispr = all.filter((n) => n.kind === "wispr");
  const guest = all.filter((n) => n.kind === "wispr" || n.kind === "guest");
  if (!guest.length) return null;
  const mixed = wispr.length !== guest.length;
  const wisprNote = mixed ? `(${wispr.length} WISPr network${wispr.length === 1 ? "" : "s"} only)` : undefined;

  // The walled garden every WISPr network shares, if they all share one.
  const lists = new Set(wispr.map((n) => JSON.stringify(n.profile?.guest?.walledGardens ?? [])));
  const sharedList = lists.size === 1 ? (JSON.parse([...lists][0]) as string[]) : null;

  return (
    <div className="mt-4 border border-blue-200 rounded-md">
      <div className="px-3 py-2 bg-blue-50 border-b border-blue-200 text-sm font-semibold text-blue-900">
        Change captive portal settings
        <span className="font-normal text-blue-700"> — applies only to the {guest.length} captive portal network{guest.length === 1 ? "" : "s"} selected; other networks are left alone</span>
      </div>
      <div className="px-3">
        {wispr.length > 0 && (
          <>
            <Row label="Integration key" note={wisprNote} summary={summary.portalKey}>
              <select value={changes.portalKey ?? ""} disabled={!hasSavedKey}
                onChange={(e) => setField("portalKey", e.target.value || undefined)}
                className="px-3 py-1 text-sm border rounded disabled:bg-gray-50">
                <option value="">No Change</option>
                <option value="saved">Set to the key saved on this controller</option>
              </select>
              {!hasSavedKey && (
                <div className="text-xs text-gray-400 mt-1 max-w-xs text-right">
                  No key is saved on this controller (Controllers → Edit → Portal integration key).
                </div>
              )}
            </Row>
            <Row label="MAC & IP encryption" note={wisprNote} summary={summary.portalEncryptMacIp}>
              <Tri value={changes.portalEncryptMacIp} onChange={(v) => setField("portalEncryptMacIp", v)} />
            </Row>
            <Row label="Portal URL" note={wisprNote} summary={summary.portalUrl}>
              <Url value={changes.portalUrl} onChange={(v) => setField("portalUrl", v)} />
            </Row>
            <Row label="Redirect URL" note={wisprNote} summary={summary.portalRedirectUrl}>
              <Url value={changes.portalRedirectUrl} onChange={(v) => setField("portalRedirectUrl", v)} />
            </Row>
            <Row label="Walled garden" note={wisprNote ?? "(replaces the whole list)"}>
              <WalledGarden value={changes.portalWalledGardens} current={sharedList}
                onChange={(v) => setField("portalWalledGardens", v)} />
            </Row>
          </>
        )}
        <Row label="Session timeout" summary={summary.portalSessionTimeout}>
          <Num value={changes.portalSessionTimeout} min={1} max={14400} onChange={(v) => setField("portalSessionTimeout", v)} />
        </Row>
        <Row label="Grace period" summary={summary.portalGracePeriod}>
          <Num value={changes.portalGracePeriod} min={1} max={14400} onChange={(v) => setField("portalGracePeriod", v)} />
        </Row>
        <Row label="Bypass CNA" summary={summary.bypassCNA}>
          <Tri value={changes.bypassCNA} onChange={(v) => setField("bypassCNA", v)} />
        </Row>
        <Row label="MAC auth bypass" summary={summary.bypassCPUsingMacAddressAuthentication}>
          <Tri value={changes.bypassCPUsingMacAddressAuthentication}
            onChange={(v) => setField("bypassCPUsingMacAddressAuthentication", v)} />
        </Row>
      </div>
    </div>
  );
}
