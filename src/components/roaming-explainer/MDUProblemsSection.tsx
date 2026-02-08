import type { MduProblemsData, ViewMode } from '@/types/roamingExplainer';

interface Props {
  data: MduProblemsData;
  viewMode: ViewMode;
}

function getSeverityConfig(severity: string): { color: string; bgColor: string; icon: string } {
  switch (severity) {
    case 'high':
      return { color: 'text-red-700', bgColor: 'bg-red-50 border-red-200', icon: '🔴' };
    case 'medium':
      return { color: 'text-yellow-700', bgColor: 'bg-yellow-50 border-yellow-200', icon: '🟡' };
    case 'low':
      return { color: 'text-blue-700', bgColor: 'bg-blue-50 border-blue-200', icon: '🔵' };
    default:
      return { color: 'text-gray-700', bgColor: 'bg-gray-50 border-gray-200', icon: '⚪' };
  }
}

function getIssueIcon(issue: string): string {
  switch (issue) {
    case 'overlap': return '📶';
    case 'gap': return '📵';
    case 'interference': return '⚡';
    case 'floor_bleed': return '🏢';
    default: return '❓';
  }
}

export default function MDUProblemsSection({ data, viewMode }: Props) {
  const { metrics } = data;

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <h3 className="text-xl font-semibold mb-4 flex items-center gap-2">
        <span className="text-2xl">🏢</span>
        MDU-Specific Issues
      </h3>

      {/* Explanation */}
      <div className="bg-purple-50 border border-purple-200 rounded-lg p-4 mb-6">
        <p className="text-gray-700 leading-relaxed">
          <strong>Multi-Dwelling Units</strong> (apartments, condos, hotels) present unique roaming challenges.
          Dense AP deployment, thin walls, and vertical signal bleed create situations where clients can
          "see" many APs but connect to the wrong one. Common issues include <strong className="text-purple-700">hallway huggers</strong> (clients
          sticking to hallway APs from inside units) and <strong className="text-purple-700">floor bleeders</strong> (clients connecting
          to APs on different floors).
        </p>
      </div>

      {/* Metrics Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div className="bg-gray-50 rounded-lg p-4 text-center">
          <div className="text-2xl font-bold text-gray-900">{metrics.totalUnits}</div>
          <div className="text-sm text-gray-600">Total Units</div>
        </div>
        <div className="bg-gray-50 rounded-lg p-4 text-center">
          <div className="text-2xl font-bold text-gray-900">{metrics.totalAps}</div>
          <div className="text-sm text-gray-600">Total APs</div>
        </div>
        <div className={`rounded-lg p-4 text-center ${metrics.hallwayHuggerClients > 20 ? 'bg-red-50' : metrics.hallwayHuggerClients > 10 ? 'bg-yellow-50' : 'bg-green-50'}`}>
          <div className={`text-2xl font-bold ${metrics.hallwayHuggerClients > 20 ? 'text-red-600' : metrics.hallwayHuggerClients > 10 ? 'text-yellow-600' : 'text-green-600'}`}>
            {metrics.hallwayHuggerClients}
          </div>
          <div className="text-sm text-gray-600">Hallway Huggers</div>
        </div>
        <div className={`rounded-lg p-4 text-center ${metrics.floorBleedIssues > 10 ? 'bg-red-50' : metrics.floorBleedIssues > 5 ? 'bg-yellow-50' : 'bg-green-50'}`}>
          <div className={`text-2xl font-bold ${metrics.floorBleedIssues > 10 ? 'text-red-600' : metrics.floorBleedIssues > 5 ? 'text-yellow-600' : 'text-green-600'}`}>
            {metrics.floorBleedIssues}
          </div>
          <div className="text-sm text-gray-600">Floor Bleed Issues</div>
        </div>
      </div>

      {/* Detailed Metrics */}
      {viewMode === 'detailed' && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6 border-t pt-4">
          <div className="text-center">
            <div className="text-lg font-semibold text-gray-700">{metrics.hallwayAps}</div>
            <div className="text-xs text-gray-500">Hallway APs</div>
          </div>
          <div className="text-center">
            <div className="text-lg font-semibold text-gray-700">{metrics.inUnitAps}</div>
            <div className="text-xs text-gray-500">In-Unit APs</div>
          </div>
          <div className="text-center">
            <div className="text-lg font-semibold text-gray-700">{metrics.avgApsPerFloor}</div>
            <div className="text-xs text-gray-500">Avg APs/Floor</div>
          </div>
          <div className="text-center">
            <div className={`text-lg font-semibold ${metrics.avgOverlapPercent > 30 ? 'text-orange-600' : 'text-green-600'}`}>
              {metrics.avgOverlapPercent}%
            </div>
            <div className="text-xs text-gray-500">Avg Overlap</div>
          </div>
        </div>
      )}

      {/* Common MDU Issues Explained */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        <div className="bg-orange-50 border border-orange-200 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-xl">🚪</span>
            <h4 className="font-semibold text-orange-800">Hallway Hugger</h4>
          </div>
          <p className="text-sm text-orange-700">
            Client enters their unit but stays connected to the hallway AP they walked past.
            Signal may be -70 to -80 dBm while in-unit AP is at -40 dBm nearby.
          </p>
          {metrics.hallwayHuggerClients > 0 && (
            <div className="mt-2 text-orange-800 font-semibold">
              {metrics.hallwayHuggerClients} clients affected
            </div>
          )}
        </div>
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-xl">🏗️</span>
            <h4 className="font-semibold text-blue-800">Floor Bleeder</h4>
          </div>
          <p className="text-sm text-blue-700">
            Wood-frame or thin concrete construction allows strong signal from APs on floors
            above or below. Clients may connect to AP in unit 201 while sitting in unit 101.
          </p>
          {metrics.floorBleedIssues > 0 && (
            <div className="mt-2 text-blue-800 font-semibold">
              {metrics.floorBleedIssues} areas affected
            </div>
          )}
        </div>
      </div>

      {/* Problem Areas */}
      {data.problemAreas.length > 0 && (
        <div className="border-t pt-4">
          <h4 className="font-semibold mb-3 flex items-center gap-2">
            <span>📍</span> Identified Problem Areas
          </h4>
          <div className="space-y-3">
            {data.problemAreas.map((area, idx) => {
              const config = getSeverityConfig(area.severity);
              return (
                <div key={idx} className={`rounded-lg border p-4 ${config.bgColor}`}>
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <div className="flex items-center gap-2">
                      <span>{getIssueIcon(area.issue)}</span>
                      <span className="font-semibold">{area.location}</span>
                    </div>
                    <span className={`text-xs uppercase font-semibold px-2 py-1 rounded ${config.color}`}>
                      {config.icon} {area.severity}
                    </span>
                  </div>
                  <p className={`text-sm ${config.color}`}>{area.description}</p>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Coverage Map Hint */}
      {viewMode === 'detailed' && data.coverageMap.length > 0 && (
        <div className="border-t pt-4 mt-4">
          <h4 className="font-semibold mb-3 flex items-center gap-2">
            <span>📶</span> AP Coverage Overview
          </h4>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-100">
                <tr>
                  <th className="px-3 py-2 text-left">AP Name</th>
                  <th className="px-3 py-2 text-left">Location</th>
                  <th className="px-3 py-2 text-left">Channel</th>
                  <th className="px-3 py-2 text-left">Clients</th>
                  <th className="px-3 py-2 text-left">Overlap</th>
                </tr>
              </thead>
              <tbody>
                {data.coverageMap.map((ap, idx) => (
                  <tr key={idx} className={`border-b ${ap.isOverlapping ? 'bg-yellow-50' : ''}`}>
                    <td className="px-3 py-2 font-medium">{ap.apName}</td>
                    <td className="px-3 py-2 text-gray-600">{ap.location}</td>
                    <td className="px-3 py-2">{ap.channel} ({ap.band})</td>
                    <td className="px-3 py-2">{ap.clientCount}</td>
                    <td className="px-3 py-2">
                      {ap.isOverlapping ? (
                        <span className="text-yellow-600">⚠️ {ap.overlappingAps?.length || 0} APs</span>
                      ) : (
                        <span className="text-green-600">✓ OK</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
