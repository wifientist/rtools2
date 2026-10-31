// "resident" -> "residents", "identity" -> "identities", "batch" -> "batches"
export function pluralize(word: string): string {
  if (word.endsWith('y')) return `${word.slice(0, -1)}ies`;
  return word.endsWith('ch') ? `${word}es` : `${word}s`;
}

// "ap_groups" -> "AP groups", "wifi_networks" -> "WiFi networks"
export function resourceLabel(type: string): string {
  const words = type.replace(/_/g, ' ');
  const label = words.replace(/\bap\b/g, 'AP').replace(/\baps\b/g, 'APs').replace(/\bwifi\b/g, 'WiFi')
    .replace(/\bdpsk\b/g, 'DPSK').replace(/\bradius\b/g, 'RADIUS');
  return label.charAt(0).toUpperCase() + label.slice(1);
}

// What a phase's result says in a few counts. Phases name their outputs freely
// (radius_profiles_created, pools_linked), so this reads the shape, not the
// names: numbers and lists are counts. Dicts are skipped because most are
// payloads (a plan, a mapping) rather than something to report; "failed" is
// the exception, keyed by object name.
export function resultCounts(result: Record<string, any> | undefined): [string, number][] {
  if (!result) return [];
  const counts: [string, number][] = [];
  for (const [key, value] of Object.entries(result)) {
    const label = key.replace(/_/g, ' ');
    if (typeof value === 'number') counts.push([label, value]);
    else if (Array.isArray(value)) counts.push([label, value.length]);
    else if (key === 'failed' && value && typeof value === 'object') {
      counts.push([label, Object.keys(value).length]);
    }
  }
  return counts.filter(([, n]) => n > 0);
}
