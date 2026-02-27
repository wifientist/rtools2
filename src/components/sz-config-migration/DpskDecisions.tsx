import type { TypeMapping } from '@/types/szConfigMigration';

interface Props {
  typeMappings: Record<string, TypeMapping>;
}

export default function DpskDecisions({ typeMappings }: Props) {
  // Filter to DPSK WLANs only
  const dpskEntries = Object.entries(typeMappings).filter(
    ([, m]) => m.r1_network_type === 'dpsk'
  );

  if (dpskEntries.length === 0) {
    return (
      <div className="text-sm text-gray-500 italic">
        No DPSK WLANs in this migration. This section can be skipped.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-gray-600">
        DPSK WLANs will be created with R1 DPSK pools and identity groups.
        The workflow will automatically create a DPSK pool and link it to the network.
      </p>

      <div className="space-y-2">
        {dpskEntries.map(([wlanId, mapping]) => (
          <div
            key={wlanId}
            className="border border-orange-200 bg-orange-50 rounded-lg p-3"
          >
            <div className="flex items-center justify-between">
              <div>
                <span className="font-semibold text-sm">{mapping.wlan_name}</span>
                <span className="text-xs text-gray-500 ml-2">({mapping.sz_auth_type})</span>
              </div>
              <span className="text-xs bg-orange-200 text-orange-700 px-2 py-0.5 rounded font-medium">
                {mapping.dpsk_type || 'DPSK'}
              </span>
            </div>
            {mapping.notes && (
              <div className="mt-1 text-xs text-gray-600">* {mapping.notes}</div>
            )}
            <div className="mt-2 text-xs text-orange-700">
              Will create: identity group + DPSK pool in R1
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
