import type { InterferenceData } from '@/types/speedExplainer';

interface InterferenceSectionProps {
  data: InterferenceData;
  viewMode: 'simple' | 'detailed';
  context: {
    scopeType: string;
    scopeName: string | null;
    timeWindow: string;
  };
}

function InterferenceSection({ data, viewMode, context }: InterferenceSectionProps) {
  const retryColor = data.retryRate < 10 ? 'green' : data.retryRate < 20 ? 'yellow' : 'red';
  const failureColor = data.failureRate < 5 ? 'green' : data.failureRate < 10 ? 'yellow' : 'red';
  const neighborColor = data.neighborApCount < 5 ? 'green' : data.neighborApCount < 10 ? 'yellow' : 'red';

  return (
    <div className="bg-white rounded-lg shadow p-6">
      {/* Section Header */}
      <div className="flex items-center gap-3 mb-4">
        <div className="text-3xl">📡</div>
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Step 3: Interference & Contention</h2>
          <p className="text-gray-600">Are there too many devices competing for airtime?</p>
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
            <div className={`bg-${retryColor}-50 border border-${retryColor}-200 rounded-lg p-4`}>
              <div className="text-sm font-semibold text-gray-600 mb-1">Retry Rate</div>
              <div className={`text-2xl font-bold text-${retryColor}-700`}>{data.retryRate}%</div>
              <div className="text-xs text-gray-600 mt-1">
                {data.retryRate < 10 ? 'Low' : data.retryRate < 20 ? 'Moderate' : 'High'}
              </div>
            </div>
            <div className={`bg-${failureColor}-50 border border-${failureColor}-200 rounded-lg p-4`}>
              <div className="text-sm font-semibold text-gray-600 mb-1">Failure Rate</div>
              <div className={`text-2xl font-bold text-${failureColor}-700`}>{data.failureRate}%</div>
              <div className="text-xs text-gray-600 mt-1">
                {data.failureRate < 5 ? 'Low' : data.failureRate < 10 ? 'Moderate' : 'High'}
              </div>
            </div>
            <div className={`bg-${neighborColor}-50 border border-${neighborColor}-200 rounded-lg p-4`}>
              <div className="text-sm font-semibold text-gray-600 mb-1">Neighbor APs</div>
              <div className={`text-2xl font-bold text-${neighborColor}-700`}>{data.neighborApCount}</div>
              <div className="text-xs text-gray-600 mt-1">
                {data.neighborApCount < 5 ? 'Clean' : data.neighborApCount < 10 ? 'Crowded' : 'Very crowded'}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Detailed View */}
      {viewMode === 'detailed' && (
        <div className="space-y-6">
          {/* Article-style explanation */}
          <div className="prose max-w-none">
            <h3 className="text-lg font-semibold text-gray-900 mb-2">What Is Interference?</h3>
            <p className="text-gray-700 leading-relaxed mb-3">
              Wi-Fi operates on unlicensed radio spectrum, which means anyone can use it—your neighbors' APs,
              microwave ovens, Bluetooth devices, baby monitors, and more. When multiple devices transmit on
              overlapping channels, they interfere with each other.
            </p>
            <p className="text-gray-700 leading-relaxed mb-3">
              The tell-tale sign of interference is a high <strong>retry rate</strong>: when packets fail to transmit
              cleanly on the first attempt, they must be resent. This wastes airtime and lowers throughput.
              A retry rate above 15-20% is a red flag.
            </p>
            <p className="text-gray-700 leading-relaxed">
              <strong>Contention</strong> happens even without interference: if too many devices are trying to talk
              at once on the same channel, they must "wait their turn" using Wi-Fi's collision avoidance protocol.
              More neighbors = more waiting.
            </p>
          </div>

          {/* Live metrics */}
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
            <h4 className="font-semibold text-blue-900 mb-3">
              📊 Live Metrics for {context.scopeName}
            </h4>

            {/* Retry and Failure Rates */}
            <div className="grid grid-cols-2 gap-4 mb-4">
              <div>
                <div className="text-sm text-gray-600 mb-1">Retry Rate</div>
                <div className={`text-3xl font-bold text-${retryColor}-700`}>{data.retryRate}%</div>
                <div className="text-xs text-gray-600 mt-1">
                  {data.retryRate < 10 && '✓ Low - Clean transmission'}
                  {data.retryRate >= 10 && data.retryRate < 20 && '⚠ Moderate - Some retries'}
                  {data.retryRate >= 20 && '✗ High - Significant packet loss'}
                </div>
                <div className="mt-2 bg-gray-200 rounded-full h-2">
                  <div
                    className={`bg-${retryColor}-500 h-2 rounded-full transition-all`}
                    style={{ width: `${Math.min(data.retryRate, 100)}%` }}
                  />
                </div>
              </div>

              <div>
                <div className="text-sm text-gray-600 mb-1">Failure Rate</div>
                <div className={`text-3xl font-bold text-${failureColor}-700`}>{data.failureRate}%</div>
                <div className="text-xs text-gray-600 mt-1">
                  {data.failureRate < 5 && '✓ Low - Stable connection'}
                  {data.failureRate >= 5 && data.failureRate < 10 && '⚠ Moderate - Some drops'}
                  {data.failureRate >= 10 && '✗ High - Frequent failures'}
                </div>
                <div className="mt-2 bg-gray-200 rounded-full h-2">
                  <div
                    className={`bg-${failureColor}-500 h-2 rounded-full transition-all`}
                    style={{ width: `${Math.min(data.failureRate, 100)}%` }}
                  />
                </div>
              </div>
            </div>

            {/* Channel info */}
            <div className="border-t border-blue-300 pt-3">
              <div className="text-sm text-gray-600 mb-2">Current Channel Configuration</div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <div className="text-xs text-gray-600">Channel</div>
                  <div className="text-xl font-bold text-blue-900">{data.currentChannel}</div>
                </div>
                <div>
                  <div className="text-xs text-gray-600">Width</div>
                  <div className="text-xl font-bold text-blue-900">{data.channelWidth} MHz</div>
                </div>
              </div>
            </div>
          </div>

          {/* RF Environment */}
          <div>
            <h4 className="font-semibold text-gray-900 mb-3">RF Environment</h4>

            <div className="grid grid-cols-2 gap-4 mb-4">
              {/* Neighbor APs */}
              <div className={`bg-${neighborColor}-50 border border-${neighborColor}-200 rounded-lg p-4`}>
                <div className="text-sm font-semibold text-gray-600 mb-1">Neighbor APs Detected</div>
                <div className={`text-3xl font-bold text-${neighborColor}-700`}>{data.neighborApCount}</div>
                <div className="text-xs text-gray-600 mt-2">
                  {data.neighborApCount < 5 && '✓ Clean RF environment'}
                  {data.neighborApCount >= 5 && data.neighborApCount < 10 && '⚠ Crowded - some overlap'}
                  {data.neighborApCount >= 10 && '✗ Very crowded - high contention'}
                </div>
              </div>

              {/* Noise Floor */}
              <div className={`${
                data.noiseFloor > -85 ? 'bg-red-50 border-red-200' :
                data.noiseFloor > -90 ? 'bg-yellow-50 border-yellow-200' :
                'bg-green-50 border-green-200'
              } border rounded-lg p-4`}>
                <div className="text-sm font-semibold text-gray-600 mb-1">Noise Floor</div>
                <div className={`text-3xl font-bold ${
                  data.noiseFloor > -85 ? 'text-red-700' :
                  data.noiseFloor > -90 ? 'text-yellow-700' :
                  'text-green-700'
                }`}>
                  {data.noiseFloor} dBm
                </div>
                <div className="text-xs text-gray-600 mt-2">
                  {data.noiseFloor > -85 && '✗ High noise - interference likely'}
                  {data.noiseFloor <= -85 && data.noiseFloor > -90 && '⚠ Moderate noise'}
                  {data.noiseFloor <= -90 && '✓ Low noise - clean spectrum'}
                </div>
              </div>
            </div>

            {/* DFS Events */}
            {data.recentDfsEvents > 0 && (
              <div className="bg-yellow-50 border border-yellow-300 rounded-lg p-3">
                <div className="flex items-center gap-2 mb-1">
                  <div className="text-xl">⚠</div>
                  <div className="font-semibold text-yellow-900">
                    DFS Events Detected: {data.recentDfsEvents}
                  </div>
                </div>
                <div className="text-sm text-yellow-800">
                  The AP has detected radar on DFS channels and switched channels. This can cause brief
                  connection interruptions.
                </div>
              </div>
            )}
          </div>

          {/* Visual representation of channel overlap */}
          <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
            <h4 className="font-semibold text-gray-900 mb-3">Understanding Channel Overlap</h4>
            <p className="text-sm text-gray-700 mb-3">
              In the 2.4 GHz band, only channels 1, 6, and 11 don't overlap. In 5 GHz and 6 GHz, channels
              are cleaner, but if {data.neighborApCount} nearby APs are all transmitting, they still create
              contention even on non-overlapping channels.
            </p>

            {/* Simple visual */}
            <div className="flex items-center gap-2 text-sm">
              <div className="flex-1 bg-blue-500 text-white p-2 rounded text-center font-semibold">
                Your AP (Ch {data.currentChannel})
              </div>
              {data.neighborApCount > 0 && (
                <>
                  <div className="text-gray-400">+</div>
                  <div className="flex-1 bg-red-400 text-white p-2 rounded text-center font-semibold">
                    {data.neighborApCount} Neighbors
                  </div>
                </>
              )}
            </div>
          </div>

          {/* Diagnosis */}
          <div className={`p-4 rounded-lg border-l-4 ${
            data.isBottleneck
              ? 'bg-red-50 border-red-500'
              : 'bg-green-50 border-green-500'
          }`}>
            <h4 className={`font-semibold mb-2 ${
              data.isBottleneck ? 'text-red-900' : 'text-green-900'
            }`}>
              {data.isBottleneck ? '⚠ Diagnosis: Interference Is a Bottleneck' : '✓ Diagnosis: Low Interference'}
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

export default InterferenceSection;
