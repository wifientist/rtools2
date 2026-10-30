import { useRef } from "react";
import { CalendarDays, ChevronDown, ChevronUp } from "lucide-react";
import { parseLooseDate, stepWorkdayISO } from "./trackerModel";

/** "2026-10-28" -> "10/28/26". */
export function fmtInputDate(iso: string | null): string {
  if (!iso) return "";
  const [y, m, d] = iso.split("-");
  return `${m}/${d}/${y.slice(2)}`;
}

/**
 * A date field that shows MM/DD/YY, which a native date input can't do.
 * Typing takes anything the paste parser does (10/28, 10/28/26, Monday | 26 OCT)
 * and commits on blur or Enter; Escape or an unreadable entry puts the old
 * value back. The arrows step a workday at a time; the calendar button opens
 * the browser's own picker.
 */
export default function DateInput({
  value, onChange, min, className = "",
}: {
  value: string | null;
  onChange: (iso: string | null) => void;
  min?: string;
  className?: string;
}) {
  const text = useRef<HTMLInputElement>(null);
  const picker = useRef<HTMLInputElement>(null);

  const commit = (raw: string) => {
    const el = text.current!;
    if (!raw.trim()) {
      if (value !== null) onChange(null);
      return;
    }
    const iso = parseLooseDate(raw);
    if (!iso) {
      el.value = fmtInputDate(value);
      return;
    }
    el.value = fmtInputDate(iso);
    if (iso !== value) onChange(iso);
  };

  return (
    <span className={`relative inline-flex items-center border rounded bg-white ${className}`}>
      <input
        ref={text}
        key={value ?? ""}
        defaultValue={fmtInputDate(value)}
        onBlur={(e) => commit(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.currentTarget.value = fmtInputDate(value);
            e.currentTarget.blur();
          } else if (e.key === "Enter") {
            e.preventDefault();
            e.currentTarget.blur();
          }
        }}
        className="w-[72px] px-1 py-0.5 text-sm bg-transparent outline-none"
      />
      <span className="flex flex-col border-l border-gray-200">
        {([[1, ChevronUp, "Next workday"], [-1, ChevronDown, "Previous workday"]] as const).map(([dir, Icon, title]) => (
          <button
            key={dir}
            type="button"
            tabIndex={-1}
            onClick={() => {
              const next = stepWorkdayISO(value, dir);
              if (!min || next >= min) onChange(next);
            }}
            className="px-0.5 leading-none text-gray-400 hover:text-gray-800 hover:bg-gray-100"
            title={title}
          >
            <Icon size={11} strokeWidth={2.5} />
          </button>
        ))}
      </span>
      <button
        type="button"
        tabIndex={-1}
        onClick={() => picker.current?.showPicker()}
        className="px-1 text-gray-400 hover:text-gray-700"
        title="Pick a date"
      >
        <CalendarDays size={14} />
      </button>
      {/* Invisible native input, only here for its picker. */}
      <input
        ref={picker}
        type="date"
        tabIndex={-1}
        aria-hidden
        value={value ?? ""}
        min={min}
        onChange={(e) => onChange(e.target.value || null)}
        className="absolute right-0 bottom-0 w-0 h-0 opacity-0 pointer-events-none"
      />
    </span>
  );
}
