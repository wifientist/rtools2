import type { StickyClientsData, ViewMode } from '@/types/roamingExplainer';

interface Props {
  data: StickyClientsData;
  viewMode: ViewMode;
}

function formatDuration(minutes: number): string {
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 1440) return `${Math.round(minutes / 60)}h`;
  return `${Math.round(minutes / 1440)}d`;
}

function getRssiColor(rssi: number): string {
  if (rssi >= -50) return 'text-green-600';
  if (rssi >= -60) return 'text-green-500';
  if (rssi >= -70) return 'text-yellow-600';
  if (rssi >= -80) return 'text-orange-600';
  return 'text-red-600';
}

function getRssiBgColor(rssi: number): string {
  if (rssi >= -50) return 'bg-green-100';
  if (rssi >= -60) return 'bg-green-50';
  if (rssi >= -70) return 'bg-yellow-50';
  if (rssi >= -80) return 'bg-orange-50';
  return 'bg-red-50';
}

export default function StickyClientsSection({ data, viewMode }: Props) {
  return (
    <div className="bg-white rounded-lg shadow p-6">
      <h3 className="text-xl font-semibold mb-4 flex items-center gap-2">
        <span className="text-2xl">🧲</span>
        Sticky Clients
      </h3>

      {/* Explanation */}
      <div className="bg-orange-50 border border-orange-200 rounded-lg p-4 mb-6">
        <p className="text-gray-700 leading-relaxed">
          <strong>Sticky clients</strong> are devices that refuse to roam to a better AP, even when one is
          available nearby. This happens because <strong className="text-orange-700">clients control roaming decisions</strong>,
          and many devices (especially IoT) have very conservative roaming thresholds—sometimes staying
          connected until signal is nearly unusable.
        </p>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-6">
        <div className={`rounded-lg p-4 text-center ${data.totalStickyClients > 10 ? 'bg-red-50' : data.totalStickyClients > 5 ? 'bg-yellow-50' : 'bg-green-50'}`}>
          <div className={`text-3xl font-bold ${data.totalStickyClients > 10 ? 'text-red-600' : data.totalStickyClients > 5 ? 'text-yellow-600' : 'text-green-600'}`}>
            {data.totalStickyClients}
          </div>
          <div className="text-sm text-gray-600">Sticky Clients</div>
        </div>
        {data.commonDeviceTypes.slice(0, 2).map((deviceType, idx) => (
          <div key={idx} className="bg-gray-50 rounded-lg p-4 text-center">
            <div className="text-2xl font-bold text-gray-700">{deviceType.count}</div>
            <div className="text-sm text-gray-600">{deviceType.type}</div>
          </div>
        ))}
      </div>

      {/* Worst Offenders */}
      {data.worstOffenders.length > 0 && (
        <div className="mb-6">
          <h4 className="font-semibold mb-3 flex items-center gap-2">
            <span>⚠️</span> Worst Offenders
          </h4>
          <div className="space-y-3">
            {data.worstOffenders.map((client, idx) => (
              <div key={idx} className={`rounded-lg border p-4 ${getRssiBgColor(client.currentRssi)}`}>
                <div className="flex flex-wrap items-start justify-between gap-2 mb-2">
                  <div>
                    <div className="font-semibold">{client.clientName || client.clientMac}</div>
                    <div className="text-sm text-gray-600">{client.clientType}</div>
                  </div>
                  <div className="text-sm text-gray-500">
                    Stuck for <span className="font-semibold">{formatDuration(client.stuckDurationMinutes)}</span>
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-3">
                  {/* Current Connection */}
                  <div className="bg-white/60 rounded p-3">
                    <div className="text-xs text-gray-500 uppercase mb-1">Connected To</div>
                    <div className="font-medium">{client.connectedApName}</div>
                    {client.connectedApLocation && (
                      <div className="text-sm text-gray-600">{client.connectedApLocation}</div>
                    )}
                    <div className={`text-lg font-bold ${getRssiColor(client.currentRssi)}`}>
                      {client.currentRssi} dBm
                    </div>
                  </div>

                  {/* Better Option */}
                  {client.betterApName && (
                    <div className="bg-green-100/60 rounded p-3 border border-green-200">
                      <div className="text-xs text-gray-500 uppercase mb-1">Should Be On</div>
                      <div className="font-medium text-green-800">{client.betterApName}</div>
                      <div className={`text-lg font-bold ${getRssiColor(client.betterApRssi || -100)}`}>
                        {client.betterApRssi} dBm
                      </div>
                      <div className="text-sm text-green-700">
                        +{Math.abs((client.betterApRssi || 0) - client.currentRssi)} dB better!
                      </div>
                    </div>
                  )}
                </div>

                {viewMode === 'detailed' && (
                  <div className="mt-3 pt-3 border-t border-gray-200 text-sm text-gray-600">
                    <span className="font-mono">{client.clientMac}</span>
                    {client.lastRoamAttempt && (
                      <span className="ml-4">
                        Last roam attempt: {new Date(client.lastRoamAttempt).toLocaleDateString()}
                      </span>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Device Types Breakdown */}
      {viewMode === 'detailed' && data.commonDeviceTypes.length > 0 && (
        <div className="mb-6 border-t pt-4">
          <h4 className="font-semibold mb-3">Sticky by Device Type</h4>
          <div className="flex flex-wrap gap-2">
            {data.commonDeviceTypes.map((deviceType, idx) => (
              <div key={idx} className="bg-gray-100 px-3 py-1 rounded-full text-sm">
                {deviceType.type}: <span className="font-semibold">{deviceType.count}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Recommendations */}
      {data.recommendations.length > 0 && (
        <div className="border-t pt-4">
          <h4 className="font-semibold mb-3 flex items-center gap-2">
            <span>💡</span> Recommendations
          </h4>
          <ul className="space-y-2">
            {data.recommendations.map((rec, idx) => (
              <li key={idx} className="flex items-start gap-2 text-gray-700">
                <span className="text-blue-500 mt-1">•</span>
                {rec}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
