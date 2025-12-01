/**
 * Configuration for EC comparison and diff operations
 */

// Fields to ignore during comparison (IDs, timestamps, etc.)
export const IGNORED_FIELDS = new Set([
  'id',
  'tenantId',
  'venueId',
  'tenant_id',
  'venue_id',
  'apGroupId',
  'createdAt',
  'updatedAt',
  'lastModified',
  'timestamp',
  'zoneId',
  'domainId',
  'clusterId',
  'check-all', // UI checkbox field
  'clients', // Live client count changes frequently
  'aps', // Live AP count changes frequently
  'venues', // Count field that changes
  'pictureDownloadUrl', // Signed URLs with expiring tokens
  'lanPortPictureDownloadUrl', // Signed URLs with expiring tokens
]);

// Fields to use for matching items across ECs (in priority order)
export const MATCHING_FIELDS_BY_SECTION: Record<string, string[]> = {
  venues: ['name', 'address.addressLine', 'latitude', 'longitude'],
  apGroups: ['name', 'venueId'], // Note: venueId won't match, so name is primary
  wlans: ['name', 'ssid'],
  aps: ['serialNumber', 'mac', 'name'],
  switches: ['serialNumber', 'mac', 'name'],
  switchPorts: ['portNumber', 'switchId'],
  l2AccessControls: ['name', 'macAddress'],
  l3AccessControls: ['name'],
  hotspot20Profiles: ['name'],
  hotspot20IdentityProviders: ['name'],
  hotspot20Operators: ['name'],
  dpskGroups: ['name'],
  radiusServers: ['address', 'port'],
  apRules: ['name', 'type'],
  defaultApRules: ['type'],
};

// Fields that should be highlighted as important differences
export const IMPORTANT_FIELDS = new Set([
  'name',
  'ssid',
  'enabled',
  'status',
  'encryption',
  'vlan',
  'vlanPool',
  'address',
  'port',
  'serialNumber',
  'mac',
  'description',
  'nwSubType',
  'captiveType',
  'cog', // COG (Captive Portal) settings
  // Venue WiFi settings
  'channelWidth',
  'channelBandwidth',
  'channel',
  'txPower',
  'bandSteering',
  'loadBalancing',
  'bandMode',
  'antennaType',
  'antennaGain',
  'admissionControl',
]);

/**
 * Check if a field should be ignored during comparison
 */
export function shouldIgnoreField(fieldPath: string): boolean {
  // Check if the field name (or last part of path) is in ignored list
  const fieldName = fieldPath.split('.').pop() || '';
  return IGNORED_FIELDS.has(fieldName);
}

/**
 * Get matching fields for a specific section type
 */
export function getMatchingFields(sectionType: string): string[] {
  return MATCHING_FIELDS_BY_SECTION[sectionType] || ['name'];
}

/**
 * Check if a field is considered important for highlighting
 */
export function isImportantField(fieldPath: string): boolean {
  const fieldName = fieldPath.split('.').pop() || '';
  return IMPORTANT_FIELDS.has(fieldName);
}
