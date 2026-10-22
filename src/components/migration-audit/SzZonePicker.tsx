import { useEffect, useMemo, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { apiFetch } from '@/utils/api';
import { matchesSnippet, snippetScore } from '@/utils/textMatch';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';
const LIST_CAP = 50;

interface Zone {
  id: string;
  name: string;
  description?: string;
  _domain_name?: string;
}

interface Props {
  controllerId: number | null;
  filter: string;                       // inherited from the page's search box
  selectedZoneId: string | null;
  onZoneSelect: (zoneId: string | null, zoneName: string | null) => void;
  disabled?: boolean;
}

// Every zone on the controller, across domains, narrowed by the shared search
// so the domain never has to be picked first.
export default function SzZonePicker({ controllerId, filter, selectedZoneId, onZoneSelect, disabled }: Props) {
  const [zones, setZones] = useState<Zone[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!controllerId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    apiFetch(`${API_URL}/sz/${controllerId}/zones/all`)
      .then(async res => {
        if (!res.ok) throw new Error(`Failed to load zones (${res.status})`);
        const json = await res.json();
        const seen = new Set<string>();
        const list = (json.data || []).filter((z: Zone) => !seen.has(z.id) && seen.add(z.id));
        if (!cancelled) setZones(list);
      })
      .catch(e => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [controllerId]);

  const label = (z: Zone) => `${z.name} ${z.description ?? ''} ${z._domain_name ?? ''}`;

  const matches = useMemo(() => {
    const q = filter.trim();
    if (!q) return [...zones].sort((a, b) => a.name.localeCompare(b.name));
    return zones
      .filter(z => matchesSnippet(label(z), q))
      .map(z => ({ z, s: snippetScore(z.name, q) }))
      .sort((a, b) => b.s - a.s || a.z.name.localeCompare(b.z.name))
      .map(x => x.z);
  }, [zones, filter]);

  const selected = zones.find(z => z.id === selectedZoneId);
  const suggested = filter.trim() && matches.length > 0 ? matches[0].id : null;

  if (loading) return <div className="text-sm text-gray-400 flex items-center gap-2"><Loader2 size={14} className="animate-spin" /> Loading zones…</div>;
  if (error) return <div className="text-sm text-red-600">{error}</div>;

  return (
    <div className="space-y-2">
      <div className="text-xs text-gray-500">
        {filter.trim()
          ? `${matches.length} of ${zones.length} zones match`
          : `${zones.length} zones — type in the search above to narrow`}
      </div>
      <div className="border border-gray-200 rounded max-h-64 overflow-y-auto divide-y divide-gray-100">
        {matches.slice(0, LIST_CAP).map(z => {
          const isSel = z.id === selectedZoneId;
          return (
            <button
              key={z.id}
              type="button"
              disabled={disabled}
              onClick={() => onZoneSelect(isSel ? null : z.id, isSel ? null : z.name)}
              className={`w-full text-left px-3 py-1.5 text-sm flex items-center gap-2 hover:bg-blue-50 disabled:opacity-50 ${isSel ? 'bg-blue-100' : ''}`}
            >
              <span className="font-medium text-gray-800 truncate">{z.name}</span>
              {z._domain_name && <span className="text-xs text-gray-400 truncate">{z._domain_name}</span>}
              {z.id === suggested && !isSel && <span className="ml-auto text-[10px] font-semibold text-amber-700 bg-amber-50 border border-amber-200 rounded px-1.5">best match</span>}
              {isSel && <span className="ml-auto text-[10px] font-semibold text-blue-700">selected</span>}
            </button>
          );
        })}
        {matches.length === 0 && <div className="px-3 py-2 text-sm text-gray-400 italic">No zone matches “{filter}”.</div>}
        {matches.length > LIST_CAP && <div className="px-3 py-1.5 text-xs text-gray-400">…{matches.length - LIST_CAP} more — narrow the search</div>}
      </div>
      {selected && !matches.slice(0, LIST_CAP).includes(selected) && (
        <div className="text-xs text-gray-500">Selected: <span className="font-medium text-gray-700">{selected.name}</span> (hidden by the search)</div>
      )}
    </div>
  );
}
