import { AlertCircle, AlertTriangle } from 'lucide-react';
import type { PlanResult } from '@/types/szConfigMigration';

// Where on Review each kind of plan problem is fixed.
const FIX_IN: Record<string, string> = {
  radius_profile: 'RADIUS Servers',
  dpsk_pool: 'DPSK Services',
  dpsk_service: 'DPSK Services',
  identity_group: 'DPSK Services',
  ap_group: 'WLAN Placement',
  wifi_network_activation: 'WLAN Placement',
  wifi_network: 'Authentication / RADIUS Servers',
  wifi_network_setting: 'Network Settings',
  venue_settings: 'Venue Settings',
};

export function fixSection(resourceType: string): string | undefined {
  return FIX_IN[resourceType] ?? (resourceType.startsWith('dpsk') ? 'DPSK Services' : undefined);
}

/** What the plan will create and reuse, and every conflict it found. */
export default function PlanDetails({ plan }: { plan: PlanResult }) {
  const create = plan.actions.filter(a => a.action === 'create');
  const reuse = plan.actions.filter(a => a.action === 'reuse' || a.action === 'exists');
  const update = plan.actions.filter(a => a.action === 'update');
  const errors = plan.conflicts.filter(c => c.severity === 'error');
  const warnings = plan.conflicts.filter(c => c.severity !== 'error');

  return (
    <div className="space-y-3">
      {(create.length > 0 || reuse.length > 0) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {create.length > 0 && (
            <ActionList title={`Will Create (${create.length})`} tone="blue" sign="+" items={create} />
          )}
          {reuse.length > 0 && (
            <ActionList title={`Will Reuse (${reuse.length})`} tone="green" sign="=" items={reuse} />
          )}
        </div>
      )}

      {update.length > 0 && (
        <ActionList title={`Will Update (${update.length}) — venue settings from the zone`} tone="indigo" sign="~" items={update} />
      )}

      {errors.length > 0 && (
        <ConflictList title={`Errors (${errors.length})`} tone="red" items={errors} />
      )}
      {warnings.length > 0 && (
        <ConflictList title={`Warnings (${warnings.length}) — read before confirming`} tone="amber" items={warnings} />
      )}
    </div>
  );
}

function ActionList({ title, tone, sign, items }: {
  title: string; tone: 'blue' | 'green' | 'indigo'; sign: string; items: PlanResult['actions'];
}) {
  const c = {
    blue: { box: 'bg-blue-50 border-blue-200', h: 'text-blue-800', li: 'text-blue-700', sub: 'text-blue-400' },
    green: { box: 'bg-green-50 border-green-200', h: 'text-green-800', li: 'text-green-700', sub: 'text-green-400' },
    indigo: { box: 'bg-indigo-50 border-indigo-200', h: 'text-indigo-800', li: 'text-indigo-700', sub: 'text-indigo-400' },
  }[tone];
  return (
    <div className={`border rounded-lg p-3 ${c.box}`}>
      <h4 className={`text-xs font-semibold mb-1 ${c.h}`}>{title}</h4>
      <ul className="space-y-1 max-h-40 overflow-y-auto">
        {items.map((a, i) => (
          <li key={i} className={`text-xs ${c.li}`}>
            {sign} {a.resource_name || a.name} <span className={c.sub}>({a.resource_type})</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ConflictList({ title, tone, items }: {
  title: string; tone: 'red' | 'amber'; items: PlanResult['conflicts'];
}) {
  const c = tone === 'red'
    ? { box: 'bg-red-50 border-red-200', h: 'text-red-800', t: 'text-red-700' }
    : { box: 'bg-amber-50 border-amber-200', h: 'text-amber-800', t: 'text-amber-700' };
  const Icon = tone === 'red' ? AlertCircle : AlertTriangle;
  return (
    <div className={`border rounded-lg p-3 ${c.box}`}>
      <h4 className={`text-xs font-semibold mb-1 ${c.h}`}>{title}</h4>
      <div className="space-y-0.5 max-h-56 overflow-y-auto">
        {items.map((x, i) => (
          <div key={i} className={`text-xs flex items-start gap-1 ${c.t}`}>
            <Icon size={12} className="mt-0.5 flex-shrink-0" />
            <span>{x.description}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
