import { useEffect, useMemo, useState } from 'react';
import { Loader2, Plus, X, AlertTriangle } from 'lucide-react';
import { apiFetch } from '@/utils/api';
import type { VenueDraft } from '@/types/szConfigMigration';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

interface Props {
  r1ControllerId: number;
  tenantId: string | null;
  draft: VenueDraft | null;
  onCreated: (venue: { id: string; name: string }) => void;
}

function countryName(code: string): string {
  if (!code) return '';
  try {
    return new Intl.DisplayNames(['en'], { type: 'region' }).of(code.toUpperCase()) || '';
  } catch {
    return '';
  }
}

// Every region the browser can name, as {code, name}, United States first.
const COUNTRIES: Array<{ code: string; name: string }> = (() => {
  try {
    const dn = new Intl.DisplayNames(['en'], { type: 'region' });
    const out: Array<{ code: string; name: string }> = [];
    const A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
    for (const a of A) for (const b of A) {
      const code = a + b;
      const name = dn.of(code);
      if (name && name !== code && !/unknown/i.test(name)) out.push({ code, name });
    }
    out.sort((x, y) => (x.code === 'US' ? -1 : y.code === 'US' ? 1 : x.name.localeCompare(y.name)));
    return out;
  } catch {
    return [{ code: 'US', name: 'United States' }];
  }
})();

// City from a one-line address, as R1's own address box resolves it:
// "1234 Main St, Houston, TX 77001, USA" -> "Houston". The part before the
// state / postal code, with a trailing country dropped. '' when unclear.
export function cityFromAddress(address: string, countryLabel = ''): string {
  let parts = address.split(',').map(p => p.trim()).filter(Boolean);
  const countryish = new Set(['usa', 'us', 'united states', 'united states of america', countryLabel.toLowerCase()]);
  while (parts.length && countryish.has(parts[parts.length - 1].toLowerCase())) parts = parts.slice(0, -1);
  if (parts.length < 2) return '';
  const last = parts[parts.length - 1];
  const statePostal = /^([A-Za-z]{2,}\.?)?\s*[A-Za-z0-9]{0,3}\s?\d[A-Za-z0-9 -]*$|^[A-Z]{2}$/;
  if (statePostal.test(last)) {
    const before = parts[parts.length - 2];
    // "Houston, TX": the part before the state is the city -- unless it's the street.
    if (parts.length >= 3 || !/\d/.test(before)) return before;
    return '';
  }
  if (/\d/.test(last)) return parts.length >= 3 ? parts[parts.length - 2] : '';
  return last;
}

const TIME_ZONES: string[] = (() => {
  try {
    return (Intl as any).supportedValuesOf('timeZone') as string[];
  } catch {
    return [];
  }
})();

export default function CreateVenuePanel({ r1ControllerId, tenantId, draft, onCreated }: Props) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [addressLine, setAddressLine] = useState('');
  const [city, setCity] = useState('');
  const [cityEdited, setCityEdited] = useState(false);
  const [editDetails, setEditDetails] = useState(false);
  const [countryCode, setCountryCode] = useState('');
  const [country, setCountry] = useState('');
  const [timezone, setTimezone] = useState('');
  const [tags, setTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState('');
  // Pasted / typed tags split on this; remembered per browser.
  const [tagDelimiter, setTagDelimiter] = useState<string>(() => {
    try { return localStorage.getItem('szVenueTagDelimiter') ?? ';'; } catch { return ';'; }
  });
  const changeDelimiter = (d: string) => {
    setTagDelimiter(d);
    try { localStorage.setItem('szVenueTagDelimiter', d); } catch { /* not kept; fine */ }
  };
  const splitTags = (raw: string) =>
    (tagDelimiter ? raw.split(tagDelimiter) : [raw]).map(t => t.trim()).filter(Boolean);
  const [knownTags, setKnownTags] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Pre-fill once the draft arrives; the operator's edits win after that.
  useEffect(() => {
    if (!draft) return;
    setName(draft.name);
    setDescription(draft.description);
    setAddressLine(draft.address.addressLine || '');
    setCountryCode(draft.address.countryCode || '');
    setCountry(countryName(draft.address.countryCode));
    setTimezone(draft.address.timezone || '');
  }, [draft]);

  useEffect(() => {
    const qs = new URLSearchParams({ controller_id: String(r1ControllerId) });
    if (tenantId) qs.set('tenant_id', tenantId);
    apiFetch(`${API_URL}/sz-migration/venue-tags?${qs}`)
      .then(r => (r.ok ? r.json() : { tags: [] }))
      .then(d => setKnownTags(d.tags || []))
      .catch(() => setKnownTags([]));
  }, [r1ControllerId, tenantId]);

  const zoneCountry = draft?.address.countryCode || '';
  const countryChanged = !!zoneCountry && countryCode.toUpperCase() !== zoneCountry.toUpperCase();
  const suggestions = useMemo(
    () => knownTags.filter(t => !tags.includes(t)),
    [knownTags, tags],
  );

  const addTag = (raw: string) => {
    setTags(prev => [...prev, ...splitTags(raw).filter((t, i, all) => !prev.includes(t) && all.indexOf(t) === i)]);
    setTagInput('');
  };

  // R1's API needs a city and country; its own form gets them from one
  // address box (Google lookup). Here: the city read from the address unless
  // corrected, the country from the dropdown.
  const effectiveCity = (cityEdited ? city : cityFromAddress(addressLine, country)).trim();
  const canCreate = name.trim().length >= 2 && !!effectiveCity && country.trim() && !creating;

  const create = async () => {
    setCreating(true);
    setError(null);
    try {
      const res = await apiFetch(`${API_URL}/sz-migration/venues`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          controller_id: r1ControllerId,
          tenant_id: tenantId,
          name: name.trim(),
          description: description.trim() || null,
          address: {
            addressLine: addressLine.trim() || null,
            city: effectiveCity,
            country: country.trim(),
            countryCode: countryCode.trim().toUpperCase() || null,
            timezone: timezone.trim() || null,
            latitude: draft?.address.latitude ?? null,
            longitude: draft?.address.longitude ?? null,
          },
          tags: [...tags, ...splitTags(tagInput).filter(t => !tags.includes(t))],
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Venue create failed: ${res.status}`);
      onCreated(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCreating(false);
    }
  };

  const input = 'w-full border border-gray-300 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500';
  const label = 'block text-xs font-medium text-gray-600 mb-1';

  return (
    <div className="space-y-4">
      {draft?.notes.map(n => (
        <div key={n} className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">{n}</div>
      ))}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="md:col-span-2">
          <label className={label}>Venue name *</label>
          <input className={input} value={name} maxLength={63} onChange={e => setName(e.target.value)} />
        </div>
        <div className="md:col-span-2">
          <label className={label}>Address *</label>
          <input className={input} value={addressLine} placeholder="e.g. 1234 Main St, Houston, TX 77001"
                 onChange={e => setAddressLine(e.target.value)} />
          <div className="text-[11px] text-gray-500 mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
            {editDetails ? (
              <>
                <label className="flex items-center gap-1">City
                  <input className="border border-gray-300 rounded px-1.5 py-0.5 text-xs w-40" value={cityEdited ? city : effectiveCity}
                         onChange={e => { setCity(e.target.value); setCityEdited(true); }} />
                </label>
                <label className="flex items-center gap-1">Time zone
                  <input className="border border-gray-300 rounded px-1.5 py-0.5 text-xs w-44" value={timezone}
                         list="sz-venue-timezones" placeholder="e.g. America/Chicago" onChange={e => setTimezone(e.target.value)} />
                </label>
                <datalist id="sz-venue-timezones">
                  {TIME_ZONES.map(tz => <option key={tz} value={tz} />)}
                </datalist>
                <button type="button" onClick={() => setEditDetails(false)} className="text-indigo-600 hover:underline">Done</button>
              </>
            ) : (
              <>
                <span>City: {effectiveCity
                  ? <b className="text-gray-700">{effectiveCity}</b>
                  : <span className="text-amber-700">couldn't read one from the address — set it</span>}
                  {effectiveCity && !cityEdited && ' (from the address)'}</span>
                <span>Time zone: {timezone ? <b className="text-gray-700">{timezone}</b> : 'R1 default'}
                  {timezone && draft?.address.timezone === timezone && ' (from the SZ zone)'}</span>
                <button type="button" onClick={() => setEditDetails(true)} className="text-indigo-600 hover:underline">Edit</button>
              </>
            )}
          </div>
        </div>
        <div>
          <label className={label}>Country *</label>
          <select className={input} value={countryCode}
                  onChange={e => { setCountryCode(e.target.value); setCountry(countryName(e.target.value)); }}>
            <option value="">Choose a country…</option>
            {COUNTRIES.map(c => <option key={c.code} value={c.code}>{c.name} ({c.code})</option>)}
          </select>
        </div>
        <div>
          <label className={label}>Description</label>
          <input className={input} value={description} onChange={e => setDescription(e.target.value)} />
        </div>
      </div>

      {countryChanged && (
        <div className="flex items-start gap-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">
          <AlertTriangle size={14} className="mt-0.5 flex-shrink-0" />
          The zone's country is {zoneCountry}. A venue's country can't be changed after it is created, and the
          allowed channels depend on it, so the zone's channel plan may not apply.
        </div>
      )}

      <div>
        <div className="flex items-center gap-2 mb-1">
          <label className="text-xs font-medium text-gray-600">Tags</label>
          <label className="flex items-center gap-1 text-[11px] text-gray-500 ml-auto"
                 title="Pasted or typed text is split into tags on this">
            Delimiter
            <input className="border border-gray-300 rounded px-1 py-0 w-10 text-center font-mono text-xs"
                   value={tagDelimiter} maxLength={3} onChange={e => changeDelimiter(e.target.value)} />
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 border border-gray-300 rounded px-2 py-1.5">
          {tags.map(t => (
            <span key={t} className="inline-flex items-center gap-1 bg-indigo-50 text-indigo-700 text-xs rounded px-1.5 py-0.5">
              {t}
              <button onClick={() => setTags(tags.filter(x => x !== t))} className="hover:text-indigo-900">
                <X size={11} />
              </button>
            </span>
          ))}
          <input
            className="flex-1 min-w-[10rem] text-sm outline-none"
            value={tagInput}
            list="sz-venue-tags"
            placeholder={tags.length ? '' : `Type or paste tags (Enter or "${tagDelimiter || 'Enter'}" separates), e.g. CID:273785`}
            onChange={e => setTagInput(e.target.value)}
            onPaste={e => {
              const text = e.clipboardData.getData('text');
              if (tagDelimiter && text.includes(tagDelimiter)) {
                e.preventDefault();
                addTag(tagInput + text);
              }
            }}
            onKeyDown={e => {
              if (e.key === 'Enter' || (tagDelimiter.length === 1 && e.key === tagDelimiter)) {
                e.preventDefault();
                addTag(tagInput);
              } else if (e.key === 'Backspace' && !tagInput && tags.length) {
                setTags(tags.slice(0, -1));
              }
            }}
            onBlur={() => tagInput.trim() && addTag(tagInput)}
          />
          <datalist id="sz-venue-tags">
            {suggestions.map(t => <option key={t} value={t} />)}
          </datalist>
        </div>
        {knownTags.length > 0 && (
          <p className="text-[11px] text-gray-400 mt-1">{knownTags.length} tags already used in this tenant are suggested as you type</p>
        )}
      </div>

      {error && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">{error}</div>}

      <button
        onClick={create}
        disabled={!canCreate}
        className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:bg-gray-400 font-medium text-sm flex items-center gap-2"
      >
        {creating ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />}
        {creating ? 'Creating venue…' : 'Create venue in R1'}
      </button>
    </div>
  );
}
