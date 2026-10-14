import { Shield, AlertTriangle, Lock } from 'lucide-react';
import type {
  RadiusChoices, RadiusDecision, RadiusAction, WlanFallback,
} from '@/types/szConfigMigration';

interface Props {
  choices: RadiusChoices;
  decisions: Record<string, RadiusDecision>;
  fallbacks: Record<string, WlanFallback>;
  // Which services have a secret typed. The values themselves stay with the
  // parent and are never rendered back.
  secretSet: Record<string, boolean>;
  onDecision: (serviceId: string, decision: RadiusDecision) => void;
  onFallback: (wlanId: string, fallback: WlanFallback) => void;
  onSecret: (serviceId: string, secret: string) => void;
  disabled?: boolean;
}

const ACTION_LABELS: Record<RadiusAction, string> = {
  create: 'Create new R1 profile from SZ',
  existing: 'Use existing R1 profile',
  skip: "Don't migrate",
};

export default function RadiusMapping({
  choices, decisions, fallbacks, secretSet, onDecision, onFallback, onSecret, disabled,
}: Props) {
  if (choices.services.length === 0) {
    return (
      <div className="text-sm text-gray-500 italic">
        No WLAN in this zone uses a RADIUS server. This section can be skipped.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-600">
        Only the RADIUS services that WLANs <strong>in this zone</strong> use are listed.
        R1 profiles are tenant-wide, so you can link one that already exists instead of
        creating another.
      </p>
      {choices.error && (
        <div className="text-xs text-amber-700 flex items-start gap-1.5">
          <AlertTriangle size={12} className="mt-0.5 flex-shrink-0" /> {choices.error}
        </div>
      )}

      {choices.services.map(svc => {
        const d = decisions[svc.id] ?? { action: svc.suggestion.action, r1_profile_id: svc.suggestion.r1_profile_id };
        const wantType = svc.roles.length === 1 && svc.roles[0] === 'accounting' ? 'ACCOUNTING' : 'AUTHENTICATION';
        const profiles = choices.r1_profiles.filter(p => !p.type || p.type === wantType);
        const authUsers = svc.used_by.filter(u => u.role === 'authentication');
        const wlanNames = Array.from(new Set(svc.used_by.map(u => u.wlan_name)));
        const canCreate = !!svc.primary;

        return (
          <div key={svc.id} className="border border-purple-200 bg-purple-50 rounded-lg p-3 space-y-2">
            <div className="flex items-start gap-2">
              <Shield size={14} className="text-purple-600 mt-0.5 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="font-semibold text-sm">{svc.name}</div>
                <div className="text-xs text-gray-600">
                  {svc.primary ?? 'no server address in snapshot'}
                  {svc.secondary && <span className="text-gray-400"> · secondary {svc.secondary}</span>}
                  <span className="text-gray-400"> · {svc.roles.join(' + ')}</span>
                </div>
                <div className="text-xs text-gray-500 mt-0.5">Used by: {wlanNames.join(', ')}</div>
              </div>
            </div>

            <div className="space-y-1.5 pl-6">
              {(['create', 'existing', 'skip'] as RadiusAction[]).map(action => {
                const unavailable =
                  (action === 'create' && !canCreate) ||
                  (action === 'existing' && profiles.length === 0);
                return (
                  <label key={action} className={`flex items-center gap-2 text-xs ${unavailable ? 'text-gray-400' : 'text-gray-700'}`}>
                    <input
                      type="radio"
                      name={`radius-${svc.id}`}
                      checked={d.action === action}
                      disabled={disabled || unavailable}
                      onChange={() => onDecision(svc.id, {
                        action,
                        r1_profile_id: action === 'existing'
                          ? (d.r1_profile_id ?? svc.suggestion.r1_profile_id ?? profiles[0]?.id ?? null)
                          : null,
                      })}
                    />
                    {ACTION_LABELS[action]}
                    {unavailable && action === 'existing' && <span>(none of this type in R1)</span>}
                    {unavailable && action === 'create' && <span>(no server address)</span>}
                  </label>
                );
              })}
              <div className="text-[11px] text-gray-400 pl-5">Suggested: {svc.suggestion.reason}</div>
            </div>

            {d.action === 'existing' && (
              <div className="pl-6">
                <select
                  className="text-xs border border-gray-300 rounded px-2 py-1 bg-white w-full max-w-md"
                  value={d.r1_profile_id ?? ''}
                  disabled={disabled}
                  onChange={e => onDecision(svc.id, { action: 'existing', r1_profile_id: e.target.value || null })}
                >
                  <option value="">Choose an R1 profile…</option>
                  {profiles.map(p => (
                    <option key={p.id} value={p.id}>
                      {p.name}{p.primary ? ` — ${p.primary}` : ''}{p.primary && p.primary === svc.primary ? '  (same server)' : ''}{p.placeholder_secret ? '  ⚠ placeholder secret' : ''}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {d.action === 'create' && (
              <div className="pl-6 space-y-1">
                <label className="flex items-center gap-2 text-xs text-gray-700 flex-wrap">
                  <Lock size={12} className="text-gray-500" />
                  Shared secret (optional)
                  <input
                    type="password"
                    autoComplete="new-password"
                    spellCheck={false}
                    placeholder={secretSet[svc.id] ? 'Set — type to replace' : 'Leave blank to use a placeholder'}
                    className="border border-gray-300 rounded px-2 py-1 text-xs flex-1 min-w-[10rem] max-w-xs bg-white"
                    disabled={disabled}
                    // Uncontrolled on purpose: the value is never read back
                    // into the page, only handed to the parent as it is typed.
                    defaultValue=""
                    onChange={e => onSecret(svc.id, e.target.value)}
                  />
                </label>
                <div className="text-[11px] text-gray-500 pl-5">
                  {secretSet[svc.id]
                    ? 'Sent to R1 when the profile is created. It is not saved with this migration and is never shown again. Re-enter it if you leave this page before building the plan.'
                    : 'SZ does not expose shared secrets. Blank creates the profile with a placeholder — authentication fails until you set it in R1.'}
                </div>
              </div>
            )}

            {d.action === 'skip' && authUsers.length > 0 && (
              <div className="pl-6 space-y-1.5">
                <div className="text-xs text-gray-700">
                  These WLANs authenticate against it. For each, choose what happens:
                </div>
                {authUsers.map(u => (
                  <div key={u.wlan_id} className="flex items-center gap-3 text-xs flex-wrap">
                    <span className="font-medium w-32 truncate" title={u.wlan_name}>{u.wlan_name}</span>
                    {(['skip', 'dpsk'] as WlanFallback[]).map(fb => (
                      <label key={fb} className="flex items-center gap-1">
                        <input
                          type="radio"
                          name={`fallback-${u.wlan_id}`}
                          checked={fallbacks[u.wlan_id] === fb}
                          disabled={disabled}
                          onChange={() => onFallback(u.wlan_id, fb)}
                        />
                        {fb === 'skip' ? 'Skip this WLAN' : 'Convert to R1 DPSK'}
                      </label>
                    ))}
                    {!fallbacks[u.wlan_id] && <span className="text-red-600">choose one</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
