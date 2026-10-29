import { Fragment, useState } from "react";
import { ChevronDown, ChevronRight, Trash2, X, Check } from "lucide-react";
import {
  STEPS, STEP_STYLES, TONE_CLASSES, defaultNewDate, fmtShort, isOverdue, isReconfigOverdue,
  nextWorkday, parseISO, previewMissConfig, siteStatus, toISO, type EditableField, type TrackerSite,
} from "./trackerModel";

export interface FailBody {
  failed_on: string | null;
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

export interface EventPatch {
  reconfig_on?: string | null;
  reconfig_done?: boolean;
}

interface Props {
  sites: TrackerSite[];
  canEdit: boolean;
  today: string;
  highlightId: number | null;
  onPatch: (id: number, patch: Partial<TrackerSite>) => Promise<void>;
  onFailValidation: (id: number, body: FailBody) => Promise<void>;
  onMissConfig: (id: number, body: MissBody) => Promise<void>;
  onMissMigration: (id: number, body: MissMigrationBody) => Promise<void>;
  onUpdateEvent: (id: number, index: number, patch: EventPatch) => Promise<void>;
  onDeleteEvent: (id: number, index: number) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
}

/**
 * Every site, editable in place for admins. A cell saves when it loses focus
 * (or on Enter); Escape puts it back. Viewers get the same table as text.
 */
export default function TrackerTable({
  sites, canEdit, today, highlightId, onPatch, onFailValidation, onMissConfig, onMissMigration, onUpdateEvent, onDeleteEvent, onDelete,
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

  const th = "px-2 py-2 text-left text-xs font-medium text-gray-500 whitespace-nowrap";

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead className="bg-gray-50 border-b border-gray-200">
          <tr>
            <th className="w-6" />
            <th className={th}>Site</th>
            <th className={th}>Status</th>
            <th className={th}>Current vSZ</th>
            <th className={th}>Tenant</th>
            <th className={`${th} text-right`}>SZ APs</th>
            <th className={`${th} text-right`}>SZ WLANs</th>
            <th className={th}>Category</th>
            {STEPS.map((s) => (
              <th key={s.key} className={th}>
                <span className={STEP_STYLES[s.color].text}>{s.label}</span>
              </th>
            ))}
            <th className={`${th} min-w-[220px]`}>Notes</th>
            {canEdit && <th className="w-8" />}
          </tr>
        </thead>
        <tbody>
          {sites.map((site) => {
            const status = siteStatus(site, today);
            const open = expanded.has(site.id);
            const fails = site.events.filter((e) => e.kind === "validation_failed").length;
            const misses = site.events.filter((e) => e.kind === "config_missed").length;
            const hwMisses = site.events.filter((e) => e.kind === "migration_missed").length;
            return (
              <Fragment key={site.id}>
                <tr
                  id={`tracker-site-${site.id}`}
                  className={`border-b border-gray-100 align-top transition-colors ${
                    highlightId === site.id ? "bg-yellow-50" : "hover:bg-gray-50/60"
                  }`}
                >
                  <td className="pl-2 pt-2.5">
                    <button onClick={() => toggle(site.id)} className="text-gray-400 hover:text-gray-700" title="History">
                      {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                    </button>
                  </td>
                  <Cell site={site} field="site_name" canEdit={canEdit} save={save} className="font-medium min-w-[200px]" />
                  <td className="px-2 py-2">
                    <span className={`px-2 py-0.5 rounded text-xs font-medium whitespace-nowrap ${TONE_CLASSES[status.tone]}`}>
                      {status.label}
                    </span>
                  </td>
                  <Cell site={site} field="vsz" canEdit={canEdit} save={save} className="min-w-[110px]" />
                  <Cell site={site} field="tenant" canEdit={canEdit} save={save} className="min-w-[110px]" />
                  <Cell site={site} field="sz_ap_count" kind="number" canEdit={canEdit} save={save} className="w-20 text-right" />
                  <Cell site={site} field="sz_wlan_count" kind="number" canEdit={canEdit} save={save} className="w-20 text-right" />
                  <Cell site={site} field="category" canEdit={canEdit} save={save} className="min-w-[100px]" list="tracker-categories" />
                  {STEPS.map((s) => {
                    const done = site[s.doneKey];
                    const overdue = isOverdue(site, s, today);
                    return (
                      <td key={s.key} className="px-2 py-1.5 whitespace-nowrap">
                        <div className="flex items-center gap-1.5">
                          {canEdit ? (
                            <input
                              type="date"
                              key={site[s.key] ?? ""}
                              defaultValue={site[s.key] ?? ""}
                              onChange={(e) => save(site, s.key, e.target.value || null)}
                              className={`border rounded px-1 py-0.5 text-sm ${overdue ? "border-red-400" : "border-gray-300"}`}
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
                          {s.key === "validation" && fails > 0 && (
                            <button
                              onClick={() => toggle(site.id)}
                              className="text-[11px] px-1.5 rounded-full bg-red-100 text-red-700"
                              title="Failed validations — open history"
                            >
                              {fails}✕
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
                    <td colSpan={canEdit ? 12 : 11} className="px-2 py-3">
                      <History
                        site={site}
                        canEdit={canEdit}
                        today={today}
                        onFailValidation={onFailValidation}
                        onMissConfig={onMissConfig}
                        onMissMigration={onMissMigration}
                        onUpdateEvent={onUpdateEvent}
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
  site, canEdit, today, onFailValidation, onMissConfig, onMissMigration, onUpdateEvent, onDeleteEvent,
}: {
  site: TrackerSite;
  canEdit: boolean;
  today: string;
  onFailValidation: Props["onFailValidation"];
  onMissConfig: Props["onMissConfig"];
  onMissMigration: Props["onMissMigration"];
  onUpdateEvent: Props["onUpdateEvent"];
  onDeleteEvent: Props["onDeleteEvent"];
}) {
  return (
    <div className="space-y-3 text-sm max-w-4xl">
      <div>
        <div className="text-xs font-medium text-gray-500 mb-1">Missed days and failed validations</div>
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
                <span className="font-medium">
                  Validation #{site.events.slice(0, i + 1).filter((x) => x.kind === "validation_failed").length} failed {fmtShort(e.date)}
                </span>
                {e.note && <span className="text-gray-700">· {e.note}</span>}
                {e.reconfig_on !== undefined ? (
                  <span className="flex items-center gap-1.5 text-gray-600">
                    → re-config + re-validate
                    {canEdit ? (
                      <input
                        type="date"
                        key={e.reconfig_on ?? ""}
                        defaultValue={e.reconfig_on ?? ""}
                        onChange={(ev) => void onUpdateEvent(site.id, i, { reconfig_on: ev.target.value || null })}
                        className={`border rounded px-1 py-0.5 text-sm ${isReconfigOverdue(e, today) ? "border-red-400" : "border-gray-300"}`}
                      />
                    ) : (
                      <span className={isReconfigOverdue(e, today) ? "text-red-700 font-medium" : ""}>{fmtShort(e.reconfig_on ?? null)}</span>
                    )}
                    <button
                      disabled={!canEdit}
                      onClick={() => void onUpdateEvent(site.id, i, { reconfig_done: !e.reconfig_done })}
                      title={e.reconfig_done ? "Re-config done — click to undo" : "Mark re-config done"}
                      className={`inline-flex items-center justify-center w-5 h-5 rounded border ${
                        e.reconfig_done ? STEP_STYLES.sky.done : "border-gray-300 text-transparent hover:border-gray-500"
                      } ${!canEdit && !e.reconfig_done ? "invisible" : ""} disabled:cursor-default`}
                    >
                      <Check size={12} strokeWidth={3} />
                    </button>
                  </span>
                ) : (
                  e.slip_days ? <span className="text-gray-500">— pushed validation + HW +{e.slip_days}d</span> : null
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

      {canEdit && <RecordForm key={site.events.length} site={site} today={today} onFailValidation={onFailValidation}
        onMissConfig={onMissConfig} onMissMigration={onMissMigration} />}

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
  site, today, onFailValidation, onMissConfig, onMissMigration,
}: {
  site: TrackerSite;
  today: string;
  onFailValidation: Props["onFailValidation"];
  onMissConfig: Props["onMissConfig"];
  onMissMigration: Props["onMissMigration"];
}) {
  const [mode, setMode] = useState<"config" | "failed" | "migration">(
    !site.config_pushed_done ? "config" : !site.validation_done ? "failed" : "migration");
  const [failedOn, setFailedOn] = useState(site.validation ?? today);
  const [missedOn, setMissedOn] = useState(site.config_pushed ?? today);
  const [newConfig, setNewConfig] = useState<string | null>(null); // null = follow the default
  const [pushLater, setPushLater] = useState(true);
  const [hwMissedOn, setHwMissedOn] = useState(site.hw_migrated ?? today);
  const [newHw, setNewHw] = useState<string | null>(null); // null = follow the default
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  // Same rules the API applies, shown before they're committed.
  const retryOn = failedOn ? toISO(nextWorkday(parseISO(failedOn))) : null;
  const hwFloor = retryOn ? toISO(nextWorkday(parseISO(retryOn))) : null;
  const hwAfterFail = site.hw_migrated && hwFloor && site.hw_migrated < hwFloor ? hwFloor : site.hw_migrated;
  const effectiveNewConfig = newConfig ?? (missedOn ? defaultNewDate(missedOn) : "");
  const missValid = !!missedOn && !!effectiveNewConfig && effectiveNewConfig > missedOn;
  const afterMiss = missValid ? previewMissConfig(site, missedOn, effectiveNewConfig, pushLater) : null;
  const effectiveNewHw = newHw ?? (hwMissedOn ? defaultNewDate(hwMissedOn) : "");
  const hwMissValid = !!hwMissedOn && !!effectiveNewHw && effectiveNewHw > hwMissedOn;

  const submit = async () => {
    setBusy(true);
    try {
      if (mode === "failed") {
        await onFailValidation(site.id, { failed_on: failedOn || null, note: note.trim() || null });
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
  const dateInput = "block border border-gray-300 rounded px-1 py-0.5 text-sm";
  const MODES = [
    { key: "config", label: "Missed config" },
    { key: "failed", label: "Failed validation" },
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

      {mode === "failed" ? (
        <label className="text-xs text-gray-600">
          Failed on
          <input type="date" value={failedOn} onChange={(e) => setFailedOn(e.target.value)} className={dateInput} />
        </label>
      ) : mode === "config" ? (
        <>
          <label className="text-xs text-gray-600">
            Config was due
            <input type="date" value={missedOn} onChange={(e) => setMissedOn(e.target.value)} className={dateInput} />
          </label>
          <label className="text-xs text-gray-600">
            New config date
            <input type="date" value={effectiveNewConfig} min={missedOn || undefined}
              onChange={(e) => setNewConfig(e.target.value || null)} className={dateInput} />
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
            <input type="date" value={hwMissedOn} onChange={(e) => setHwMissedOn(e.target.value)} className={dateInput} />
          </label>
          <label className="text-xs text-gray-600">
            New migration date
            <input type="date" value={effectiveNewHw} min={hwMissedOn || undefined}
              onChange={(e) => setNewHw(e.target.value || null)} className={dateInput} />
          </label>
        </>
      )}
      <label className="text-xs text-gray-600 flex-1 min-w-[200px]">
        {mode === "failed" ? "What failed" : "Why"}
        <input value={note} onChange={(e) => setNote(e.target.value)}
          placeholder={mode === "failed" ? "e.g. DPSK SSID not broadcasting in units 3xx"
            : mode === "config" ? "e.g. change window cancelled" : "e.g. no tech on site"}
          className="block w-full border border-gray-300 rounded px-1.5 py-0.5 text-sm" />
      </label>
      <button
        onClick={submit}
        disabled={busy || (mode === "failed" ? !failedOn : mode === "config" ? !missValid : !hwMissValid)}
        className="px-3 py-1 rounded bg-red-600 text-white text-sm hover:bg-red-700 disabled:opacity-50"
      >
        {mode === "failed" ? "Record failure" : "Record miss"}
      </button>

      <div className="w-full text-xs text-gray-500">
        {mode === "failed" ? (
          retryOn && <>Re-config and re-validation {fmtShort(retryOn)}
            {site.hw_migrated ? ` · ${moved("HW migrated", site.hw_migrated, hwAfterFail)}` : ""}</>
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
