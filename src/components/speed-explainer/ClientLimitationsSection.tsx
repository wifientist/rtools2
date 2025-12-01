import type { ClientLimitationsData } from '@/types/speedExplainer';

interface ClientLimitationsSectionProps {
  data: ClientLimitationsData;
  viewMode: 'simple' | 'detailed';
  context: {
    scopeType: string;
    scopeName: string | null;
    timeWindow: string;
  };
}

function ClientLimitationsSection({ data, viewMode, context }: ClientLimitationsSectionProps) {
  const wifiGenMap: Record<string, { color: string; label: string; emoji: string }> = {
    'Wi-Fi 4': { color: 'red', label: '802.11n (2009)', emoji: '🔴' },
    'Wi-Fi 5': { color: 'yellow', label: '802.11ac (2014)', emoji: '🟡' },
    'Wi-Fi 6': { color: 'green', label: '802.11ax (2019)', emoji: '🟢' },
    'Wi-Fi 6E': { color: 'green', label: '802.11ax + 6GHz (2021)', emoji: '🟢' },
    'Wi-Fi 7': { color: 'blue', label: '802.11be (2024)', emoji: '🔵' }
  };

  const genInfo = wifiGenMap[data.wifiGeneration] || { color: 'gray', label: 'Unknown', emoji: '❓' };

  const streamsDisplay = data.streams === 1 ? '1x1' : data.streams === 2 ? '2x2' : data.streams === 4 ? '4x4' : `${data.streams}x${data.streams}`;

  return (
    <div className="bg-white rounded-lg shadow p-6">
      {/* Section Header */}
      <div className="flex items-center gap-3 mb-4">
        <div className="text-3xl">📱</div>
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Step 5: Client Device Limitations</h2>
          <p className="text-gray-600">Is the device itself holding back performance?</p>
        </div>
      </div>

      {/* Simple View */}
      {viewMode === 'simple' && (
        <div className="space-y-4">
          <div className={`p-4 rounded-lg border-l-4 ${
            data.isBottleneck
              ? 'bg-red-50 border-red-500'
              : 'bg-green-50 border-green-500'
          }`}>
            <p className="text-lg text-gray-800 leading-relaxed">
              {data.diagnosis}
            </p>
          </div>

          {/* Key device specs */}
          <div className="grid grid-cols-3 gap-4">
            <div className={`bg-${genInfo.color}-50 border border-${genInfo.color}-200 rounded-lg p-4`}>
              <div className="text-sm font-semibold text-gray-600 mb-1">Wi-Fi Generation</div>
              <div className="text-2xl mb-1">{genInfo.emoji}</div>
              <div className={`text-lg font-bold text-${genInfo.color}-700`}>{data.wifiGeneration}</div>
            </div>
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
              <div className="text-sm font-semibold text-gray-600 mb-1">Spatial Streams</div>
              <div className="text-2xl font-bold text-blue-700">{streamsDisplay}</div>
              <div className="text-xs text-gray-600 mt-1">Antenna configuration</div>
            </div>
            <div className="bg-purple-50 border border-purple-200 rounded-lg p-4">
              <div className="text-sm font-semibold text-gray-600 mb-1">Max Throughput</div>
              <div className="text-2xl font-bold text-purple-700">{data.maxRealisticThroughput} Mbps</div>
              <div className="text-xs text-gray-600 mt-1">Device capability</div>
            </div>
          </div>
        </div>
      )}

      {/* Detailed View */}
      {viewMode === 'detailed' && (
        <div className="space-y-6">
          {/* Article-style explanation */}
          <div className="prose max-w-none">
            <h3 className="text-lg font-semibold text-gray-900 mb-2">Why Device Specs Matter</h3>
            <p className="text-gray-700 leading-relaxed mb-3">
              Even with a perfect Wi-Fi signal and a fast AP, an older or budget device won't reach high speeds.
              Three key specs determine a client's maximum throughput:
            </p>
            <ul className="list-disc list-inside space-y-2 text-gray-700 mb-3">
              <li>
                <strong>Wi-Fi Generation:</strong> Newer standards (Wi-Fi 6, Wi-Fi 7) support faster data rates
                and more efficient encoding than older ones (Wi-Fi 4, Wi-Fi 5).
              </li>
              <li>
                <strong>Spatial Streams:</strong> A 1x1 device (common in smartphones and budget laptops) can
                only use one antenna, limiting speed. 2x2 or 4x4 devices can transmit/receive multiple data
                streams simultaneously for higher throughput.
              </li>
              <li>
                <strong>Channel Width:</strong> Wider channels (80 MHz, 160 MHz) carry more data than narrow
                ones (20 MHz, 40 MHz). Not all devices support wide channels.
              </li>
            </ul>
            <p className="text-gray-700 leading-relaxed">
              A 2017 iPhone with 2x2 Wi-Fi 5 maxes out around 600-800 Mbps (theoretical). A modern Wi-Fi 6E laptop
              with 2x2 can reach 1.2 Gbps on 80 MHz or 2.4 Gbps on 160 MHz.
            </p>
          </div>

          {/* Live device info */}
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
            <h4 className="font-semibold text-blue-900 mb-3">
              📊 Device Capabilities for {context.scopeName}
            </h4>

            {/* Wi-Fi Generation */}
            <div className="mb-4">
              <div className="text-sm text-gray-600 mb-1">Wi-Fi Generation</div>
              <div className="flex items-center gap-3">
                <div className="text-4xl">{genInfo.emoji}</div>
                <div>
                  <div className={`text-2xl font-bold text-${genInfo.color}-700`}>{data.wifiGeneration}</div>
                  <div className="text-xs text-gray-600">{genInfo.label}</div>
                </div>
              </div>
            </div>

            {/* Spatial Streams & Channel Width */}
            <div className="grid grid-cols-2 gap-4 border-t border-blue-300 pt-3">
              <div>
                <div className="text-sm text-gray-600 mb-1">Spatial Streams</div>
                <div className="text-3xl font-bold text-blue-700">{streamsDisplay}</div>
                <div className="text-xs text-gray-600 mt-1">
                  {data.streams === 1 && 'Single antenna - basic throughput'}
                  {data.streams === 2 && 'Dual antenna - good throughput'}
                  {data.streams >= 4 && 'Multi-antenna - excellent throughput'}
                </div>
              </div>
              <div>
                <div className="text-sm text-gray-600 mb-1">Max Channel Width</div>
                <div className="text-3xl font-bold text-blue-700">{data.maxWidthMhz} MHz</div>
                <div className="text-xs text-gray-600 mt-1">
                  {data.maxWidthMhz <= 20 && 'Narrow - legacy device'}
                  {data.maxWidthMhz === 40 && 'Moderate - older standard'}
                  {data.maxWidthMhz === 80 && 'Wide - modern device'}
                  {data.maxWidthMhz >= 160 && 'Very wide - high-end device'}
                </div>
              </div>
            </div>
          </div>

          {/* Max realistic throughput */}
          <div>
            <h4 className="font-semibold text-gray-900 mb-3">Maximum Realistic Throughput</h4>
            <p className="text-sm text-gray-700 mb-3">
              Based on this device's capabilities, here's the maximum speed you can expect under ideal conditions:
            </p>

            <div className="bg-purple-50 border border-purple-200 rounded-lg p-4 mb-3">
              <div className="text-center">
                <div className="text-sm text-gray-600 mb-2">Max Device Throughput</div>
                <div className="text-5xl font-bold text-purple-700 mb-2">{data.maxRealisticThroughput} Mbps</div>
                <div className="text-sm text-gray-700">
                  Calculated from: {data.wifiGeneration} • {streamsDisplay} streams • {data.maxWidthMhz} MHz width
                </div>
              </div>
            </div>

            <div className="text-sm text-gray-700 bg-gray-50 border border-gray-300 rounded p-3">
              💡 <strong>Note:</strong> This is the ceiling for this specific device. Actual speeds depend on
              signal quality, airtime, interference, and backhaul—all covered in previous sections.
            </div>
          </div>

          {/* Device profile (if available) */}
          {data.deviceProfile && (
            <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
              <h4 className="font-semibold text-gray-900 mb-2">Device Profile</h4>
              <div className="text-sm text-gray-700">{data.deviceProfile}</div>
              {data.clientMac && (
                <div className="text-xs text-gray-500 mt-2">MAC: {data.clientMac}</div>
              )}
            </div>
          )}

          {/* Comparison table */}
          <div>
            <h4 className="font-semibold text-gray-900 mb-3">Wi-Fi Generation Comparison</h4>
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm border-collapse">
                <thead>
                  <tr className="bg-gray-100 border-b">
                    <th className="text-left p-2 font-semibold text-gray-700">Generation</th>
                    <th className="text-left p-2 font-semibold text-gray-700">Standard</th>
                    <th className="text-left p-2 font-semibold text-gray-700">Year</th>
                    <th className="text-left p-2 font-semibold text-gray-700">Typical Max</th>
                  </tr>
                </thead>
                <tbody>
                  <tr className={`border-b ${data.wifiGeneration === 'Wi-Fi 4' ? 'bg-red-50 font-bold' : ''}`}>
                    <td className="p-2">🔴 Wi-Fi 4</td>
                    <td className="p-2">802.11n</td>
                    <td className="p-2">2009</td>
                    <td className="p-2">150-600 Mbps</td>
                  </tr>
                  <tr className={`border-b ${data.wifiGeneration === 'Wi-Fi 5' ? 'bg-yellow-50 font-bold' : ''}`}>
                    <td className="p-2">🟡 Wi-Fi 5</td>
                    <td className="p-2">802.11ac</td>
                    <td className="p-2">2014</td>
                    <td className="p-2">433-1733 Mbps</td>
                  </tr>
                  <tr className={`border-b ${data.wifiGeneration === 'Wi-Fi 6' ? 'bg-green-50 font-bold' : ''}`}>
                    <td className="p-2">🟢 Wi-Fi 6</td>
                    <td className="p-2">802.11ax</td>
                    <td className="p-2">2019</td>
                    <td className="p-2">600-2400 Mbps</td>
                  </tr>
                  <tr className={`border-b ${data.wifiGeneration === 'Wi-Fi 6E' ? 'bg-green-50 font-bold' : ''}`}>
                    <td className="p-2">🟢 Wi-Fi 6E</td>
                    <td className="p-2">802.11ax + 6GHz</td>
                    <td className="p-2">2021</td>
                    <td className="p-2">600-2400 Mbps</td>
                  </tr>
                  <tr className={`${data.wifiGeneration === 'Wi-Fi 7' ? 'bg-blue-50 font-bold' : ''}`}>
                    <td className="p-2">🔵 Wi-Fi 7</td>
                    <td className="p-2">802.11be</td>
                    <td className="p-2">2024</td>
                    <td className="p-2">1.4-5.8 Gbps</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          {/* Additional notes */}
          {data.notes && (
            <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3">
              <div className="font-semibold text-yellow-900 mb-1">Additional Notes</div>
              <div className="text-sm text-yellow-800">{data.notes}</div>
            </div>
          )}

          {/* Diagnosis */}
          <div className={`p-4 rounded-lg border-l-4 ${
            data.isBottleneck
              ? 'bg-red-50 border-red-500'
              : 'bg-green-50 border-green-500'
          }`}>
            <h4 className={`font-semibold mb-2 ${
              data.isBottleneck ? 'text-red-900' : 'text-green-900'
            }`}>
              {data.isBottleneck ? '⚠ Diagnosis: Device Is a Bottleneck' : '✓ Diagnosis: Device Has Modern Capabilities'}
            </h4>
            <p className="text-gray-800 leading-relaxed">
              {data.diagnosis}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export default ClientLimitationsSection;
