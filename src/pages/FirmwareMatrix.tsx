import { useState } from 'react';

type WifiGeneration = 'wifi5' | 'wifi6' | 'wifi6e' | 'wifi7';
type FirmwareVersion = '5.2.2' | '6.0' | '6.1' | '6.1.1' | '6.1.2' | '7.0.0' | '7.1.0' | '7.1.1';
type SupportStatus = 'initial' | 'supported' | 'final' | 'unsupported';

interface APModel {
  model: string;
  wifiGen: WifiGeneration;
  firmwareSupport: Partial<Record<FirmwareVersion, SupportStatus>>;
  notes?: string;
}

interface FirmwareRelease {
  version: string;
  releaseDate: string;
  highlights?: string[];
  patches: {
    version: string;
    releaseDate: string;
    notes?: string;
  }[];
}

interface UpgradePath {
  from: FirmwareVersion;
  to: FirmwareVersion;
  directUpgrade: boolean;
  intermediateSteps?: FirmwareVersion[];
  notes?: string;
  isRecommended?: boolean;
}

interface FirmwareUpgradeConfig {
  version: FirmwareVersion;
  directUpgradeTo: FirmwareVersion[];
  notes?: string;
}

function FirmwareMatrix() {
  const [selectedWifiGen, setSelectedWifiGen] = useState<WifiGeneration | 'all'>('all');
  const [currentFirmware, setCurrentFirmware] = useState<FirmwareVersion | null>(null);
  const [targetFirmware, setTargetFirmware] = useState<FirmwareVersion | null>(null);
  const [expandedReleases, setExpandedReleases] = useState<Set<string>>(new Set());
  const [showReleases, setShowReleases] = useState<boolean>(true);

  // Sample AP model data - will eventually come from JSON files
  const apModels: APModel[] = [
    // Wi-Fi 5 (802.11ac Wave 2)
    {
      model: 'R510',
      wifiGen: 'wifi5',
      firmwareSupport: {
        '5.2.2': 'initial',
        '6.1.2': 'supported',
        '7.0.0': 'supported',
        '7.1.0': 'supported',
        '7.1.1': 'final'
      },
      notes: 'Indoor, dual-band'
    },
    {
      model: 'R610',
      wifiGen: 'wifi5',
      firmwareSupport: {
        '5.2.2': 'initial',
        '6.1.2': 'supported',
        '7.0.0': 'supported',
        '7.1.0': 'supported',
        '7.1.1': 'final'
      },
      notes: 'Indoor, dual-band'
    },
    {
      model: 'R710',
      wifiGen: 'wifi5',
      firmwareSupport: {
        '5.2.2': 'initial',
        '6.1.2': 'supported',
        '7.0.0': 'supported',
        '7.1.0': 'supported',
        '7.1.1': 'final'
      },
      notes: 'Indoor, dual-band, 4x4:4'
    },
    {
      model: 'R720',
      wifiGen: 'wifi5',
      firmwareSupport: {
        '5.2.2': 'initial',
        '6.1.2': 'supported',
        '7.0.0': 'supported',
        '7.1.0': 'supported',
        '7.1.1': 'final'
      },
      notes: 'Indoor, dual-band, 4x4:4'
    },
    {
      model: 'T310c',
      wifiGen: 'wifi5',
      firmwareSupport: {
        '5.2.2': 'initial',
        '6.1.2': 'final'
      },
      notes: 'Outdoor, single-band 5GHz'
    },
    {
      model: 'T310d',
      wifiGen: 'wifi5',
      firmwareSupport: {
        '5.2.2': 'initial',
        '6.1.2': 'final'
      },
      notes: 'Outdoor, dual-band'
    },

    // Wi-Fi 6 (802.11ax)
    {
      model: 'R650',
      wifiGen: 'wifi6',
      firmwareSupport: {
        '6.1.2': 'initial',
        '7.0.0': 'supported',
        '7.1.0': 'supported',
        '7.1.1': 'supported'
      },
      notes: 'Indoor, dual-band, 4x4:4'
    },
    {
      model: 'R750',
      wifiGen: 'wifi6',
      firmwareSupport: {
        '6.1.2': 'initial',
        '7.0.0': 'supported',
        '7.1.0': 'supported',
        '7.1.1': 'supported'
      },
      notes: 'Indoor, dual-band, 4x4:4'
    },
    {
      model: 'R850',
      wifiGen: 'wifi6',
      firmwareSupport: {
        '6.1.2': 'initial',
        '7.0.0': 'supported',
        '7.1.0': 'supported',
        '7.1.1': 'supported'
      },
      notes: 'Indoor, dual-band, 8x8:8'
    },
    {
      model: 'T650',
      wifiGen: 'wifi6',
      firmwareSupport: {
        '6.1.2': 'initial',
        '7.0.0': 'supported',
        '7.1.0': 'supported',
        '7.1.1': 'supported'
      },
      notes: 'Outdoor, dual-band, 4x4:4'
    },
    {
      model: 'R350',
      wifiGen: 'wifi6',
      firmwareSupport: {
        '6.1.2': 'initial',
        '7.0.0': 'supported',
        '7.1.0': 'supported',
        '7.1.1': 'supported'
      },
      notes: 'Indoor, dual-band, compact'
    },
    {
      model: 'H550',
      wifiGen: 'wifi6',
      firmwareSupport: {
        '6.1.2': 'initial',
        '7.0.0': 'supported',
        '7.1.0': 'supported',
        '7.1.1': 'supported'
      },
      notes: 'Indoor, dual-band, wallplate'
    },

    // Wi-Fi 6E
    {
      model: 'R760',
      wifiGen: 'wifi6e',
      firmwareSupport: {
        '7.0.0': 'initial',
        '7.1.0': 'supported',
        '7.1.1': 'supported'
      },
      notes: 'Indoor, tri-band, 6GHz support'
    },
    {
      model: 'R560',
      wifiGen: 'wifi6e',
      firmwareSupport: {
        '7.0.0': 'initial',
        '7.1.0': 'supported',
        '7.1.1': 'supported'
      },
      notes: 'Indoor, tri-band, 6GHz support'
    },

    // Wi-Fi 7 (802.11be)
    {
      model: 'R770',
      wifiGen: 'wifi7',
      firmwareSupport: {
        '7.1.0': 'initial',
        '7.1.1': 'supported'
      },
      notes: 'Indoor, tri-band, Wi-Fi 7'
    },
    {
      model: 'R670',
      wifiGen: 'wifi7',
      firmwareSupport: {
        '7.1.0': 'initial',
        '7.1.1': 'supported'
      },
      notes: 'Indoor, tri-band, Wi-Fi 7'
    },
    {
      model: 'T670',
      wifiGen: 'wifi7',
      firmwareSupport: {
        '7.1.0': 'initial',
        '7.1.1': 'supported'
      },
      notes: 'Outdoor, tri-band, Wi-Fi 7'
    },
    {
      model: 'R370',
      wifiGen: 'wifi7',
      firmwareSupport: {
        '7.1.1': 'initial'
      },
      notes: 'Indoor, dual-band, Wi-Fi 7'
    },
    {
      model: 'H670',
      wifiGen: 'wifi7',
      firmwareSupport: {
        '7.1.1': 'initial'
      },
      notes: 'Indoor, tri-band, wallplate'
    },
    {
      model: 'R575',
      wifiGen: 'wifi7',
      firmwareSupport: {
        '7.1.1': 'initial'
      },
      notes: 'Indoor, tri-band, Wi-Fi 7'
    },
  ];

  // Sample firmware release data - will eventually come from JSON files
  const firmwareReleases: FirmwareRelease[] = [
    {
      version: '5.2.2',
      releaseDate: '2019-12-15',
      highlights: [
        'Last major release supporting older AP models',
        'Stable baseline for legacy deployments',
      ],
      patches: [
        { version: '5.2.2.0.1234', releaseDate: '2020-01-15', notes: 'Security fixes' },
        { version: '5.2.2.0.2345', releaseDate: '2020-03-20', notes: 'Stability improvements' },
      ],
    },
    {
      version: '6.0',
      releaseDate: '2021-06-10',
      highlights: [
        'Initial Wi-Fi 6 support',
        'Improved roaming performance',
        'OFDMA and MU-MIMO enhancements',
      ],
      patches: [
        { version: '6.1.2.0.123', releaseDate: '2021-07-15', notes: 'Bug fixes' },
      ],
    },
    {
      version: '6.1',
      releaseDate: '2021-06-10',
      highlights: [
        'Enhanced Wi-Fi 6 support',
        'Improved roaming performance',
        'OFDMA and MU-MIMO enhancements',
      ],
      patches: [
        { version: '6.1.2.0.123', releaseDate: '2021-07-15', notes: 'Bug fixes for mesh' },
        { version: '6.1.2.0.234', releaseDate: '2021-09-01', notes: 'Security updates' },
        { version: '6.1.2.0.345', releaseDate: '2021-11-10', notes: 'Performance improvements' },
      ],
    },
    {
      version: '6.1.1',
      releaseDate: '2021-06-10',
      highlights: [
        'Enhanced Wi-Fi 6 support',
        'Improved roaming performance',
        'OFDMA and MU-MIMO enhancements',
      ],
      patches: [
        { version: '6.1.2.0.123', releaseDate: '2021-07-15', notes: 'Bug fixes for mesh' },
        { version: '6.1.2.0.234', releaseDate: '2021-09-01', notes: 'Security updates' },
        { version: '6.1.2.0.345', releaseDate: '2021-11-10', notes: 'Performance improvements' },
      ],
    },
    {
      version: '6.1.2',
      releaseDate: '2021-06-10',
      highlights: [
        'Enhanced Wi-Fi 6 support',
        'Improved roaming performance',
        'OFDMA and MU-MIMO enhancements',
      ],
      patches: [
        { version: '6.1.2.0.123', releaseDate: '2021-07-15', notes: 'Bug fixes for mesh' },
        { version: '6.1.2.0.234', releaseDate: '2021-09-01', notes: 'Security updates' },
        { version: '6.1.2.0.345', releaseDate: '2021-11-10', notes: 'Performance improvements' },
      ],
    },
    {
      version: '7.0.0',
      releaseDate: '2022-09-01',
      highlights: [
        'Major architectural updates',
        'Initial Wi-Fi 6E support',
        'Enhanced controller integration',
      ],
      patches: [
        { version: '7.0.0.0.100', releaseDate: '2022-10-01', notes: 'Initial bug fixes' },
        { version: '7.0.0.0.200', releaseDate: '2022-11-15', notes: 'Performance tuning' },
      ],
    },
    {
      version: '7.1.0',
      releaseDate: '2023-03-15',
      highlights: [
        'Initial Wi-Fi 7 AP support',
        'Improved 6GHz performance',
        'Enhanced mesh capabilities',
      ],
      patches: [
        { version: '7.1.0.0.100', releaseDate: '2023-04-10', notes: 'Stability improvements' },
        { version: '7.1.0.0.200', releaseDate: '2023-05-20', notes: 'Wi-Fi 7 optimizations' },
      ],
    },
    {
      version: '7.1.1',
      releaseDate: '2023-08-20',
      highlights: [
        'Expanded Wi-Fi 7 support',
        'Advanced AI-driven optimization',
        'Enhanced security features',
        'WPA3 improvements',
      ],
      patches: [
        { version: '7.1.1.0.100', releaseDate: '2023-09-15', notes: 'Initial stability fixes' },
        { version: '7.1.1.0.200', releaseDate: '2023-11-01', notes: '6GHz optimizations' },
        { version: '7.1.1.0.300', releaseDate: '2024-01-15', notes: 'Security enhancements' },
      ],
    },
  ];

  const filteredModels = selectedWifiGen === 'all'
    ? apModels
    : apModels.filter(ap => ap.wifiGen === selectedWifiGen);

  const getCompatibilityStatus = (apModel: APModel, firmware: string): SupportStatus | 'unsupported' => {
    const status = apModel.firmwareSupport[firmware as FirmwareVersion];
    return status || 'unsupported';
  };

  // Define direct upgrade paths for each firmware version
  const upgradeConfig: Record<FirmwareVersion, FirmwareUpgradeConfig> = {
    '5.2.2': {
      version: '5.2.2',
      directUpgradeTo: ['6.0', '6.1', '6.1.1', '6.1.2'],
      notes: 'Legacy firmware - must upgrade to 6.1.2 before any 7.x versions'
    },
    '6.0': {
      version: '6.0',
      directUpgradeTo: ['6.1', '6.1.1', '6.1.2'],
      notes: 'Initial Wi-Fi 6 support - can upgrade to any 6.1.x version'
    },
    '6.1': {
      version: '6.1',
      directUpgradeTo: ['6.1.1', '6.1.2', '7.0.0', '7.1.0', '7.1.1'],
      notes: 'Enhanced Wi-Fi 6 support - can upgrade to 6.1.2'
    },
    '6.1.1': {
      version: '6.1.1',
      directUpgradeTo: ['6.1.2', '7.0.0', '7.1.0', '7.1.1'],
      notes: 'Can upgrade directly to 6.1.2 or any 7.x version'
    },
    '6.1.2': {
      version: '6.1.2',
      directUpgradeTo: ['7.0.0', '7.1.0', '7.1.1'],
      notes: 'Can upgrade directly to any 7.x version'
    },
    '7.0.0': {
      version: '7.0.0',
      directUpgradeTo: ['7.1.0', '7.1.1']
    },
    '7.1.0': {
      version: '7.1.0',
      directUpgradeTo: ['7.1.1']
    },
    '7.1.1': {
      version: '7.1.1',
      directUpgradeTo: []
    },
  };

  // Define recommended upgrade paths (optional - these will be highlighted)
  const recommendedPaths: Array<{ from: FirmwareVersion; to: FirmwareVersion; reason?: string }> = [
    { from: '5.2.2', to: '6.1.2', reason: 'Stable intermediate step' },
    { from: '6.1.2', to: '7.1.1', reason: 'Latest stable release' },
    { from: '7.0.0', to: '7.1.1', reason: 'Latest stable release' },
    { from: '7.1.0', to: '7.1.1', reason: 'Latest stable release' },
  ];

  // Find recommended path first, then fall back to BFS
  const findRecommendedPath = (from: FirmwareVersion, to: FirmwareVersion): FirmwareVersion[] | null => {
    // Try to build a path using only recommended paths
    const queue: Array<{ version: FirmwareVersion; path: FirmwareVersion[] }> = [
      { version: from, path: [] }
    ];
    const visited = new Set<FirmwareVersion>([from]);

    while (queue.length > 0) {
      const current = queue.shift()!;

      // Find all recommended next steps from current version
      const recommendedNextSteps = recommendedPaths
        .filter(p => p.from === current.version)
        .map(p => p.to);

      for (const nextVersion of recommendedNextSteps) {
        if (nextVersion === to) {
          return [...current.path, nextVersion];
        }

        if (!visited.has(nextVersion)) {
          visited.add(nextVersion);
          queue.push({
            version: nextVersion,
            path: [...current.path, nextVersion]
          });
        }
      }
    }

    return null; // No recommended path found
  };

  // BFS algorithm that prioritizes recommended hops at each step
  const findUpgradePath = (from: FirmwareVersion, to: FirmwareVersion): FirmwareVersion[] | null => {
    if (from === to) return [];

    const queue: Array<{ version: FirmwareVersion; path: FirmwareVersion[] }> = [
      { version: from, path: [] }
    ];
    const visited = new Set<FirmwareVersion>([from]);

    while (queue.length > 0) {
      const current = queue.shift()!;
      const directUpgrades = upgradeConfig[current.version]?.directUpgradeTo || [];

      // Split available upgrades into recommended and non-recommended
      const recommendedNextSteps = recommendedPaths
        .filter(p => p.from === current.version)
        .map(p => p.to)
        .filter(v => directUpgrades.includes(v));

      const nonRecommendedNextSteps = directUpgrades.filter(
        v => !recommendedNextSteps.includes(v)
      );

      // Process recommended steps FIRST (they get added to queue before non-recommended)
      const orderedNextSteps = [...recommendedNextSteps, ...nonRecommendedNextSteps];

      for (const nextVersion of orderedNextSteps) {
        if (nextVersion === to) {
          return [...current.path, nextVersion];
        }

        if (!visited.has(nextVersion)) {
          visited.add(nextVersion);
          queue.push({
            version: nextVersion,
            path: [...current.path, nextVersion]
          });
        }
      }
    }

    return null; // No path found
  };

  const getUpgradePath = (from: FirmwareVersion | null, to: FirmwareVersion | null): UpgradePath | null => {
    if (!from || !to || from === to) return null;

    // Check if it's a direct upgrade
    const directUpgrades = upgradeConfig[from]?.directUpgradeTo || [];
    const isDirect = directUpgrades.includes(to);

    // Check if this is a direct recommended path
    const isDirectRecommended = recommendedPaths.some(p => p.from === from && p.to === to);
    const recommendedPathInfo = recommendedPaths.find(p => p.from === from && p.to === to);

    if (isDirect) {
      return {
        from,
        to,
        directUpgrade: true,
        isRecommended: isDirectRecommended,
        notes: recommendedPathInfo?.reason
      };
    }

    // Try to find a multi-step path using recommended paths first
    const recommendedPath = findRecommendedPath(from, to);
    if (recommendedPath) {
      const intermediateSteps = recommendedPath.slice(0, -1);
      return {
        from,
        to,
        directUpgrade: false,
        intermediateSteps,
        isRecommended: true,
        notes: `Recommended upgrade path: ${from} → ${intermediateSteps.join(' → ')} → ${to}`
      };
    }

    // Fall back to finding any valid path using BFS
    const path = findUpgradePath(from, to);
    if (!path) return null;

    // Extract intermediate steps (all steps except the final one)
    const intermediateSteps = path.slice(0, -1);

    return {
      from,
      to,
      directUpgrade: false,
      intermediateSteps,
      isRecommended: false,
      notes: `Requires ${intermediateSteps.length} intermediate step${intermediateSteps.length > 1 ? 's' : ''}: ${intermediateSteps.join(' → ')}`
    };
  };

  const getAffectedAPs = (from: FirmwareVersion | null, to: FirmwareVersion | null) => {
    if (!from || !to) return { compatible: [], incompatible: [], warnings: [] };

    const compatible: APModel[] = [];
    const incompatible: APModel[] = [];
    const warnings: APModel[] = [];

    apModels.forEach(ap => {
      const fromStatus = ap.firmwareSupport[from];
      const toStatus = ap.firmwareSupport[to];

      if (fromStatus && toStatus) {
        if (toStatus === 'final') {
          warnings.push(ap);
        } else {
          compatible.push(ap);
        }
      } else if (fromStatus && !toStatus) {
        incompatible.push(ap);
      }
    });

    return { compatible, incompatible, warnings };
  };

  return (
    <div className="min-h-screen bg-gray-50 p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-6">
          <h1 className="text-3xl font-bold text-gray-900">SmartZone Firmware Matrix</h1>
          <p className="text-gray-600 mt-1">AP compatibility, upgrade paths, and firmware release highlights</p>
        </div>

        {/* Filters */}
        <div className="bg-white rounded-lg shadow p-4 mb-6">
          <div className="flex items-center gap-4 flex-wrap">
            <div>
              <label className="text-sm font-medium text-gray-700 mr-2">Wi-Fi Generation:</label>
              <select
                value={selectedWifiGen}
                onChange={(e) => setSelectedWifiGen(e.target.value as WifiGeneration | 'all')}
                className="border border-gray-300 rounded px-3 py-2 text-sm"
              >
                <option value="all">All Generations</option>
                <option value="wifi5">Wi-Fi 5 (802.11ac)</option>
                <option value="wifi6">Wi-Fi 6 (802.11ax)</option>
                <option value="wifi6e">Wi-Fi 6E</option>
                <option value="wifi7">Wi-Fi 7 (802.11be)</option>
              </select>
            </div>
          </div>
        </div>

        {/* Firmware Releases Table */}
        <div className="mb-6">
          <div className="bg-white rounded-lg shadow overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
              <div>
                <h2 className="text-2xl font-bold text-gray-900">Firmware Releases</h2>
                <p className="text-sm text-gray-600 mt-1">Click any row to view highlights and patches</p>
              </div>
              <button
                onClick={() => setShowReleases(!showReleases)}
                className="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 hover:bg-gray-200 rounded-lg transition"
              >
                {showReleases ? 'Hide Releases' : 'Show Releases'}
              </button>
            </div>

            {showReleases && (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-10"></th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        Version
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        Release Date
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        Patches
                      </th>
                    </tr>
                  </thead>
                  <tbody className="bg-white divide-y divide-gray-200">
                    {firmwareReleases.map((release) => {
                      const isExpanded = expandedReleases.has(release.version);
                      return (
                        <>
                          <tr
                            key={release.version}
                            className="hover:bg-gray-50 cursor-pointer"
                            onClick={() => {
                              const newExpanded = new Set(expandedReleases);
                              if (isExpanded) {
                                newExpanded.delete(release.version);
                              } else {
                                newExpanded.add(release.version);
                              }
                              setExpandedReleases(newExpanded);
                            }}
                          >
                            <td className="px-6 py-4 whitespace-nowrap text-gray-400">
                              <span className="text-lg">{isExpanded ? '▼' : '▶'}</span>
                            </td>
                            <td className="px-6 py-4 whitespace-nowrap text-sm font-semibold text-gray-900">
                              {release.version}
                            </td>
                            <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-600">
                              {release.releaseDate}
                            </td>
                            <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-600">
                              {release.patches.length} patch{release.patches.length !== 1 ? 'es' : ''}
                            </td>
                          </tr>
                          {isExpanded && (
                            <tr key={`${release.version}-details`}>
                              <td colSpan={4} className="px-6 py-4 bg-gray-50">
                                <div className="space-y-4">
                                  {release.highlights && release.highlights.length > 0 && (
                                    <div>
                                      <h4 className="text-sm font-semibold text-gray-900 mb-2">Highlights:</h4>
                                      <ul className="list-disc list-inside text-sm text-gray-700 space-y-1">
                                        {release.highlights.map((highlight, idx) => (
                                          <li key={idx}>{highlight}</li>
                                        ))}
                                      </ul>
                                    </div>
                                  )}
                                  {release.patches.length > 0 && (
                                    <div>
                                      <h4 className="text-sm font-semibold text-gray-900 mb-2">Patches:</h4>
                                      <div className="space-y-2">
                                        {release.patches.map((patch) => (
                                          <div key={patch.version} className="bg-white rounded p-3 border border-gray-200">
                                            <div className="flex items-center justify-between">
                                              <span className="font-mono text-xs font-medium text-gray-900">{patch.version}</span>
                                              <span className="text-xs text-gray-500">{patch.releaseDate}</span>
                                            </div>
                                            {patch.notes && (
                                              <p className="text-sm text-gray-700 mt-1">{patch.notes}</p>
                                            )}
                                          </div>
                                        ))}
                                      </div>
                                    </div>
                                  )}
                                </div>
                              </td>
                            </tr>
                          )}
                        </>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>

        {/* AP Compatibility Matrix */}
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200">
            <h2 className="text-xl font-bold text-gray-900">AP Model Compatibility</h2>
            <p className="text-sm text-gray-600 mt-1">
              Showing {filteredModels.length} AP model{filteredModels.length !== 1 ? 's' : ''}
            </p>
          </div>

          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    AP Model
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Wi-Fi Gen
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Notes
                  </th>
                  {firmwareReleases.map((release) => (
                    <th
                      key={release.version}
                      className="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider"
                    >
                      {release.version}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {filteredModels.map((ap) => (
                  <tr key={ap.model} className="hover:bg-gray-50">
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">
                      {ap.model}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-600">
                      <span className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${
                        ap.wifiGen === 'wifi7' ? 'bg-purple-100 text-purple-800' :
                        ap.wifiGen === 'wifi6e' ? 'bg-blue-100 text-blue-800' :
                        ap.wifiGen === 'wifi6' ? 'bg-green-100 text-green-800' :
                        'bg-gray-100 text-gray-800'
                      }`}>
                        {ap.wifiGen === 'wifi5' ? 'Wi-Fi 5' :
                         ap.wifiGen === 'wifi6' ? 'Wi-Fi 6' :
                         ap.wifiGen === 'wifi6e' ? 'Wi-Fi 6E' :
                         'Wi-Fi 7'}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500">
                      {ap.notes}
                    </td>
                    {firmwareReleases.map((release) => {
                      const status = getCompatibilityStatus(ap, release.version);
                      return (
                        <td key={release.version} className="px-6 py-4 text-center">
                          {status === 'initial' ? (
                            <span className="inline-flex items-center justify-center px-2 py-1 rounded text-xs font-medium bg-blue-100 text-blue-700">
                              Initial
                            </span>
                          ) : status === 'supported' ? (
                            <span className="inline-flex items-center justify-center px-2 py-1 rounded text-xs font-medium bg-green-100 text-green-700">
                              ✓
                            </span>
                          ) : status === 'final' ? (
                            <span className="inline-flex items-center justify-center px-2 py-1 rounded text-xs font-medium bg-orange-100 text-orange-700">
                              Final
                            </span>
                          ) : (
                            <span className="inline-flex items-center justify-center w-6 h-6 text-gray-300">
                              —
                            </span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Legend */}
        <div className="mt-6 bg-white rounded-lg shadow p-4">
          <h3 className="text-sm font-semibold text-gray-900 mb-3">Legend</h3>
          <div className="flex items-center gap-6 flex-wrap text-sm">
            <div className="flex items-center gap-2">
              <span className="inline-flex items-center justify-center px-2 py-1 rounded text-xs font-medium bg-blue-100 text-blue-700">
                Initial
              </span>
              <span className="text-gray-700">First supported firmware for this AP</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="inline-flex items-center justify-center px-2 py-1 rounded text-xs font-medium bg-green-100 text-green-700">
                ✓
              </span>
              <span className="text-gray-700">Fully supported</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="inline-flex items-center justify-center px-2 py-1 rounded text-xs font-medium bg-orange-100 text-orange-700">
                Final
              </span>
              <span className="text-gray-700">Last supported firmware for this AP</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="inline-flex items-center justify-center w-6 h-6 text-gray-300">
                —
              </span>
              <span className="text-gray-700">Not supported</span>
            </div>
          </div>
        </div>

        {/* Upgrade Path Planner */}
        <div className="mt-6 bg-white rounded-lg shadow overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200">
            <h2 className="text-2xl font-bold text-gray-900">Upgrade Path Planner</h2>
            <p className="text-sm text-gray-600 mt-1">
              View supported upgrade paths and analyze AP compatibility for your deployment
            </p>
          </div>

          {/* Upgrade Path Matrix */}
          <div className="border-b border-gray-200">
            <div className="px-6 py-4 bg-gray-50">
              <h3 className="text-lg font-semibold text-gray-900">Upgrade Path Matrix</h3>
              <p className="text-xs text-gray-600 mt-1">
                Quick reference showing all supported upgrade paths between firmware versions
              </p>
            </div>

            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider sticky left-0 bg-gray-50 z-10">
                      From / To
                    </th>
                    {firmwareReleases.map((release) => (
                      <th
                        key={release.version}
                        className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider"
                      >
                        <div className="flex flex-col items-center">
                          <span className="font-semibold">{release.version}</span>
                        </div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {firmwareReleases.map((fromRelease) => {
                    const fromVersion = fromRelease.version as FirmwareVersion;
                    return (
                      <tr key={fromVersion} className="hover:bg-gray-50">
                        <td className="px-4 py-3 whitespace-nowrap text-sm font-semibold text-gray-900 sticky left-0 bg-white z-10 border-r border-gray-200">
                          {fromVersion}
                        </td>
                        {firmwareReleases.map((toRelease) => {
                          const toVersion = toRelease.version as FirmwareVersion;

                          if (fromVersion === toVersion) {
                            return (
                              <td key={toVersion} className="px-4 py-3 text-center bg-gray-100">
                                <span className="text-gray-400 text-xs">—</span>
                              </td>
                            );
                          }

                          const path = getUpgradePath(fromVersion, toVersion);

                          if (!path) {
                            return (
                              <td key={toVersion} className="px-4 py-3 text-center">
                                <span className="text-gray-300 text-xs">✗</span>
                              </td>
                            );
                          }

                          return (
                            <td key={toVersion} className="px-4 py-3 text-center">
                              {path.directUpgrade ? (
                                <div className="flex flex-col items-center gap-1">
                                  {path.isRecommended ? (
                                    <span className="inline-flex items-center justify-center w-7 h-7 rounded-full bg-purple-100 text-purple-700 font-bold text-sm" title="Recommended direct upgrade">
                                      ⭐
                                    </span>
                                  ) : (
                                    <span className="inline-flex items-center justify-center w-7 h-7 rounded-full bg-green-100 text-green-700 font-bold" title="Direct upgrade supported">
                                      ✓
                                    </span>
                                  )}
                                </div>
                              ) : (
                                <div className="flex flex-col items-center gap-1">
                                  <span className="inline-flex items-center justify-center w-7 h-7 rounded-full bg-orange-100 text-orange-700 font-bold text-xs" title={`Multi-step: ${path.intermediateSteps?.join(' → ')}`}>
                                    {path.intermediateSteps?.length}
                                  </span>
                                </div>
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Matrix Legend */}
            <div className="px-6 py-4 bg-gray-50 border-t border-gray-200">
              <h4 className="text-xs font-semibold text-gray-700 mb-2">Legend</h4>
              <div className="flex items-center gap-6 flex-wrap text-xs">
                <div className="flex items-center gap-2">
                  <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-purple-100 text-purple-700 font-bold text-sm">⭐</span>
                  <span className="text-gray-600">Recommended direct upgrade</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-green-100 text-green-700 font-bold">✓</span>
                  <span className="text-gray-600">Direct upgrade supported</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-orange-100 text-orange-700 font-bold text-xs">2</span>
                  <span className="text-gray-600">Multi-step upgrade (number shows steps required)</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-gray-300 font-bold">✗</span>
                  <span className="text-gray-600">No upgrade path available</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-gray-400">—</span>
                  <span className="text-gray-600">Same version</span>
                </div>
              </div>
            </div>
          </div>

          {/* Interactive Path Analyzer */}
          <div className="px-6 py-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-2">Interactive Path Analyzer</h3>
            <p className="text-sm text-gray-600 mb-6">
              Select specific firmware versions to see detailed upgrade path and AP compatibility analysis
            </p>

          {/* Firmware Selectors */}
          <div className="grid md:grid-cols-2 gap-6 mb-6">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Current Firmware
              </label>
              <select
                value={currentFirmware || ''}
                onChange={(e) => setCurrentFirmware(e.target.value as FirmwareVersion || null)}
                className="w-full border border-gray-300 rounded-lg px-4 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              >
                <option value="">Select current version...</option>
                {firmwareReleases.map((release) => (
                  <option key={release.version} value={release.version}>
                    {release.version} ({release.releaseDate})
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Target Firmware
              </label>
              <select
                value={targetFirmware || ''}
                onChange={(e) => setTargetFirmware(e.target.value as FirmwareVersion || null)}
                className="w-full border border-gray-300 rounded-lg px-4 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              >
                <option value="">Select target version...</option>
                {firmwareReleases.map((release) => (
                  <option key={release.version} value={release.version}>
                    {release.version} ({release.releaseDate})
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Upgrade Path Results */}
          {currentFirmware && targetFirmware && currentFirmware !== targetFirmware && (() => {
            const path = getUpgradePath(currentFirmware, targetFirmware);
            const { compatible, incompatible, warnings } = getAffectedAPs(currentFirmware, targetFirmware);

            return (
              <div className="space-y-6">
                {/* Path Visualization */}
                <div className="bg-gradient-to-r from-blue-50 to-green-50 rounded-lg p-6 border-2 border-blue-200">
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">Upgrade Path</h3>

                  {path ? (
                    <div>
                      {/* Show recommended badge if applicable */}
                      {path.isRecommended && (
                        <div className="mb-3">
                          <span className="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium bg-purple-100 text-purple-800 border-2 border-purple-300">
                            ⭐ Recommended Upgrade Path
                          </span>
                        </div>
                      )}

                      {path.directUpgrade ? (
                        <div className="flex items-center gap-4 flex-wrap">
                          <div className="bg-blue-600 text-white px-4 py-2 rounded-lg font-semibold">
                            {path.from}
                          </div>
                          <div className="flex items-center gap-2">
                            <div className="h-0.5 w-12 bg-green-600"></div>
                            <span className="text-green-600 font-semibold">→</span>
                            <div className="h-0.5 w-12 bg-green-600"></div>
                          </div>
                          <div className="bg-green-600 text-white px-4 py-2 rounded-lg font-semibold">
                            {path.to}
                          </div>
                          <div className="ml-4 bg-green-100 text-green-800 px-3 py-1 rounded text-sm font-medium">
                            ✓ Direct Upgrade Supported
                          </div>
                          {path.notes && (
                            <div className="w-full mt-2">
                              <p className="text-sm text-gray-700 bg-white p-3 rounded border border-gray-200">
                                <strong>Note:</strong> {path.notes}
                              </p>
                            </div>
                          )}
                        </div>
                      ) : (
                        <div>
                          <div className="flex items-center gap-3 mb-3">
                            <div className="bg-orange-100 text-orange-800 px-3 py-1 rounded text-sm font-medium">
                              ⚠ Multi-Step Upgrade Required
                            </div>
                          </div>
                          <div className="flex items-center gap-3 flex-wrap">
                            <div className="bg-blue-600 text-white px-4 py-2 rounded-lg font-semibold">
                              {path.from}
                            </div>
                            {path.intermediateSteps?.map((step, idx) => (
                              <div key={idx} className="flex items-center gap-3">
                                <div className="flex items-center gap-2">
                                  <div className="h-0.5 w-8 bg-orange-400"></div>
                                  <span className="text-orange-600 font-semibold">→</span>
                                  <div className="h-0.5 w-8 bg-orange-400"></div>
                                </div>
                                <div className="bg-orange-500 text-white px-4 py-2 rounded-lg font-semibold">
                                  {step}
                                </div>
                              </div>
                            ))}
                            <div className="flex items-center gap-2">
                              <div className="h-0.5 w-8 bg-orange-400"></div>
                              <span className="text-orange-600 font-semibold">→</span>
                              <div className="h-0.5 w-8 bg-orange-400"></div>
                            </div>
                            <div className="bg-green-600 text-white px-4 py-2 rounded-lg font-semibold">
                              {path.to}
                            </div>
                          </div>
                          {path.notes && (
                            <p className="mt-3 text-sm text-orange-800 bg-orange-50 p-3 rounded">
                              <strong>Note:</strong> {path.notes}
                            </p>
                          )}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="bg-red-50 border-2 border-red-200 rounded-lg p-4">
                      <p className="text-red-800 font-medium">
                        ✗ No upgrade path available from {currentFirmware} to {targetFirmware}
                      </p>
                      <p className="text-red-700 text-sm mt-1">
                        This may indicate a downgrade attempt or an unsupported upgrade path.
                      </p>
                    </div>
                  )}
                </div>

                {/* AP Compatibility Summary */}
                <div className="grid md:grid-cols-3 gap-4">
                  <div className="bg-green-50 border-2 border-green-200 rounded-lg p-4">
                    <div className="flex items-center justify-between mb-2">
                      <h4 className="font-semibold text-green-900">Compatible APs</h4>
                      <span className="bg-green-600 text-white text-sm font-bold px-2 py-1 rounded">
                        {compatible.length}
                      </span>
                    </div>
                    <p className="text-sm text-green-700">
                      {compatible.length === 0
                        ? 'No APs support both versions'
                        : 'APs that support the upgrade'}
                    </p>
                  </div>

                  <div className="bg-orange-50 border-2 border-orange-200 rounded-lg p-4">
                    <div className="flex items-center justify-between mb-2">
                      <h4 className="font-semibold text-orange-900">Warnings</h4>
                      <span className="bg-orange-600 text-white text-sm font-bold px-2 py-1 rounded">
                        {warnings.length}
                      </span>
                    </div>
                    <p className="text-sm text-orange-700">
                      {warnings.length === 0
                        ? 'No warnings'
                        : 'APs reaching end of support'}
                    </p>
                  </div>

                  <div className="bg-red-50 border-2 border-red-200 rounded-lg p-4">
                    <div className="flex items-center justify-between mb-2">
                      <h4 className="font-semibold text-red-900">Incompatible APs</h4>
                      <span className="bg-red-600 text-white text-sm font-bold px-2 py-1 rounded">
                        {incompatible.length}
                      </span>
                    </div>
                    <p className="text-sm text-red-700">
                      {incompatible.length === 0
                        ? 'All APs compatible'
                        : 'APs that do not support target'}
                    </p>
                  </div>
                </div>

                {/* Detailed AP Lists */}
                {(warnings.length > 0 || incompatible.length > 0) && (
                  <div className="space-y-4">
                    {warnings.length > 0 && (
                      <div className="bg-orange-50 border border-orange-200 rounded-lg p-4">
                        <h4 className="font-semibold text-orange-900 mb-2 flex items-center gap-2">
                          <span>⚠</span>
                          <span>APs Reaching End of Support</span>
                        </h4>
                        <p className="text-sm text-orange-800 mb-3">
                          The following APs will reach their final supported firmware with this upgrade:
                        </p>
                        <div className="flex flex-wrap gap-2">
                          {warnings.map((ap) => (
                            <span key={ap.model} className="bg-orange-100 text-orange-800 px-3 py-1 rounded text-sm font-medium">
                              {ap.model}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    {incompatible.length > 0 && (
                      <div className="bg-red-50 border border-red-200 rounded-lg p-4">
                        <h4 className="font-semibold text-red-900 mb-2 flex items-center gap-2">
                          <span>✗</span>
                          <span>Incompatible AP Models</span>
                        </h4>
                        <p className="text-sm text-red-800 mb-3">
                          The following APs do not support the target firmware and will need to be replaced or remain on current version:
                        </p>
                        <div className="flex flex-wrap gap-2">
                          {incompatible.map((ap) => (
                            <span key={ap.model} className="bg-red-100 text-red-800 px-3 py-1 rounded text-sm font-medium">
                              {ap.model}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {compatible.length > 0 && (
                  <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
                    <h4 className="font-semibold text-gray-900 mb-3">
                      Compatible AP Models ({compatible.length})
                    </h4>
                    <div className="flex flex-wrap gap-2">
                      {compatible.map((ap) => (
                        <span key={ap.model} className="bg-white border border-gray-300 text-gray-700 px-3 py-1 rounded text-sm">
                          {ap.model}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
          })()}

          {currentFirmware && targetFirmware && currentFirmware === targetFirmware && (
            <div className="bg-blue-50 border-2 border-blue-200 rounded-lg p-4">
              <p className="text-blue-800">
                Current and target firmware are the same. Please select a different target version.
              </p>
            </div>
          )}

          {(!currentFirmware || !targetFirmware) && (
            <div className="bg-gray-50 border-2 border-gray-200 rounded-lg p-8 text-center">
              <p className="text-gray-600">
                Select both current and target firmware versions to see the upgrade path and compatibility analysis.
              </p>
            </div>
          )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default FirmwareMatrix;
