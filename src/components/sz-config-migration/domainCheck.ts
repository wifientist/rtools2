// Words in a production R1 controller's name that say nothing about its region.
const GENERIC_WORDS = new Set(['prod', 'production', 'comcast', 'msp', 'ro', 'rw']);

const squash = (text: string) => text.toLowerCase().replace(/[^a-z0-9]/g, '');

// "PROD-Acme-BigSouth2" -> "bigsouth". Null when the name carries no region
// or isn't production: an MSP covers every region (its tenant is picked later),
// and a sandbox can take any zone.
function r1Region(controllerName: string): string | null {
  const words = controllerName.split(/[\s_-]+/).filter(Boolean);
  if (!words.some((w) => /^prod(uction)?$/i.test(w))) return null;
  const region = words.filter((w) => !GENERIC_WORDS.has(w.toLowerCase())).join('');
  return squash(region).replace(/\d+$/, '') || null;
}

// Too common to tie a domain to a region: Greater Boston isn't GreaterChicago.
const VAGUE_WORDS = new Set(['greater', 'central', 'division', 'region', 'sites', 'domain', 'western', 'eastern', 'northern', 'southern']);
const MIN_WORD_LENGTH = 5;

// True when a SZ domain shows no correlation with the production R1 controller's
// region: neither name contains the other, and no distinctive word is shared.
// Anything short of that gets asked about; one extra confirmation costs little.
export function domainLooksMismatched(domainName: string, controllerName: string): boolean {
  const region = r1Region(controllerName);
  const domain = squash(domainName);
  if (!region || !domain) return false;
  if (domain.includes(region) || region.includes(domain)) return false;
  return !domainName
    .split(/[^a-zA-Z0-9]+/)
    .map(squash)
    .some((word) => word.length >= MIN_WORD_LENGTH && !VAGUE_WORDS.has(word) && region.includes(word));
}
