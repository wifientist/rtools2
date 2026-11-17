import { Fragment, useRef, useState } from "react";
import { ArrowRight, ChevronDown, ChevronRight, Trash2, X, Check } from "lucide-react";
import {
  STEPS, STEP_STYLES, TONE_CLASSES, defaultNewDate, fmtShort, isOverdue,
  nextWorkday, parseISO, previewMissConfig, previewMissValidation, siteStatus, toISO, type ControllerOption, type EditableField,
  type TrackerSite,
} from "./trackerModel";
import DateInput from "./DateInput";

export interface MissValidationBody {
  missed_on: string;
  new_validation: string;
  push_later: boolean;
  note: string | null;
}

export interface MissBody {
  missed_on: string;
  new_config: string;
  push_later: boolean;
  note: string | null;
}

export interface MissMigrationBody {
  missed_on: string;
  new_hw: string;
  note: string | null;
}

interface Props {
  sites: TrackerSite[];
  canEdit: boolean;
  today: string;
  highlightId: number | null;
  /** Controllers a tenant can be picked from (the editor's own non-MSP R1 controllers). */
  tenants: ControllerOption[];
  /** Controllers a vSZ can be picked from (the editor's own SmartZone controllers). */
  vszs: ControllerOption[];
  /** Show the Tenant ID and Venue ID columns. */
  showIds: boolean;
  onPatch: (id: number, patch: Partial<TrackerSite>) => Promise<void>;
  onMissConfig: (id: number, body: MissBody) => Promise<void>;
  onMissValidation: (id: number, body: MissValidationBody) => Promise<void>;
  onMissMigration: (id: number, body: MissMigrationBody) => Promise<void>;
  onDeleteEvent: (id: number, index: number) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
}

/**
 * Every site, editable in place for admins. A cell saves when it loses focus
 * (or on Enter); Escape puts it back. Viewers get the same table as text.
 */
export default function TrackerTable({
  sites, canEdit, today, highlightId, tenants, vszs, showIds, onPatch, onMissConfig, onMissValidation, onMissMigration, onDeleteEvent, onDelete,
}: Props) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const categories = [...new Set(sites.map((s) => s.category).filter((c): c is string => !!c))].sort();

  const toggle = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const save = (site: TrackerSite, field: EditableField, value: string | number | boolean | null) => {
    if (site[field] === value) return;
    void onPatch(site.id, { [field]: value });
  };

  const th = "relative px-2 py-2 text-left text-xs font-medium text-gray-500 whitespace-nowrap overflow-hidden text-ellipsis";

  // Columns in order. `width` is the starting width once someone resizes;
  // `fixed` columns (the chevron and delete button) don't resize.
  const columns: { key: string; label?: React.ReactNode; width: number; fixed?: boolean; right?: boolean }[] = [
    { key: "expand", width: 28, fixed: true },
    { key: "site_name", label: "Site", width: 220 },
    { key: "status", label: "Status", width: 110 },
    { key: "vsz", label: "Current vSZ", width: 190 },
    { key: "tenant", label: "Tenant", width: 210 },
    ...(showIds ? [{ key: "r1_tenant_id", label: "Tenant ID", width: 260 }, { key: "venue_id", label: "Venue ID", width: 260 }] : []),
    { key: "sz_ap_count", label: "SZ APs", width: 80, right: true },
    { key: "sz_wlan_count", label: "SZ WLANs", width: 90, right: true },
    { key: "category", label: "Category", width: 120 },
    ...STEPS.map((s) => ({ key: s.key, label: <span className={STEP_STYLES[s.color].text}>{s.label}</span>, width: 230 })),
    { key: "notes", label: "Notes", width: 240 },
    ...(canEdit ? [{ key: "delete", width: 32, fixed: true }] : []),
  ];
  const { widths, startResize, resetWidths } = useColumnWidths();
  // The chevron and Site columns stay put when the table scrolls sideways.
  // Sticky cells take the row's (opaque) background so nothing shows through.
  const stickyExpand = "sticky left-0 z-10 bg-inherit";
  const stickySite = "sticky left-[28px] z-10 bg-inherit shadow-[inset_-1px_0_0_#e5e7eb]";
  const thRefs = useRef<Record<string, HTMLTableCellElement | null>>({});
  const widthOf = (c: (typeof columns)[number]) => (c.fixed ? c.width : widths?.[c.key] ?? c.width);

  return (
    <div className="overflow-x-auto">
      <table
        className={`text-sm ${widths ? "table-fixed [&_td]:overflow-hidden [&_td]:!min-w-0" : "min-w-full"}`}
        style={widths ? { width: columns.reduce((n, c) => n + widthOf(c), 0) } : undefined}
      >
        {widths && (
          <colgroup>
            {columns.map((c) => <col key={c.key} style={{ width: widthOf(c) }} />)}
          </colgroup>
        )}
        <thead className="bg-gray-50 border-b border-gray-200">
          <tr className="bg-gray-50">
            {columns.map((c) => (
              <th
                key={c.key}
                ref={(el) => { thRefs.current[c.key] = el; }}
                style={c.key === "expand" ? { width: 28, minWidth: 28, maxWidth: 28 } : undefined}
                className={`${c.fixed ? "w-6" : `${th} ${c.right ? "text-right" : ""} ${c.key === "notes" && !widths ? "min-w-[220px]" : ""}`} ${
                  c.key === "expand" ? stickyExpand : c.key === "site_name" ? stickySite : ""
                }`}
              >
                {c.label}
                {!c.fixed && (
                  <span
                    onMouseDown={(e) => startResize(e, c.key, columns.filter((x) => !x.fixed).map((x) => x.key), thRefs.current)}
                    onDoubleClick={resetWidths}
                    title="Drag to resize · double-click to reset all widths"
                    className="absolute top-0 right-0 h-full w-1.5 cursor-col-resize hover:bg-blue-300/60"
                  />
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sites.map((site) => {
            const status = siteStatus(site, today);
            const open = expanded.has(site.id);
            const vMisses = site.events.filter((e) => e.kind === "validation_missed").length;
            const misses = site.events.filter((e) => e.kind === "config_missed").length;
            const hwMisses = site.events.filter((e) => e.kind === "migration_missed").length;
            return (
              <Fragment key={site.id}>
                <tr
                  id={`tracker-site-${site.id}`}
                  className={`border-b border-gray-100 align-top transition-colors ${
                    highlightId === site.id ? "bg-yellow-50" : "bg-white hover:bg-gray-50"
                  }`}
                >
                  <td className={`pl-2 pt-2.5 ${stickyExpand}`} style={{ width: 28, minWidth: 28, maxWidth: 28 }}>
                    <button onClick={() => toggle(site.id)} className="text-gray-400 hover:text-gray-700" title="History">
                      {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                    </button>
                  </td>
                  <Cell site={site} field="site_name" canEdit={canEdit} save={save} className={`font-medium min-w-[200px] ${stickySite}`} />
                  <td className="px-2 py-2">
                    <span className={`px-2 py-0.5 rounded text-xs font-medium whitespace-nowrap ${TONE_CLASSES[status.tone]}`}>
                      {status.label}
                    </span>
                  </td>
                  <ControllerCell
                    site={site} canEdit={canEdit} options={vszs} onPatch={onPatch}
                    nameField="vsz" idField="sz_host" what="vSZ"
                  />
                  <ControllerCell
                    site={site} canEdit={canEdit} options={tenants} onPatch={onPatch}
                    nameField="tenant" idField="r1_tenant_id" what="tenant"
                  />
                  {showIds && (
                    <td className="px-2 py-2 font-mono text-xs text-gray-600 whitespace-nowrap">
                      {site.r1_tenant_id ?? <span className="text-gray-300">—</span>}
                    </td>
                  )}
                  {showIds && (
                    <Cell site={site} field="venue_id" canEdit={canEdit} save={save} className="min-w-[280px] font-mono text-xs" />
                  )}
                  <Cell site={site} field="sz_ap_count" kind="number" canEdit={canEdit} save={save} className="w-20 text-right" />
                  <Cell site={site} field="sz_wlan_count" kind="number" canEdit={canEdit} save={save} className="w-20 text-right" />
                  <Cell site={site} field="category" canEdit={canEdit} save={save} className="min-w-[100px]" list="tracker-categories" />
                  {STEPS.map((s, si) => {
                    const done = site[s.doneKey];
                    // → copies this step forward: the next step lands one workday later.
                    const nextStep = STEPS[si + 1];
                    const fillTo = nextStep && site[s.key] ? toISO(nextWorkday(parseISO(site[s.key]!))) : null;
                    const overdue = isOverdue(site, s, today);
                    return (
                      <td key={s.key} className="px-2 py-1.5 whitespace-nowrap">
                        <div className="flex items-center gap-1.5">
                          {canEdit ? (
                            <DateInput
                              value={site[s.key]}
                              onChange={(v) => save(site, s.key, v)}
                              className={overdue ? "border-red-400" : "border-gray-300"}
                            />
                          ) : (
                            <span className={overdue ? "text-red-700 font-medium" : ""}>{fmtShort(site[s.key])}</span>
                          )}
                          <button
                            disabled={!canEdit || !site[s.key]}
                            onClick={() => save(site, s.doneKey, !done)}
                            title={done ? "Done — click to mark not done" : "Mark done"}
                            className={`inline-flex items-center justify-center w-5 h-5 rounded border ${
                              done ? STEP_STYLES[s.color].done : "border-gray-300 text-transparent"
                            } ${canEdit && site[s.key] ? "hover:border-gray-500" : "cursor-default"} ${
                              !canEdit && !done ? "invisible" : ""
                            } disabled:opacity-40`}
                          >
                            <Check size={12} strokeWidth={3} />
                          </button>
                          {s.key === "config_pushed" && misses > 0 && (
                            <button
                              onClick={() => toggle(site.id)}
                              className="text-[11px] px-1.5 rounded-full bg-red-100 text-red-700 whitespace-nowrap"
                              title="Missed config days — open history"
                            >
                              {misses} missed
                            </button>
                          )}
                          {s.key === "hw_migrated" && hwMisses > 0 && (
                            <button
                              onClick={() => toggle(site.id)}
                              className="text-[11px] px-1.5 rounded-full bg-red-100 text-red-700 whitespace-nowrap"
                              title="Missed migrations — open history"
                            >
                              {hwMisses} missed
                            </button>
                          )}
                          {s.key === "validation" && vMisses > 0 && (
                            <button
                              onClick={() => toggle(site.id)}
                              className="text-[11px] px-1.5 rounded-full bg-red-100 text-red-700 whitespace-nowrap"
                              title="Missed validations — open history"
                            >
                              {vMisses} missed
                            </button>
                          )}
                          {canEdit && nextStep && (
                            <button
                              disabled={!fillTo || fillTo === site[nextStep.key]}
                              onClick={() => fillTo && save(site, nextStep.key, fillTo)}
                              title={fillTo ? `Set ${nextStep.label.toLowerCase()} to ${fmtShort(fillTo)} (next workday)` : `Set a date first`}
                              className="ml-1 px-1 rounded text-gray-400 hover:text-gray-800 hover:bg-gray-100 disabled:opacity-30 disabled:hover:bg-transparent"
                            >
                              <ArrowRight size={14} />
                            </button>
                          )}
                        </div>
                      </td>
                    );
                  })}
                  <Cell site={site} field="notes" kind="textarea" canEdit={canEdit} save={save} className="min-w-[220px]" />
                  {canEdit && (
                    <td className="px-2 pt-2">
                      <button
                        onClick={() => {
                          if (confirm(`Delete "${site.site_name}" from the tracker?`)) void onDelete(site.id);
                        }}
                        className="text-gray-300 hover:text-red-600"
                        title="Delete site"
                      >
                        <Trash2 size={15} />
                      </button>
                    </td>
                  )}
                </tr>
                {open && (
                  <tr className="border-b border-gray-200 bg-gray-50/70">
                    <td />
                    <td colSpan={columns.length} className="px-2 py-3">
                      <History
                        site={site}
                        canEdit={canEdit}
                        today={today}
                        onMissConfig={onMissConfig}
                        onMissValidation={onMissValidation}
                        onMissMigration={onMissMigration}
                        onDeleteEvent={onDeleteEvent}
                      />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
      <datalist id="tracker-categories">
        {categories.map((c) => <option key={c} value={c} />)}
      </datalist>
    </div>
  );
}

/**
 * A site's vSZ or R1 tenant, picked from the editor's controllers. Picking one
 * stores its name and its id (SZ host / R1 tenant id). Text typed before the
 * link existed, or a controller this editor doesn't have, stays listed as-is.
 */
function ControllerCell({
  site, canEdit, options, onPatch, nameField, idField, what,
}: {
  site: TrackerSite;
  canEdit: boolean;
  options: ControllerOption[];
  onPatch: (id: number, patch: Partial<TrackerSite>) => Promise<void>;
  nameField: "vsz" | "tenant";
  idField: "sz_host" | "r1_tenant_id";
  what: string;
}) {
  const name = site[nameField];
  const id = site[idField];
  const linked = id ? options.find((o) => o.id === id) : undefined;
  if (!canEdit) {
    return (
      <td className="px-2 py-2">
        {name ?? <span className="text-gray-300">—</span>}
        {name && !id && <span className="text-amber-600" title="Not linked to a controller"> *</span>}
      </td>
    );
  }
  // "" = none; "id:<id>" = a controller; "keep" = what's stored but not in the list.
  const value = linked ? `id:${linked.id}` : name || id ? "keep" : "";
  return (
    <td className="px-1 py-1">
      <select
        value={value}
        onChange={(e) => {
          const v = e.target.value;
          if (v === "keep") return;
          const o = options.find((x) => `id:${x.id}` === v);
          void onPatch(site.id, { [nameField]: o?.name ?? null, [idField]: o?.id ?? null });
        }}
        className={`w-full border rounded px-1 py-1 bg-transparent text-sm focus:outline-none focus:border-blue-400 ${
          value === "keep" ? "border-amber-300 text-amber-800" : "border-transparent hover:border-gray-300"
        }`}
        title={value === "keep" ? (id ? `Linked to a ${what} controller you don't have` : `Typed text, not linked to a ${what} controller`) : undefined}
      >
        <option value="">—</option>
        {value === "keep" && (
          <option value="keep">
            {name ?? id} {id ? "(not your controller)" : "(not linked)"}
          </option>
        )}
        {options.map((o) => (
          <option key={o.id} value={`id:${o.id}`}>{o.name}</option>
        ))}
      </select>
    </td>
  );
}

const WIDTHS_KEY = "tracker.table.widths";

/**
 * Column widths the viewer dragged, remembered in this browser. Null until the
 * first drag: the table lays itself out automatically until then. The first
 * drag freezes every column at its current width so only the dragged one moves.
 */
function useColumnWidths() {
  const [widths, setWidths] = useState<Record<string, number> | null>(() => {
    try {
      const raw = localStorage.getItem(WIDTHS_KEY);
      return raw ? (JSON.parse(raw) as Record<string, number>) : null;
    } catch {
      return null;
    }
  });
  const persist = (w: Record<string, number> | null) => {
    try {
      if (w) localStorage.setItem(WIDTHS_KEY, JSON.stringify(w));
      else localStorage.removeItem(WIDTHS_KEY);
    } catch { /* storage unavailable */ }
  };

  const startResize = (
    e: React.MouseEvent, key: string, keys: string[], ths: Record<string, HTMLTableCellElement | null>,
  ) => {
    e.preventDefault();
    e.stopPropagation();
    const base: Record<string, number> = { ...(widths ?? {}) };
    for (const k of keys) if (base[k] == null && ths[k]) base[k] = ths[k]!.offsetWidth;
    const startX = e.clientX;
    const startW = base[key] ?? ths[key]?.offsetWidth ?? 120;
    let latest = base;
    const move = (ev: MouseEvent) => {
      latest = { ...base, [key]: Math.max(40, Math.round(startW + ev.clientX - startX)) };
      setWidths(latest);
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      document.body.style.cursor = "";
      persist(latest);
    };
    document.body.style.cursor = "col-resize";
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    setWidths(base);
  };

  const resetWidths = () => {
    setWidths(null);
    persist(null);
  };

  return { widths, startResize, resetWidths };
}

function Cell({
  site, field, canEdit, save, className = "", kind = "text", list,
}: {
  site: TrackerSite;
  field: EditableField;
  canEdit: boolean;
  save: (site: TrackerSite, field: EditableField, value: string | number | null) => void;
  className?: string;
  kind?: "text" | "number" | "textarea";
  list?: string;
}) {
  const value = site[field] as string | number | null;
  if (!canEdit) {
    return (
      <td className={`px-2 py-2 ${kind === "textarea" ? "whitespace-pre-wrap text-gray-700" : ""} ${className}`}>
        {value ?? <span className="text-gray-300">—</span>}
      </td>
    );
  }

  const commit = (raw: string) => {
    const trimmed = raw.trim();
    if (kind === "number") {
      const n = trimmed === "" ? null : parseInt(trimmed.replace(/,/g, ""), 10);
      save(site, field, Number.isNaN(n) ? value : n);
    } else if (field === "site_name" && !trimmed) {
      return; // name is required; the key reset below restores it
    } else {
      save(site, field, trimmed || null);
    }
  };

  const resetKey = String(value ?? "");
  const shared = {
    defaultValue: value ?? "",
    onBlur: (e: React.FocusEvent<HTMLInputElement | HTMLTextAreaElement>) => commit(e.target.value),
    onKeyDown: (e: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      if (e.key === "Escape") {
        e.currentTarget.value = String(value ?? "");
        e.currentTarget.blur();
      } else if (e.key === "Enter" && !(kind === "textarea" && e.shiftKey)) {
        e.preventDefault();
        e.currentTarget.blur();
      }
    },
    className: `w-full border border-transparent hover:border-gray-300 focus:border-blue-400 focus:outline-none rounded px-1.5 py-1 bg-transparent focus:bg-white ${
      kind === "number" ? "text-right" : ""
    }`,
  };

  return (
    <td className={`px-1 py-1 ${className}`}>
      {kind === "textarea" ? (
        <textarea key={resetKey} {...shared} rows={Math.min(4, Math.max(1, String(value ?? "").split("\n").length))} placeholder="Add a note" />
      ) : (
        <input key={resetKey} {...shared} type="text" inputMode={kind === "number" ? "numeric" : undefined} list={list} />
      )}
    </td>
  );
}

function History({
  site, canEdit, today, onMissConfig, onMissValidation, onMissMigration, onDeleteEvent,
}: {
  site: TrackerSite;
  canEdit: boolean;
  today: string;
  onMissConfig: Props["onMissConfig"];
  onMissValidation: Props["onMissValidation"];
  onMissMigration: Props["onMissMigration"];
  onDeleteEvent: Props["onDeleteEvent"];
}) {
  return (
    <div className="space-y-3 text-sm max-w-4xl">
      <div>
        <div className="text-xs font-medium text-gray-500 mb-1">Missed days</div>
        {site.events.length === 0 ? (
          <div className="text-gray-400">None recorded.</div>
        ) : (
          <ul className="space-y-1.5">
            {site.events.map((e, i) => (
              <li key={i} className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-red-600 text-white shrink-0">
                  <X size={10} strokeWidth={3} />
                </span>
                {e.kind === "migration_missed" ? (
                  <>
                    <span className="font-medium">Migration missed {fmtShort(e.date)}</span>
                    {e.note && <span className="text-gray-700">· {e.note}</span>}
                    <span className="text-gray-600">→ moved to {fmtShort(e.new_hw ?? null)}</span>
                  </>
                ) : e.kind === "config_missed" ? (
                  <>
                    <span className="font-medium">Config missed {fmtShort(e.date)}</span>
                    {e.note && <span className="text-gray-700">· {e.note}</span>}
                    <span className="text-gray-600">
                      → moved to {fmtShort(e.new_config ?? null)}
                      {e.pushed_later ? ", validation + HW moved with it" : ""}
                    </span>
                  </>
                ) : (
                  <>
                    <span className="font-medium">Validation missed {fmtShort(e.date)}</span>
                    {e.note && <span className="text-gray-700">· {e.note}</span>}
                    {e.new_validation && (
                      <span className="text-gray-600">
                        → moved to {fmtShort(e.new_validation)}{e.pushed_later ? ", HW moved with it" : ""}
                      </span>
                    )}
                  </>
                )}
                {canEdit && (
                  <button
                    onClick={() => {
                      if (confirm("Remove this entry? Dates it moved stay where they are.")) void onDeleteEvent(site.id, i);
                    }}
                    className="text-gray-300 hover:text-red-600"
                    title="Remove entry"
                  >
                    <Trash2 size={13} />
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {canEdit && <RecordForm key={site.events.length} site={site} today={today}
        onMissConfig={onMissConfig} onMissValidation={onMissValidation} onMissMigration={onMissMigration} />}

      {site.updated_by && (
        <div className="text-xs text-gray-400">
          Last edited {site.updated_at ? new Date(site.updated_at + "Z").toLocaleString() : ""} by {site.updated_by}
        </div>
      )}
    </div>
  );
}

/** One box for every slip kind; opens on whichever step the site is waiting on. */
function RecordForm({
  site, today, onMissConfig, onMissValidation, onMissMigration,
}: {
  site: TrackerSite;
  today: string;
  onMissConfig: Props["onMissConfig"];
  onMissValidation: Props["onMissValidation"];
  onMissMigration: Props["onMissMigration"];
}) {
  const [mode, setMode] = useState<"config" | "validation" | "migration">(
    !site.config_pushed_done ? "config" : !site.validation_done ? "validation" : "migration");
  const [vMissedOn, setVMissedOn] = useState(site.validation ?? today);
  const [newValidation, setNewValidation] = useState<string | null>(null); // null = follow the default
  const [vPushLater, setVPushLater] = useState(true);
  const [missedOn, setMissedOn] = useState(site.config_pushed ?? today);
  const [newConfig, setNewConfig] = useState<string | null>(null); // null = follow the default
  const [pushLater, setPushLater] = useState(true);
  const [hwMissedOn, setHwMissedOn] = useState(site.hw_migrated ?? today);
  const [newHw, setNewHw] = useState<string | null>(null); // null = follow the default
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  // Same rules the API applies, shown before they're committed.
  const effectiveNewValidation = newValidation ?? (vMissedOn ? defaultNewDate(vMissedOn) : "");
  const vMissValid = !!vMissedOn && !!effectiveNewValidation && effectiveNewValidation > vMissedOn;
  const hwAfterVMiss = vMissValid ? previewMissValidation(site, vMissedOn, effectiveNewValidation, vPushLater) : null;
  const effectiveNewConfig = newConfig ?? (missedOn ? defaultNewDate(missedOn) : "");
  const missValid = !!missedOn && !!effectiveNewConfig && effectiveNewConfig > missedOn;
  const afterMiss = missValid ? previewMissConfig(site, missedOn, effectiveNewConfig, pushLater) : null;
  const effectiveNewHw = newHw ?? (hwMissedOn ? defaultNewDate(hwMissedOn) : "");
  const hwMissValid = !!hwMissedOn && !!effectiveNewHw && effectiveNewHw > hwMissedOn;

  const submit = async () => {
    setBusy(true);
    try {
      if (mode === "validation") {
        await onMissValidation(site.id, {
          missed_on: vMissedOn, new_validation: effectiveNewValidation, push_later: vPushLater, note: note.trim() || null,
        });
        setNewValidation(null);
      } else if (mode === "config") {
        await onMissConfig(site.id, {
          missed_on: missedOn, new_config: effectiveNewConfig, push_later: pushLater, note: note.trim() || null,
        });
        setNewConfig(null);
      } else {
        await onMissMigration(site.id, { missed_on: hwMissedOn, new_hw: effectiveNewHw, note: note.trim() || null });
        setNewHw(null);
      }
      setNote("");
    } finally {
      setBusy(false);
    }
  };

  const moved = (label: string, from: string | null, to: string | null) =>
    from ? (from !== to ? `${label} ${fmtShort(from)} → ${fmtShort(to)}` : `${label} stays ${fmtShort(from)}`) : null;
  const dateInput = "border-gray-300";
  const MODES = [
    { key: "config", label: "Missed config" },
    { key: "validation", label: "Missed validation" },
    { key: "migration", label: "Missed migration" },
  ] as const;

  return (
    <div className="flex flex-wrap items-end gap-2 p-2 rounded border border-red-200 bg-white">
      <div className="w-full flex flex-wrap items-center gap-1 text-xs">
        <span className="font-medium text-red-700 mr-1">Record a</span>
        {MODES.map((m) => (
          <button
            key={m.key}
            onClick={() => setMode(m.key)}
            className={`px-2 py-0.5 rounded-full border ${
              mode === m.key ? "bg-red-600 border-red-600 text-white" : "border-gray-300 text-gray-600 hover:bg-gray-50"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>

      {mode === "validation" ? (
        <>
          <label className="text-xs text-gray-600">
            Validation was due
            <div><DateInput value={vMissedOn || null} onChange={(v) => setVMissedOn(v ?? "")} className={dateInput} /></div>
          </label>
          <label className="text-xs text-gray-600">
            New validation date
            <div><DateInput value={effectiveNewValidation || null} min={vMissedOn || undefined}
              onChange={setNewValidation} className={dateInput} /></div>
          </label>
          <label className="flex items-center gap-1.5 text-xs text-gray-700 pb-1 cursor-pointer">
            <input type="checkbox" checked={vPushLater} onChange={(e) => setVPushLater(e.target.checked)} />
            Also push HW
          </label>
        </>
      ) : mode === "config" ? (
        <>
          <label className="text-xs text-gray-600">
            Config was due
            <div><DateInput value={missedOn || null} onChange={(v) => setMissedOn(v ?? "")} className={dateInput} /></div>
          </label>
          <label className="text-xs text-gray-600">
            New config date
            <div><DateInput value={effectiveNewConfig || null} min={missedOn || undefined}
              onChange={setNewConfig} className={dateInput} /></div>
          </label>
          <label className="flex items-center gap-1.5 text-xs text-gray-700 pb-1 cursor-pointer">
            <input type="checkbox" checked={pushLater} onChange={(e) => setPushLater(e.target.checked)} />
            Also push validation + HW
          </label>
        </>
      ) : (
        <>
          <label className="text-xs text-gray-600">
            Migration was due
            <div><DateInput value={hwMissedOn || null} onChange={(v) => setHwMissedOn(v ?? "")} className={dateInput} /></div>
          </label>
          <label className="text-xs text-gray-600">
            New migration date
            <div><DateInput value={effectiveNewHw || null} min={hwMissedOn || undefined}
              onChange={setNewHw} className={dateInput} /></div>
          </label>
        </>
      )}
      <label className="text-xs text-gray-600 flex-1 min-w-[200px]">
        Why
        <input value={note} onChange={(e) => setNote(e.target.value)}
          placeholder={mode === "validation" ? "e.g. failed: DPSK SSID not broadcasting in units 3xx"
            : mode === "config" ? "e.g. change window cancelled" : "e.g. no tech on site"}
          className="block w-full border border-gray-300 rounded px-1.5 py-0.5 text-sm" />
      </label>
      <button
        onClick={submit}
        disabled={busy || (mode === "validation" ? !vMissValid : mode === "config" ? !missValid : !hwMissValid)}
        className="px-3 py-1 rounded bg-red-600 text-white text-sm hover:bg-red-700 disabled:opacity-50"
      >
        Record miss
      </button>

      <div className="w-full text-xs text-gray-500">
        {mode === "validation" ? (
          vMissValid ? (
            [
              `Validation ${fmtShort(vMissedOn)} → ${fmtShort(effectiveNewValidation)}`,
              moved("HW migrated", site.hw_migrated, hwAfterVMiss),
            ].filter(Boolean).join(" · ")
          ) : (
            <span className="text-red-600">The new validation date has to be after the one that was missed.</span>
          )
        ) : mode === "config" ? (
          afterMiss ? (
            [
              `Config ${fmtShort(missedOn)} → ${fmtShort(effectiveNewConfig)}`,
              moved("Validation", site.validation, afterMiss.validation),
              moved("HW migrated", site.hw_migrated, afterMiss.hw_migrated),
            ].filter(Boolean).join(" · ")
          ) : (
            <span className="text-red-600">The new config date has to be after the one that was missed.</span>
          )
        ) : hwMissValid ? (
          `HW migrated ${fmtShort(hwMissedOn)} → ${fmtShort(effectiveNewHw)} · config and validation stay as they are`
        ) : (
          <span className="text-red-600">The new migration date has to be after the one that was missed.</span>
        )}
      </div>
    </div>
  );
}
