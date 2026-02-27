import type { LatencyJitterData } from '@/types/speedExplainer';

interface LatencyJitterSectionProps {
  data: LatencyJitterData;
  viewMode: 'simple' | 'detailed';
  context: {
    scopeType: string;
    scopeName: string | null;
    timeWindow: string;
  };
}

function getLatencyColor(ms: number): string {
  if (ms < 30) return 'green';
  if (ms < 100) return 'yellow';
  return 'red';
}

function getJitterColor(ms: number): string {
  if (ms < 10) return 'green';
  if (ms < 30) return 'yellow';
  return 'red';
}

function getPacketLossColor(percent: number): string {
  if (percent < 1) return 'green';
  if (percent < 3) return 'yellow';
  return 'red';
}

function LatencyJitterSection({ data, viewMode, context }: LatencyJitterSectionProps) {
  const latencyColor = getLatencyColor(data.avgLatencyMs);
  const p95Color = getLatencyColor(data.p95LatencyMs);
  const jitterColor = getJitterColor(data.jitterMs);
  const lossColor = getPacketLossColor(data.packetLossPercent);

  return (
    <div className="bg-white rounded-lg shadow p-6">
      {/* Section Header */}
      <div className="flex items-center gap-3 mb-4">
        <div className="text-3xl">🏓</div>
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Step 2: Latency & Jitter</h2>
          <p className="text-gray-600">Is the connection responsive enough for real-time apps?</p>
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

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className={`bg-${latencyColor}-50 border border-${latencyColor}-200 rounded-lg p-4`}>
              <div className="text-sm font-semibold text-gray-600 mb-1">Avg Latency</div>
              <div className={`text-2xl font-bold text-${latencyColor}-700`}>{data.avgLatencyMs} ms</div>
              <div className="text-xs text-gray-600 mt-1">
                {data.avgLatencyMs < 30 ? 'Excellent' : data.avgLatencyMs < 100 ? 'Acceptable' : 'High'}
              </div>
            </div>
            <div className={`bg-${p95Color}-50 border border-${p95Color}-200 rounded-lg p-4`}>
              <div className="text-sm font-semibold text-gray-600 mb-1">P95 Latency</div>
              <div className={`text-2xl font-bold text-${p95Color}-700`}>{data.p95LatencyMs} ms</div>
              <div className="text-xs text-gray-600 mt-1">95th percentile worst-case</div>
            </div>
            <div className={`bg-${jitterColor}-50 border border-${jitterColor}-200 rounded-lg p-4`}>
              <div className="text-sm font-semibold text-gray-600 mb-1">Jitter</div>
              <div className={`text-2xl font-bold text-${jitterColor}-700`}>{data.jitterMs} ms</div>
              <div className="text-xs text-gray-600 mt-1">
                {data.jitterMs < 10 ? 'Stable' : data.jitterMs < 30 ? 'Some variation' : 'Unstable'}
              </div>
            </div>
            <div className={`bg-${lossColor}-50 border border-${lossColor}-200 rounded-lg p-4`}>
              <div className="text-sm font-semibold text-gray-600 mb-1">Packet Loss</div>
              <div className={`text-2xl font-bold text-${lossColor}-700`}>{data.packetLossPercent}%</div>
              <div className="text-xs text-gray-600 mt-1">
                {data.packetLossPercent < 1 ? 'Minimal' : data.packetLossPercent < 3 ? 'Moderate' : 'Severe'}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Detailed View */}
      {viewMode === 'detailed' && (
        <div className="space-y-6">
          <div className="prose max-w-none">
            <h3 className="text-lg font-semibold text-gray-900 mb-2">Why Latency Matters More Than Speed</h3>
            <p className="text-gray-700 leading-relaxed mb-3">
              For real-time applications like video calls, VoIP, and gaming, <strong>latency</strong> (how long a
              packet takes to arrive) and <strong>jitter</strong> (how much latency varies) matter far more than
              raw throughput. A 50 Mbps connection with 10ms latency will feel faster for a Zoom call than
              a 500 Mbps connection with 150ms latency.
            </p>
            <p className="text-gray-700 leading-relaxed">
              <strong>Packet loss</strong> is the worst offender — even 1-2% loss causes visible quality drops
              in video calls and audible glitches in VoIP. Lost packets must be retransmitted or skipped,
              creating stutters that no amount of bandwidth can fix.
            </p>
          </div>

          {/* Thresholds reference */}
          <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
            <h4 className="font-semibold text-gray-900 mb-3">Quality Thresholds for Real-Time Apps</h4>
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm border-collapse">
                <thead>
                  <tr className="bg-gray-100 border-b">
                    <th className="text-left p-2 font-semibold text-gray-700">Metric</th>
                    <th className="text-left p-2 font-semibold text-green-700">Excellent</th>
                    <th className="text-left p-2 font-semibold text-yellow-700">Acceptable</th>
                    <th className="text-left p-2 font-semibold text-red-700">Poor</th>
                    <th className="text-left p-2 font-semibold text-gray-700">Your Value</th>
                  </tr>
                </thead>
                <tbody>
                  <tr className="border-b">
                    <td className="p-2 font-medium">Latency</td>
                    <td className="p-2 text-green-700">&lt; 30 ms</td>
                    <td className="p-2 text-yellow-700">30-100 ms</td>
                    <td className="p-2 text-red-700">&gt; 100 ms</td>
                    <td className={`p-2 font-bold text-${latencyColor}-700`}>{data.avgLatencyMs} ms</td>
                  </tr>
                  <tr className="border-b">
                    <td className="p-2 font-medium">Jitter</td>
                    <td className="p-2 text-green-700">&lt; 10 ms</td>
                    <td className="p-2 text-yellow-700">10-30 ms</td>
                    <td className="p-2 text-red-700">&gt; 30 ms</td>
                    <td className={`p-2 font-bold text-${jitterColor}-700`}>{data.jitterMs} ms</td>
                  </tr>
                  <tr>
                    <td className="p-2 font-medium">Packet Loss</td>
                    <td className="p-2 text-green-700">&lt; 1%</td>
                    <td className="p-2 text-yellow-700">1-3%</td>
                    <td className="p-2 text-red-700">&gt; 3%</td>
                    <td className={`p-2 font-bold text-${lossColor}-700`}>{data.packetLossPercent}%</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          {/* Live metrics */}
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
            <h4 className="font-semibold text-blue-900 mb-3">
              📊 Metrics for {context.scopeName}
            </h4>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <div className="text-sm text-gray-600 mb-1">Avg Latency</div>
                <div className={`text-3xl font-bold text-${latencyColor}-700`}>{data.avgLatencyMs} ms</div>
              </div>
              <div>
                <div className="text-sm text-gray-600 mb-1">P95 Latency</div>
                <div className={`text-3xl font-bold text-${p95Color}-700`}>{data.p95LatencyMs} ms</div>
              </div>
              <div>
                <div className="text-sm text-gray-600 mb-1">Jitter</div>
                <div className={`text-3xl font-bold text-${jitterColor}-700`}>{data.jitterMs} ms</div>
              </div>
              <div>
                <div className="text-sm text-gray-600 mb-1">Packet Loss</div>
                <div className={`text-3xl font-bold text-${lossColor}-700`}>{data.packetLossPercent}%</div>
              </div>
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
              {data.isBottleneck ? '⚠ Diagnosis: Latency/Jitter Is a Problem' : '✓ Diagnosis: Responsive Connection'}
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

export default LatencyJitterSection;
