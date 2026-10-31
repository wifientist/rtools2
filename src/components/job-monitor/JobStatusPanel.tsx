import { useState } from 'react';
import { pluralize, resourceLabel, resultCounts } from './format';

export interface WarningGroup {
  label: string;
  count: number;
  samples: string[];
}

// The same warning about different objects is one line here: names, ids and
// numbers come out, so 40 "AP Group 'x' not created" collapse to a count.
export function warningKey(message: string): string {
  return message
    .replace(/^\[[^\]]*\]\s*/, '')
    .replace(/\(id=[^)]*\)/g, '')
    .replace(/'[^']*'/g, "'…'")
    .replace(/\d+/g, '#')
    .slice(0, 120)
    .trim();
}

interface ProgressRow {
  item_name: string;
  current: number;
  total: number;
  failed: number;
}

interface StatusPhase {
  id: string;
  name: string;
  status: string;
  result?: Record<string, any>;
  progress?: ProgressRow[];
}

interface PhaseCounts {
  completed?: number;
  failed?: number;
  total?: number;
  effect?: number;
  effect_label?: string;
}

interface JobStatusPanelProps {
  phases: StatusPhase[];
  phaseStats?: Record<string, PhaseCounts>;
  createdResources: Record<string, any[]>;
  warnings: Record<string, WarningGroup>;
}

function StatusRow({ name, done, total, text, failed, state }: {
  name: string; done?: number; total?: number; text: string; failed?: number; state: string;
}) {
  const hasBar = total !== undefined && total > 0 && done !== undefined;
  const bar = failed ? 'bg-red-500' : state === 'COMPLETED' ? 'bg-green-500' : 'bg-blue-500';
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="font-medium text-gray-800 truncate">{name}</span>
        <span className={`text-xs whitespace-nowrap ${failed ? 'text-red-700' : 'text-gray-600'}`}>{text}</span>
      </div>
      {hasBar && (
        <div className="mt-1 w-full bg-gray-100 rounded-full h-1.5 overflow-hidden">
          <div className={`h-full ${bar} transition-all duration-300`} style={{ width: `${(done! / total!) * 100}%` }} />
        </div>
      )}
    </div>
  );
}

const JobStatusPanel = ({ phases, phaseStats, createdResources, warnings }: JobStatusPanelProps) => {
  const [openWarning, setOpenWarning] = useState<string | null>(null);

  const rows = phases.flatMap((phase) => {
    if (phase.progress?.length) {
      return phase.progress.map((row) => {
        const failed = row.failed ? `, ${row.failed.toLocaleString()} failed` : '';
        return (
          <StatusRow
            key={`${phase.id}:${row.item_name}`}
            name={phase.name}
            done={row.current}
            total={row.total}
            failed={row.failed}
            state={phase.status}
            text={`${row.current.toLocaleString()}/${row.total.toLocaleString()} ${pluralize(row.item_name)}${failed}`}
          />
        );
      });
    }

    const stat = phaseStats?.[phase.id];
    if (stat?.total !== undefined && (phase.status !== 'PENDING' || (stat.completed ?? 0) > 0)) {
      const done = (stat.completed ?? 0) + (stat.failed ?? 0);
      const failed = stat.failed ? `, ${stat.failed.toLocaleString()} failed` : '';
      const effect = stat.effect && stat.effect_label ? ` · ${stat.effect.toLocaleString()} ${stat.effect_label}` : '';
      return [(
        <StatusRow
          key={phase.id}
          name={phase.name}
          done={done}
          total={stat.total}
          failed={stat.failed}
          state={phase.status}
          text={`${(stat.completed ?? 0).toLocaleString()}/${stat.total.toLocaleString()} units${failed}${effect}`}
        />
      )];
    }

    if (phase.status === 'PENDING') return [];
    const counts = resultCounts(phase.result).map(([label, n]) => `${n.toLocaleString()} ${label}`).join(', ');
    const fallback: Record<string, string> = {
      RUNNING: 'Running', COMPLETED: 'Done', FAILED: 'Failed', SKIPPED: 'Skipped',
    };
    return [(
      <StatusRow
        key={phase.id}
        name={phase.name}
        state={phase.status}
        failed={phase.status === 'FAILED' ? 1 : 0}
        text={counts || fallback[phase.status] || phase.status}
      />
    )];
  });

  const waiting = phases.filter((p) => p.status === 'PENDING' && !p.progress?.length).length;
  const warningEntries = Object.entries(warnings);
  const created = Object.entries(createdResources).filter(([, list]) => list.length > 0);

  return (
    <div className="space-y-3">
      {rows.length === 0 ? (
        <p className="text-sm text-gray-500">Waiting for the first phase...</p>
      ) : rows}
      {waiting > 0 && rows.length > 0 && (
        <p className="text-xs text-gray-400">{waiting} more phase{waiting === 1 ? '' : 's'} waiting</p>
      )}

      {warningEntries.length > 0 && (
        <div className="pt-3 border-t border-gray-200 space-y-1">
          {warningEntries.map(([key, group]) => (
            <div key={key} className="text-xs">
              <button
                onClick={() => setOpenWarning(openWarning === key ? null : key)}
                className="w-full text-left flex items-start justify-between gap-2 text-amber-800 hover:text-amber-950"
              >
                <span>⚠ {group.label}</span>
                <span className="font-medium whitespace-nowrap">× {group.count}</span>
              </button>
              {openWarning === key && (
                <div className="mt-1 ml-3 space-y-1 text-gray-600">
                  {group.samples.map((sample, idx) => <p key={idx}>{sample}</p>)}
                  {group.count > group.samples.length && (
                    <p className="text-gray-400">First {group.samples.length} of {group.count}; the rest are under Raw messages.</p>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {created.length > 0 && (
        <div className="pt-3 border-t border-gray-200">
          <p className="text-xs font-medium text-gray-500 mb-1">Created</p>
          <div className="space-y-0.5">
            {created.map(([type, list]) => (
              <div key={type} className="flex justify-between text-xs text-gray-700">
                <span>{resourceLabel(type)}</span>
                <span className="font-medium">{list.length.toLocaleString()}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export default JobStatusPanel;
