import { useState } from 'react';
import { ChevronDown, ChevronRight, Info } from 'lucide-react';
import type { VenueSettingsPlan } from '@/types/szConfigMigration';

interface Props {
  plan: VenueSettingsPlan;
  // Section keys to carry; null = all of them (the default -- a migration
  // takes the config from SZ).
  selected: string[] | null;
  onChange: (selected: string[] | null) => void;
  venueCreated: boolean;
  disabled?: boolean;
}

/** Which zone settings the migration job applies to the venue (first phase). */
export default function VenueSettingsPanel({ plan, selected, onChange, venueCreated, disabled }: Props) {
  const [open, setOpen] = useState<Set<string>>(() => new Set(['radio']));
  const all = plan.sections.map(s => s.key);
  const chosen = new Set(selected ?? all);

  const toggle = (key: string) => {
    const next = new Set(chosen);
    next.has(key) ? next.delete(key) : next.add(key);
    // Back to null when everything is ticked, so new sections stay included.
    onChange(next.size === all.length ? null : all.filter(k => next.has(k)));
  };
  const toggleOpen = (key: string) => {
    const next = new Set(open);
    next.has(key) ? next.delete(key) : next.add(key);
    setOpen(next);
  };

  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-600">
        The migration job applies these zone settings to the venue first, before any network. Anything the zone
        doesn't set keeps R1's value. Untick a section to leave R1's setting as it is.
      </p>
      {!venueCreated && (
        <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
          This is an existing venue: each ticked section overwrites its current settings with the zone's.
        </div>
      )}

      <div className="flex gap-3 text-xs">
        <button disabled={disabled} onClick={() => onChange(null)} className="text-indigo-600 hover:underline disabled:text-gray-400">
          Tick all
        </button>
        <button disabled={disabled} onClick={() => onChange([])} className="text-gray-500 hover:underline disabled:text-gray-400">
          Untick all
        </button>
        <span className="text-gray-400">{chosen.size} of {all.length} sections</span>
      </div>

      <div className="border border-gray-200 rounded divide-y divide-gray-100">
        {plan.sections.map(s => {
          const isOpen = open.has(s.key);
          return (
            <div key={s.key}>
              <div className="flex items-center gap-2 px-3 py-2">
                <input type="checkbox" checked={chosen.has(s.key)} disabled={disabled} onChange={() => toggle(s.key)} />
                <button onClick={() => toggleOpen(s.key)}
                        className="flex items-center gap-1 text-sm font-medium text-gray-800 hover:text-indigo-700">
                  {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  {s.label}
                </button>
                <span className="text-[11px] text-gray-400">{s.rows.length} settings</span>
                {s.notes.length > 0 && <span className="text-[11px] text-amber-600">{s.notes.length} notes</span>}
              </div>
              {isOpen && (
                <div className="px-9 pb-3">
                  <table className="w-full text-xs">
                    <tbody>
                      {s.rows.map(row => (
                        <tr key={row.label} className="border-t border-gray-50">
                          <td className="py-1 pr-3 text-gray-500 w-1/2">{row.label}</td>
                          <td className="py-1 font-mono text-gray-800 break-all">{String(row.value)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {s.notes.map(n => (
                    <div key={n} className="mt-1 flex items-start gap-1 text-[11px] text-amber-700">
                      <Info size={11} className="mt-0.5 flex-shrink-0" />{n}
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {plan.not_carried.length > 0 && (
        <details className="text-xs text-gray-600">
          <summary className="cursor-pointer text-gray-500">{plan.not_carried.length} zone settings not carried</summary>
          <ul className="mt-1 ml-4 list-disc space-y-0.5">
            {plan.not_carried.map(n => <li key={n}>{n}</li>)}
          </ul>
        </details>
      )}
    </div>
  );
}
