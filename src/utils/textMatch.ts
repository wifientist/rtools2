// Loose "type a snippet" matching for picking a zone/venue/EC by name.
// Case and punctuation are ignored, and every word typed must appear
// somewhere ("kessler jersey" finds "Madera-Kessler Jersey Village").

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();

function tokens(query: string): string[] {
  return norm(query).split(' ').filter(Boolean);
}

export function matchesSnippet(text: string, query: string): boolean {
  const words = tokens(query);
  if (!words.length) return true;
  const t = norm(text);
  const squashed = t.replace(/ /g, '');
  return words.every(w => t.includes(w) || squashed.includes(w));
}

// Higher is a better match; 0 means no match. Exact name beats prefix beats
// word-start beats anywhere.
export function snippetScore(text: string, query: string): number {
  if (!matchesSnippet(text, query)) return 0;
  const t = norm(text);
  const q = norm(query);
  if (!q) return 1;
  if (t === q) return 100;
  if (t.startsWith(q)) return 50;
  if (` ${t}`.includes(` ${q}`)) return 25;
  if (t.includes(q)) return 10;
  return 5;
}
