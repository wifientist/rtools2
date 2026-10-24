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
  dpsk_external: <Key size={14} className="text-amber-700" />,
  wispr: <Globe size={14} className="text-sky-700" />,
};

const TYPE_COLORS: Record<string, string> = {
  psk: 'border-blue-200 bg-blue-50',
  open: 'border-green-200 bg-green-50',
  aaa: 'border-purple-200 bg-purple-50',
  dpsk: 'border-orange-200 bg-orange-50',
  dpsk_external: 'border-amber-200 bg-amber-50',
  wispr: 'border-sky-200 bg-sky-50',
};

const TYPE_LABELS: Record<string, string> = {
  psk: 'PSK (Pre-Shared Key)',
  open: 'Open',
  aaa: 'Enterprise (AAA/RADIUS)',
  dpsk: 'DPSK (Dynamic PSK)',
  dpsk_external: 'DPSK, keys checked by external RADIUS',
  wispr: 'Guest (WISPr captive portal, SZ hotspot)',
};

export default function NetworkSettingsReview({ typeMappings }: Props) {
  const entries = Object.entries(typeMappings);

  if (entries.length === 0) {
    return <div className="text-sm text-gray-500">No WLANs to review.</div>;
  }

  const vlanDiffers = entries.filter(([, m]) => m.r1_vlan !== undefined && (m.sz_vlan ?? 1) !== m.r1_vlan).length;

  return (
    <div className="space-y-3">
    {vlanDiffers > 0 && (
      <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">
        {vlanDiffers} WLAN{vlanDiffers === 1 ? '' : 's'} will have a different default VLAN in R1 than in SZ — an existing
        R1 network of the same name is reused with its own VLAN. Change it in R1 or rename it to have this one created.
      </div>
    )}
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
            <VlanRow mapping={mapping} />
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
    </div>
  );
}

function VlanRow({ mapping }: { mapping: TypeMapping }) {
  if (mapping.r1_vlan === undefined) return null;   // older resolve result
  const differs = (mapping.sz_vlan ?? 1) !== mapping.r1_vlan;
  return (
    <div className="col-span-2 mt-1">
      <div className={`flex items-center gap-2 rounded px-2 py-1 ${differs ? 'bg-red-50 text-red-800' : 'bg-white/60'}`}>
        <span className="text-gray-500">Default VLAN</span>
        <span>SZ <span className="font-semibold">{mapping.sz_vlan ?? '— (1)'}</span></span>
        <span className="text-gray-400">→</span>
        <span>R1 <span className="font-semibold">{mapping.r1_vlan ?? '?'}</span></span>
        <span className="text-[10px] text-gray-500">
          {mapping.r1_vlan_source === 'existing' ? 'existing R1 network, kept as is' : 'set on create'}
        </span>
        {differs && <span className="ml-auto text-[10px] font-bold">DIFFERS</span>}
      </div>
      {(mapping.vlan_notes ?? []).map(n => (
        <div key={n} className="text-[10px] text-amber-700 mt-0.5 px-2">{n}</div>
      ))}
    </div>
  );
}
