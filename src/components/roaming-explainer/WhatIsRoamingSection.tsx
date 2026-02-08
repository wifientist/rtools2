import type { WhatIsRoamingData, ViewMode } from '@/types/roamingExplainer';

interface Props {
  data: WhatIsRoamingData;
  viewMode: ViewMode;
}

function formatTime(ms: number): string {
  if (ms < 100) return `${ms}ms (excellent)`;
  if (ms < 200) return `${ms}ms (good)`;
  if (ms < 400) return `${ms}ms (noticeable)`;
  return `${ms}ms (disruptive)`;
}

function getRoamTypeIcon(type: string): string {
  switch (type) {
    case 'fast': return '⚡';
    case 'full': return '🔄';
    case 'failed': return '❌';
    default: return '❓';
  }
}

export default function WhatIsRoamingSection({ data, viewMode }: Props) {
  const successRate = data.totalRoamEvents24h > 0
    ? Math.round((data.successfulRoams / data.totalRoamEvents24h) * 100)
    : 0;

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <h3 className="text-xl font-semibold mb-4 flex items-center gap-2">
        <span className="text-2xl">📡</span>
        What Is Roaming?
      </h3>

      {/* Simple explanation */}
      <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6">
        <p className="text-gray-700 leading-relaxed">
          <strong>Roaming</strong> is when your device switches from one access point (AP) to another
          while maintaining your connection. The key insight: <strong className="text-blue-700">your device
          decides when to roam, not the network</strong>. This is why some devices "stick" to distant APs
          even when a closer one is available.
        </p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div className="bg-gray-50 rounded-lg p-4 text-center">
          <div className="text-3xl font-bold text-gray-900">{data.totalRoamEvents24h}</div>
          <div className="text-sm text-gray-600">Roam Events (24h)</div>
        </div>
        <div className="bg-gray-50 rounded-lg p-4 text-center">
          <div className={`text-3xl font-bold ${successRate >= 90 ? 'text-green-600' : successRate >= 70 ? 'text-yellow-600' : 'text-red-600'}`}>
            {successRate}%
          </div>
          <div className="text-sm text-gray-600">Success Rate</div>
        </div>
        <div className="bg-gray-50 rounded-lg p-4 text-center">
          <div className={`text-3xl font-bold ${data.avgRoamTimeMs < 100 ? 'text-green-600' : data.avgRoamTimeMs < 300 ? 'text-yellow-600' : 'text-red-600'}`}>
            {data.avgRoamTimeMs}ms
          </div>
          <div className="text-sm text-gray-600">Avg Roam Time</div>
        </div>
        <div className="bg-gray-50 rounded-lg p-4 text-center">
          <div className={`text-3xl font-bold ${data.fastRoamPercent >= 80 ? 'text-green-600' : data.fastRoamPercent >= 40 ? 'text-yellow-600' : 'text-red-600'}`}>
            {data.fastRoamPercent}%
          </div>
          <div className="text-sm text-gray-600">Fast Roams (802.11r)</div>
        </div>
      </div>

      {/* Detailed view: Roam types explanation */}
      {viewMode === 'detailed' && (
        <div className="border-t pt-4 mb-6">
          <h4 className="font-semibold mb-3">Roam Types Explained</h4>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="bg-green-50 border border-green-200 rounded-lg p-3">
              <div className="flex items-center gap-2 mb-1">
                <span>⚡</span>
                <span className="font-semibold text-green-800">Fast Roam (802.11r)</span>
              </div>
              <p className="text-sm text-green-700">
                Pre-negotiated authentication. Takes 20-50ms. VoIP and video calls stay connected.
              </p>
            </div>
            <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3">
              <div className="flex items-center gap-2 mb-1">
                <span>🔄</span>
                <span className="font-semibold text-yellow-800">Full Roam</span>
              </div>
              <p className="text-sm text-yellow-700">
                Complete re-authentication. Takes 200-500ms. May drop active calls or streams.
              </p>
            </div>
            <div className="bg-red-50 border border-red-200 rounded-lg p-3">
              <div className="flex items-center gap-2 mb-1">
                <span>❌</span>
                <span className="font-semibold text-red-800">Failed Roam</span>
              </div>
              <p className="text-sm text-red-700">
                Client tried to roam but couldn't. May reconnect to original or disconnect entirely.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Recent Events */}
      {data.recentEvents.length > 0 && (
        <div className="border-t pt-4">
          <h4 className="font-semibold mb-3">Recent Roaming Events</h4>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-100">
                <tr>
                  <th className="px-3 py-2 text-left">Time</th>
                  <th className="px-3 py-2 text-left">Client</th>
                  <th className="px-3 py-2 text-left">From → To</th>
                  <th className="px-3 py-2 text-left">Type</th>
                  <th className="px-3 py-2 text-left">Duration</th>
                  {viewMode === 'detailed' && <th className="px-3 py-2 text-left">RSSI</th>}
                </tr>
              </thead>
              <tbody>
                {data.recentEvents.map((event, idx) => (
                  <tr key={idx} className="border-b">
                    <td className="px-3 py-2 text-gray-500">
                      {new Date(event.timestamp).toLocaleTimeString()}
                    </td>
                    <td className="px-3 py-2">
                      {event.clientName || event.clientMac.slice(-8)}
                    </td>
                    <td className="px-3 py-2">
                      <span className="text-gray-600">{event.fromApName}</span>
                      <span className="mx-2">→</span>
                      <span className="font-medium">{event.toApName}</span>
                    </td>
                    <td className="px-3 py-2">
                      {getRoamTypeIcon(event.roamType)} {event.roamType}
                    </td>
                    <td className="px-3 py-2">
                      {formatTime(event.roamTimeMs)}
                    </td>
                    {viewMode === 'detailed' && (
                      <td className="px-3 py-2 text-gray-600">{event.rssiAtRoam} dBm</td>
                    )}
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
