import { Shield, AlertTriangle } from 'lucide-react';
import type { TypeMapping } from '@/types/szConfigMigration';

interface Props {
  typeMappings: Record<string, TypeMapping>;
}

export default function RadiusMapping({ typeMappings }: Props) {
  // Filter to AAA WLANs only
  const aaaEntries = Object.entries(typeMappings).filter(
    ([, m]) => m.r1_network_type === 'aaa'
  );

  if (aaaEntries.length === 0) {
    return (
      <div className="text-sm text-gray-500 italic">
        No Enterprise (AAA) WLANs in this migration. This section can be skipped.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 flex items-start gap-2">
        <AlertTriangle size={16} className="text-amber-500 mt-0.5 flex-shrink-0" />
        <div className="text-xs text-amber-700">
          <strong>Note:</strong> SmartZone does not expose RADIUS shared secrets via API.
          The workflow will create RADIUS profiles with a placeholder secret
          (<code className="bg-amber-100 px-1 rounded">CHANGE_ME_after_migration</code>).
          You must manually update the shared secret in R1 after migration.
        </div>
      </div>

      <div className="space-y-2">
        {aaaEntries.map(([wlanId, mapping]) => (
          <div
            key={wlanId}
            className="border border-purple-200 bg-purple-50 rounded-lg p-3"
          >
            <div className="flex items-center gap-2">
              <Shield size={14} className="text-purple-600" />
              <span className="font-semibold text-sm">{mapping.wlan_name}</span>
              <span className="text-xs text-gray-500">({mapping.sz_auth_type})</span>
            </div>
            {mapping.notes && (
              <div className="mt-1 text-xs text-gray-600">* {mapping.notes}</div>
            )}
            <div className="mt-2 text-xs text-purple-700">
              RADIUS profile will be found-or-created in R1 (idempotent)
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
