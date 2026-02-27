import { Shield, Wifi, Lock, Globe, Key } from 'lucide-react';
import type { TypeMapping } from '@/types/szConfigMigration';

interface Props {
  typeMappings: Record<string, TypeMapping>;
}

const TYPE_ICONS: Record<string, React.ReactNode> = {
  psk: <Lock size={14} className="text-blue-600" />,
  open: <Globe size={14} className="text-green-600" />,
  aaa: <Shield size={14} className="text-purple-600" />,
  dpsk: <Key size={14} className="text-orange-600" />,
};

const TYPE_COLORS: Record<string, string> = {
  psk: 'border-blue-200 bg-blue-50',
  open: 'border-green-200 bg-green-50',
  aaa: 'border-purple-200 bg-purple-50',
  dpsk: 'border-orange-200 bg-orange-50',
};

const TYPE_LABELS: Record<string, string> = {
  psk: 'PSK (Pre-Shared Key)',
  open: 'Open',
  aaa: 'Enterprise (AAA/RADIUS)',
  dpsk: 'DPSK (Dynamic PSK)',
};

export default function NetworkSettingsReview({ typeMappings }: Props) {
  const entries = Object.entries(typeMappings);

  if (entries.length === 0) {
    return <div className="text-sm text-gray-500">No WLANs to review.</div>;
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {entries.map(([wlanId, mapping]) => (
        <div
          key={wlanId}
          className={`border rounded-lg p-3 ${TYPE_COLORS[mapping.r1_network_type] || 'border-gray-200 bg-gray-50'}`}
        >
          <div className="flex items-center gap-2 mb-2">
            {TYPE_ICONS[mapping.r1_network_type] || <Wifi size={14} />}
            <span className="font-semibold text-sm">{mapping.wlan_name}</span>
          </div>

          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
            <div>
              <span className="text-gray-500">SZ Auth:</span>{' '}
              <span className="font-medium">{mapping.sz_auth_type}</span>
            </div>
            <div>
              <span className="text-gray-500">R1 Type:</span>{' '}
              <span className="font-medium">{TYPE_LABELS[mapping.r1_network_type] || mapping.r1_network_type}</span>
            </div>
            {mapping.dpsk_type && (
              <div className="col-span-2">
                <span className="text-gray-500">DPSK:</span>{' '}
                <span className="font-medium">{mapping.dpsk_type}</span>
              </div>
            )}
          </div>

          {mapping.notes && (
            <div className="mt-2 text-[11px] text-gray-600 flex items-start gap-1">
              <span className="text-gray-400 mt-px">*</span>
              <span>{mapping.notes}</span>
            </div>
          )}

          {mapping.needs_user_decision && (
            <div className="mt-2 text-[11px] bg-amber-100 text-amber-700 rounded px-2 py-1 font-medium">
              Requires user decision (see DPSK panel)
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
