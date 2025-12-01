import { useState } from 'react';
import type { PhyVsRealData } from '@/types/speedExplainer';
import { calculateRealisticThroughput } from '@/utils/phyRates';

interface PhyVsRealSectionProps {
  data: PhyVsRealData;
  viewMode: 'simple' | 'detailed';
  context: {
    scopeType: string;
    scopeName: string | null;
    timeWindow: string;
  };
}

function PhyVsRealSection({ data, viewMode, context }: PhyVsRealSectionProps) {
  // Interactive sliders for "what-if" scenarios
  const [whatIfAirtime, setWhatIfAirtime] = useState(data.airtimeUtilization);
  const [whatIfRetries, setWhatIfRetries] = useState(5); // Assume 5% retry rate as baseline

  const whatIfThroughput = calculateRealisticThroughput(
    data.expectedPhyCeiling,
    whatIfAirtime,
    whatIfRetries
  );

  const efficiencyColor = data.efficiency >= 60 ? 'green' : data.efficiency >= 40 ? 'yellow' : 'red';

  return (
    <div className="bg-white rounded-lg shadow p-6">
      {/* Section Header */}
      <div className="flex items-center gap-3 mb-4">
        <div className="text-3xl">⚡</div>
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Step 2: PHY Rate vs. Reality</h2>
          <p className="text-gray-600">Why actual throughput is always less than the theoretical maximum</p>
        </div>
      </div>

      {/* Simple View */}
      {viewMode === 'simple' && (
        <div className="space-y-4">
          <div className="prose max-w-none">
            <p className="text-gray-700 leading-relaxed">
              The "PHY rate" you see in your Wi-Fi settings (like "866 Mbps") is theoretical—it assumes perfect conditions
              and zero overhead. Real throughput is much lower because of MAC overhead, acknowledgments, sharing airtime
              with other devices, and retransmissions.
            </p>
          </div>

          {/* Comparison */}
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
              <div className="text-sm font-semibold text-gray-600 mb-1">Theoretical PHY Rate</div>
              <div className="text-3xl font-bold text-blue-700">{data.expectedPhyCeiling} Mbps</div>
              <div className="text-xs text-gray-600 mt-1">Based on MCS, bandwidth, streams</div>
            </div>
            <div className={`bg-${efficiencyColor}-50 border border-${efficiencyColor}-200 rounded-lg p-4`}>
              <div className="text-sm font-semibold text-gray-600 mb-1">Actual Best Throughput</div>
              <div className={`text-3xl font-bold text-${efficiencyColor}-700`}>{data.actualThroughputBest} Mbps</div>
              <div className="text-xs text-gray-600 mt-1">{data.efficiency}% efficiency</div>
            </div>
          </div>

          {/* Airtime breakdown */}
          <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
            <h4 className="font-semibold text-gray-900 mb-2">Airtime Usage</h4>
            <div className="text-sm text-gray-700 mb-2">
              Channel is {data.airtimeUtilization}% busy with {data.clientsOnRadio} clients sharing it.
            </div>
            <div className="grid grid-cols-4 gap-2 text-xs">
              <div>
                <div className="font-semibold text-gray-600">Data</div>
                <div className="text-lg font-bold text-green-700">{data.airtimeBreakdown.data}%</div>
              </div>
              <div>
                <div className="font-semibold text-gray-600">Mgmt</div>
                <div className="text-lg font-bold text-blue-700">{data.airtimeBreakdown.management}%</div>
              </div>
              <div>
                <div className="font-semibold text-gray-600">Retries</div>
                <div className="text-lg font-bold text-yellow-700">{data.airtimeBreakdown.retries}%</div>
              </div>
              <div>
                <div className="font-semibold text-gray-600">Other</div>
                <div className="text-lg font-bold text-gray-700">{data.airtimeBreakdown.other}%</div>
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
            <h3 className="text-lg font-semibold text-gray-900 mb-2">Why Real Throughput Is Always Lower</h3>
            <p className="text-gray-700 leading-relaxed mb-3">
              If your device negotiates a PHY rate of 866 Mbps, you'll never see a speed test that fast. Here's why:
            </p>
            <ul className="list-disc list-inside space-y-2 text-gray-700 mb-3">
              <li><strong>MAC overhead:</strong> Every Wi-Fi frame has headers, acknowledgments, and gaps between transmissions. This eats ~35% of the theoretical rate.</li>
              <li><strong>Airtime sharing:</strong> If 5 clients are active, each gets ~20% of available airtime (in a fair system).</li>
              <li><strong>Retransmissions:</strong> When packets fail (due to interference or weak signal), they must be resent, wasting airtime.</li>
              <li><strong>Management overhead:</strong> Beacons, probes, and control frames consume some airtime too.</li>
            </ul>
            <p className="text-gray-700 leading-relaxed">
              In practice, 50-70% efficiency is typical for real-world Wi-Fi. Lower efficiency usually means congestion or interference.
            </p>
          </div>

          {/* Live metrics */}
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
            <h4 className="font-semibold text-blue-900 mb-3">
              📊 Live Metrics for {context.scopeName}
            </h4>
            <div className="grid grid-cols-2 gap-4 mb-4">
              <div>
                <div className="text-sm text-gray-600 mb-1">Theoretical PHY Ceiling</div>
                <div className="text-3xl font-bold text-blue-700">{data.expectedPhyCeiling} Mbps</div>
                <div className="text-xs text-gray-600 mt-1">Maximum possible based on radio parameters</div>
              </div>
              <div>
                <div className="text-sm text-gray-600 mb-1">Actual Best Throughput</div>
                <div className={`text-3xl font-bold text-${efficiencyColor}-700`}>{data.actualThroughputBest} Mbps</div>
                <div className="text-xs text-gray-600 mt-1">Measured peak throughput</div>
              </div>
            </div>

            {/* Efficiency gauge */}
            <div className="border-t border-blue-300 pt-3">
              <div className="text-sm text-gray-600 mb-2">Overall Efficiency</div>
              <div className="relative bg-gray-200 rounded-full h-8">
                <div
                  className={`bg-${efficiencyColor}-500 h-8 rounded-full transition-all duration-500 flex items-center justify-center`}
                  style={{ width: `${data.efficiency}%` }}
                >
                  <span className="text-sm font-bold text-white">{data.efficiency}%</span>
                </div>
              </div>
              <div className="text-xs text-gray-600 mt-1">
                {data.efficiency >= 60 && '✓ Excellent - Near optimal performance'}
                {data.efficiency < 60 && data.efficiency >= 40 && '⚠ Good - Some inefficiency present'}
                {data.efficiency < 40 && '✗ Poor - Significant overhead or congestion'}
              </div>
            </div>
          </div>

          {/* Airtime breakdown */}
          <div>
            <h4 className="font-semibold text-gray-900 mb-3">Airtime Breakdown</h4>
            <p className="text-sm text-gray-600 mb-3">
              The radio channel is {data.airtimeUtilization}% busy. Here's how that airtime is being used:
            </p>

            {/* Visual airtime breakdown */}
            <div className="relative bg-gray-200 rounded-lg h-12 flex overflow-hidden mb-2">
              <div
                className="bg-green-500 flex items-center justify-center text-white font-semibold text-sm"
                style={{ width: `${data.airtimeBreakdown.data}%` }}
              >
                {data.airtimeBreakdown.data > 10 && `Data ${data.airtimeBreakdown.data}%`}
              </div>
              <div
                className="bg-blue-500 flex items-center justify-center text-white font-semibold text-sm"
                style={{ width: `${data.airtimeBreakdown.management}%` }}
              >
                {data.airtimeBreakdown.management > 5 && `Mgmt ${data.airtimeBreakdown.management}%`}
              </div>
              <div
                className="bg-yellow-500 flex items-center justify-center text-white font-semibold text-sm"
                style={{ width: `${data.airtimeBreakdown.retries}%` }}
              >
                {data.airtimeBreakdown.retries > 5 && `Retry ${data.airtimeBreakdown.retries}%`}
              </div>
              <div
                className="bg-gray-500 flex items-center justify-center text-white font-semibold text-sm"
                style={{ width: `${data.airtimeBreakdown.other}%` }}
              >
                {data.airtimeBreakdown.other > 5 && `Other ${data.airtimeBreakdown.other}%`}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <div className="w-3 h-3 bg-green-500 rounded"></div>
                  <span className="font-semibold">Data: {data.airtimeBreakdown.data}%</span>
                </div>
                <div className="text-gray-600 text-xs">Actual user data transmission</div>
              </div>
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <div className="w-3 h-3 bg-blue-500 rounded"></div>
                  <span className="font-semibold">Management: {data.airtimeBreakdown.management}%</span>
                </div>
                <div className="text-gray-600 text-xs">Beacons, probes, control frames</div>
              </div>
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <div className="w-3 h-3 bg-yellow-500 rounded"></div>
                  <span className="font-semibold">Retries: {data.airtimeBreakdown.retries}%</span>
                </div>
                <div className="text-gray-600 text-xs">Failed transmissions being resent</div>
              </div>
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <div className="w-3 h-3 bg-gray-500 rounded"></div>
                  <span className="font-semibold">Other: {data.airtimeBreakdown.other}%</span>
                </div>
                <div className="text-gray-600 text-xs">ACKs, gaps, contention</div>
              </div>
            </div>

            {/* Client sharing info */}
            <div className="mt-4 p-3 bg-gray-50 border border-gray-300 rounded">
              <div className="text-sm font-semibold text-gray-700 mb-1">
                {data.clientsOnRadio} clients on this radio
              </div>
              <div className="text-xs text-gray-600">
                Average airtime per client: {data.avgPerClientAirtime}%
              </div>
            </div>
          </div>

          {/* Interactive "What-If" Calculator */}
          <div className="bg-purple-50 border border-purple-200 rounded-lg p-4">
            <h4 className="font-semibold text-purple-900 mb-3">
              🧪 Interactive: What If Conditions Change?
            </h4>
            <p className="text-sm text-gray-700 mb-4">
              Adjust the sliders below to see how airtime utilization and retry rate affect throughput:
            </p>

            {/* Airtime slider */}
            <div className="mb-4">
              <label className="block text-sm font-semibold text-gray-700 mb-2">
                Airtime Utilization: {whatIfAirtime}%
              </label>
              <input
                type="range"
                min="10"
                max="100"
                value={whatIfAirtime}
                onChange={(e) => setWhatIfAirtime(parseInt(e.target.value))}
                className="w-full h-2 bg-gray-300 rounded-lg appearance-none cursor-pointer accent-purple-600"
              />
              <div className="flex justify-between text-xs text-gray-500 mt-1">
                <span>10% (idle)</span>
                <span>50% (moderate)</span>
                <span>100% (saturated)</span>
              </div>
            </div>

            {/* Retry rate slider */}
            <div className="mb-4">
              <label className="block text-sm font-semibold text-gray-700 mb-2">
                Retry Rate: {whatIfRetries}%
              </label>
              <input
                type="range"
                min="0"
                max="30"
                value={whatIfRetries}
                onChange={(e) => setWhatIfRetries(parseInt(e.target.value))}
                className="w-full h-2 bg-gray-300 rounded-lg appearance-none cursor-pointer accent-purple-600"
              />
              <div className="flex justify-between text-xs text-gray-500 mt-1">
                <span>0% (clean)</span>
                <span>15% (typical)</span>
                <span>30% (poor)</span>
              </div>
            </div>

            {/* Result */}
            <div className="bg-white border border-purple-300 rounded p-3">
              <div className="text-sm text-gray-600 mb-1">Estimated Throughput:</div>
              <div className="text-2xl font-bold text-purple-700">{whatIfThroughput} Mbps</div>
              <div className="text-xs text-gray-600 mt-1">
                {Math.round((whatIfThroughput / data.expectedPhyCeiling) * 100)}% of PHY rate
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default PhyVsRealSection;
