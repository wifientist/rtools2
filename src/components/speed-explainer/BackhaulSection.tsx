import type { BackhaulData } from '@/types/speedExplainer';

interface BackhaulSectionProps {
  data: BackhaulData;
  viewMode: 'simple' | 'detailed';
  context: {
    scopeType: string;
    scopeName: string | null;
    timeWindow: string;
  };
}

function BackhaulSection({ data, viewMode, context }: BackhaulSectionProps) {
  const utilizationPercent = (data.wanUsagePeak / data.wanCapacityEstimate) * 100;
  const utilizationColor = utilizationPercent < 60 ? 'green' : utilizationPercent < 80 ? 'yellow' : 'red';

  const uplinkTypeDisplay: Record<string, { label: string; icon: string }> = {
    fiber: { label: 'Fiber', icon: '🚀' },
    copper: { label: 'Copper/Cable', icon: '🔌' },
    wireless: { label: 'Wireless', icon: '📡' },
    unknown: { label: 'Unknown', icon: '❓' }
  };

  const uplinkInfo = uplinkTypeDisplay[data.uplinkType] || uplinkTypeDisplay.unknown;

  return (
    <div className="bg-white rounded-lg shadow p-6">
      {/* Section Header */}
      <div className="flex items-center gap-3 mb-4">
        <div className="text-3xl">🌐</div>
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Step 4: Backhaul & Uplink</h2>
          <p className="text-gray-600">Is your internet connection or network uplink the bottleneck?</p>
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

          {/* Key metrics */}
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
              <div className="text-sm font-semibold text-gray-600 mb-1">WAN Capacity</div>
              <div className="text-2xl font-bold text-blue-700">{data.wanCapacityEstimate} Mbps</div>
              <div className="text-xs text-gray-600 mt-1">Estimated available bandwidth</div>
            </div>
            <div className={`bg-${utilizationColor}-50 border border-${utilizationColor}-200 rounded-lg p-4`}>
              <div className="text-sm font-semibold text-gray-600 mb-1">Peak Usage</div>
              <div className={`text-2xl font-bold text-${utilizationColor}-700`}>{data.wanUsagePeak} Mbps</div>
              <div className="text-xs text-gray-600 mt-1">{utilizationPercent.toFixed(0)}% of capacity</div>
            </div>
            <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
              <div className="text-sm font-semibold text-gray-600 mb-1">Uplink Type</div>
              <div className="text-2xl font-bold text-gray-700">{uplinkInfo.icon}</div>
              <div className="text-xs text-gray-600 mt-1">{uplinkInfo.label}</div>
            </div>
          </div>
        </div>
      )}

      {/* Detailed View */}
      {viewMode === 'detailed' && (
        <div className="space-y-6">
          {/* Article-style explanation */}
          <div className="prose max-w-none">
            <h3 className="text-lg font-semibold text-gray-900 mb-2">What Is Backhaul?</h3>
            <p className="text-gray-700 leading-relaxed mb-3">
              Your Wi-Fi might be fast, but if your internet connection or network uplink can't keep up,
              you'll still see slow speeds. <strong>Backhaul</strong> refers to the connection between your
              access point and the wider internet (or corporate network).
            </p>
            <p className="text-gray-700 leading-relaxed mb-3">
              For example, if you have a gigabit-capable Wi-Fi 6 AP, but it's connected to a 100 Mbps cable
              internet plan, your speed test will max out at ~100 Mbps—no matter how perfect your Wi-Fi
              signal is.
            </p>
            <p className="text-gray-700 leading-relaxed">
              Backhaul can also be saturated if many clients are competing for the same internet pipe. An AP
              with 50 active clients sharing a 300 Mbps uplink gives each client an average of only 6 Mbps.
            </p>
          </div>

          {/* Live metrics */}
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
            <h4 className="font-semibold text-blue-900 mb-3">
              📊 Live Metrics for {context.scopeName}
            </h4>

            {/* WAN Capacity & Usage */}
            <div className="grid grid-cols-2 gap-4 mb-4">
              <div>
                <div className="text-sm text-gray-600 mb-1">WAN Capacity (Estimate)</div>
                <div className="text-3xl font-bold text-blue-700">{data.wanCapacityEstimate} Mbps</div>
                <div className="text-xs text-gray-600 mt-1">Maximum available bandwidth</div>
              </div>
              <div>
                <div className="text-sm text-gray-600 mb-1">Peak Usage</div>
                <div className={`text-3xl font-bold text-${utilizationColor}-700`}>{data.wanUsagePeak} Mbps</div>
                <div className="text-xs text-gray-600 mt-1">Highest observed in time window</div>
              </div>
            </div>

            {/* Utilization gauge */}
            <div className="border-t border-blue-300 pt-3 mb-4">
              <div className="text-sm text-gray-600 mb-2">Peak Utilization</div>
              <div className="relative bg-gray-200 rounded-full h-8">
                <div
                  className={`bg-${utilizationColor}-500 h-8 rounded-full transition-all duration-500 flex items-center justify-center`}
                  style={{ width: `${Math.min(utilizationPercent, 100)}%` }}
                >
                  <span className="text-sm font-bold text-white">{utilizationPercent.toFixed(0)}%</span>
                </div>
              </div>
              <div className="text-xs text-gray-600 mt-1">
                {utilizationPercent < 60 && '✓ Plenty of headroom'}
                {utilizationPercent >= 60 && utilizationPercent < 80 && '⚠ Approaching capacity'}
                {utilizationPercent >= 80 && '✗ Near or at capacity - likely bottleneck'}
              </div>
            </div>

            {/* Average usage */}
            <div className="border-t border-blue-300 pt-3">
              <div className="text-sm text-gray-600 mb-1">Average Usage</div>
              <div className="text-xl font-bold text-blue-900">{data.wanUsageAvg} Mbps</div>
              <div className="text-xs text-gray-600 mt-1">
                Mean throughput over {context.timeWindow}
              </div>
            </div>
          </div>

          {/* Uplink details */}
          <div>
            <h4 className="font-semibold text-gray-900 mb-3">Uplink Details</h4>

            <div className="grid grid-cols-2 gap-4">
              {/* Uplink Type */}
              <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
                <div className="flex items-center gap-2 mb-2">
                  <div className="text-3xl">{uplinkInfo.icon}</div>
                  <div>
                    <div className="text-sm font-semibold text-gray-600">Connection Type</div>
                    <div className="text-lg font-bold text-gray-900">{uplinkInfo.label}</div>
                  </div>
                </div>
                <div className="text-xs text-gray-600 mt-2">
                  {data.uplinkType === 'fiber' && 'Fiber-optic connections typically offer the best bandwidth and latency.'}
                  {data.uplinkType === 'copper' && 'Cable or DSL connections can vary in performance based on ISP and plan.'}
                  {data.uplinkType === 'wireless' && 'Wireless uplinks (LTE/5G) may have variable bandwidth and higher latency.'}
                  {data.uplinkType === 'unknown' && 'Uplink type could not be determined.'}
                </div>
              </div>

              {/* Uplink Speed */}
              <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
                <div className="text-sm font-semibold text-gray-600 mb-1">Uplink Interface Speed</div>
                <div className="text-3xl font-bold text-gray-900">{data.uplinkSpeed} Mbps</div>
                <div className="text-xs text-gray-600 mt-2">
                  The physical port speed (e.g., 1 Gbps Ethernet). Actual throughput depends on your ISP plan.
                </div>
              </div>
            </div>
          </div>

          {/* Visual comparison */}
          <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
            <h4 className="font-semibold text-gray-900 mb-3">Capacity vs. Demand</h4>
            <p className="text-sm text-gray-700 mb-3">
              If your peak usage is close to your capacity, the backhaul is likely saturated during busy times.
            </p>

            {/* Bar chart comparison */}
            <div className="space-y-3">
              <div>
                <div className="text-xs font-semibold text-gray-600 mb-1">WAN Capacity</div>
                <div className="relative bg-gray-200 rounded h-8">
                  <div
                    className="bg-blue-500 h-8 rounded flex items-center justify-start pl-3"
                    style={{ width: '100%' }}
                  >
                    <span className="text-sm font-bold text-white">{data.wanCapacityEstimate} Mbps</span>
                  </div>
                </div>
              </div>
              <div>
                <div className="text-xs font-semibold text-gray-600 mb-1">Peak Usage</div>
                <div className="relative bg-gray-200 rounded h-8">
                  <div
                    className={`bg-${utilizationColor}-500 h-8 rounded flex items-center justify-start pl-3`}
                    style={{ width: `${Math.min(utilizationPercent, 100)}%` }}
                  >
                    <span className="text-sm font-bold text-white">{data.wanUsagePeak} Mbps</span>
                  </div>
                </div>
              </div>
              <div>
                <div className="text-xs font-semibold text-gray-600 mb-1">Average Usage</div>
                <div className="relative bg-gray-200 rounded h-8">
                  <div
                    className="bg-green-500 h-8 rounded flex items-center justify-start pl-3"
                    style={{ width: `${Math.min((data.wanUsageAvg / data.wanCapacityEstimate) * 100, 100)}%` }}
                  >
                    <span className="text-sm font-bold text-white">{data.wanUsageAvg} Mbps</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Site/AP context */}
          {(data.apMac || data.siteId) && (
            <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 text-sm">
              <div className="font-semibold text-gray-700 mb-1">Context</div>
              {data.apMac && (
                <div className="text-gray-600">AP: {data.apMac}</div>
              )}
              {data.siteId && (
                <div className="text-gray-600">Site: {data.siteId}</div>
              )}
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
              {data.isBottleneck ? '⚠ Diagnosis: Backhaul Is a Bottleneck' : '✓ Diagnosis: Backhaul Has Capacity'}
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

export default BackhaulSection;
