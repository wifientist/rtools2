import type { LinkQualityData } from '@/types/speedExplainer';

interface LinkQualitySectionProps {
  data: LinkQualityData;
  viewMode: 'simple' | 'detailed';
  context: {
    scopeType: string;
    scopeName: string | null;
    timeWindow: string;
  };
}

function LinkQualitySection({ data, viewMode, context }: LinkQualitySectionProps) {
  const rssiColor = data.rssi >= -60 ? 'green' : data.rssi >= -70 ? 'yellow' : 'red';
  const snrColor = data.snr >= 30 ? 'green' : data.snr >= 20 ? 'yellow' : 'red';

  // Calculate MCS distribution for the chart
  const mcsEntries = Object.entries(data.mcsHistogram).map(([mcs, count]) => ({
    mcs: parseInt(mcs),
    count
  })).sort((a, b) => a.mcs - b.mcs);

  const maxCount = Math.max(...mcsEntries.map(e => e.count), 1);

  return (
    <div className="bg-white rounded-lg shadow p-6">
      {/* Section Header */}
      <div className="flex items-center gap-3 mb-4">
        <div className="text-3xl">📶</div>
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Step 1: Signal & Link Quality</h2>
          <p className="text-gray-600">How strong is the radio connection between device and AP?</p>
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

          {/* Key metrics in simple cards */}
          <div className="grid grid-cols-3 gap-4">
            <div className={`bg-${rssiColor}-50 border border-${rssiColor}-200 rounded-lg p-4`}>
              <div className="text-sm font-semibold text-gray-600 mb-1">Signal Strength (RSSI)</div>
              <div className={`text-2xl font-bold text-${rssiColor}-700`}>{data.rssi} dBm</div>
            </div>
            <div className={`bg-${snrColor}-50 border border-${snrColor}-200 rounded-lg p-4`}>
              <div className="text-sm font-semibold text-gray-600 mb-1">Signal Quality (SNR)</div>
              <div className={`text-2xl font-bold text-${snrColor}-700`}>{data.snr} dB</div>
            </div>
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
              <div className="text-sm font-semibold text-gray-600 mb-1">Data Rate Mode</div>
              <div className="text-lg font-bold text-blue-700">{data.mcsMode}</div>
            </div>
          </div>
        </div>
      )}

      {/* Detailed View */}
      {viewMode === 'detailed' && (
        <div className="space-y-6">
          {/* Article-style explanation */}
          <div className="prose max-w-none">
            <h3 className="text-lg font-semibold text-gray-900 mb-2">What Is Signal Quality?</h3>
            <p className="text-gray-700 leading-relaxed mb-4">
              Before any data can fly through the air, your device needs a solid radio connection to the access point.
              Two key metrics tell us how good that link is: <strong>RSSI</strong> (signal strength) and <strong>SNR</strong> (signal-to-noise ratio).
            </p>
            <p className="text-gray-700 leading-relaxed">
              Think of RSSI as "volume" — how loud the AP's signal is when it reaches your device. SNR is more like
              "clarity" — how much louder the signal is compared to background noise. Both matter, because if either is too low,
              the Wi-Fi radio can't use fast data rates (high MCS values).
            </p>
          </div>

          {/* Live metrics */}
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
            <h4 className="font-semibold text-blue-900 mb-3">
              📊 Live Metrics for {context.scopeName}
            </h4>
            <div className="grid grid-cols-2 gap-4 mb-4">
              <div>
                <div className="text-sm text-gray-600 mb-1">RSSI (Signal Strength)</div>
                <div className={`text-3xl font-bold text-${rssiColor}-700`}>{data.rssi} dBm</div>
                <div className="text-sm text-gray-600 mt-1">
                  {data.rssi >= -60 && '✓ Excellent - Strong signal'}
                  {data.rssi < -60 && data.rssi >= -70 && '⚠ Good - Adequate signal'}
                  {data.rssi < -70 && '✗ Poor - Weak signal'}
                </div>
              </div>
              <div>
                <div className="text-sm text-gray-600 mb-1">SNR (Signal Quality)</div>
                <div className={`text-3xl font-bold text-${snrColor}-700`}>{data.snr} dB</div>
                <div className="text-sm text-gray-600 mt-1">
                  {data.snr >= 30 && '✓ Excellent - Clean signal'}
                  {data.snr < 30 && data.snr >= 20 && '⚠ Good - Some noise'}
                  {data.snr < 20 && '✗ Poor - High noise floor'}
                </div>
              </div>
            </div>

            {/* MCS Mode */}
            <div className="border-t border-blue-300 pt-3">
              <div className="text-sm text-gray-600 mb-1">Current Data Rate Mode</div>
              <div className="text-xl font-bold text-blue-900">{data.mcsMode}</div>
              <div className="text-sm text-gray-600 mt-1">
                This determines the theoretical maximum PHY rate for the radio link
              </div>
            </div>
          </div>

          {/* MCS Histogram */}
          <div>
            <h4 className="font-semibold text-gray-900 mb-3">MCS Distribution (Last {context.timeWindow})</h4>
            <p className="text-sm text-gray-600 mb-3">
              This chart shows which MCS (Modulation and Coding Scheme) values were used over the time window.
              Higher MCS = faster data rates. Consistent high MCS means good signal quality.
            </p>
            <div className="space-y-2">
              {mcsEntries.map(({ mcs, count }) => {
                const percentage = (count / maxCount) * 100;
                const mcsLabel = mcs >= 8 ? 'Excellent' : mcs >= 5 ? 'Good' : mcs >= 3 ? 'Fair' : 'Poor';
                const barColor = mcs >= 8 ? 'bg-green-500' : mcs >= 5 ? 'bg-blue-500' : mcs >= 3 ? 'bg-yellow-500' : 'bg-red-500';

                return (
                  <div key={mcs} className="flex items-center gap-3">
                    <div className="w-16 text-sm font-medium text-gray-700">MCS {mcs}</div>
                    <div className="flex-1 bg-gray-200 rounded-full h-6 relative">
                      <div
                        className={`${barColor} h-6 rounded-full transition-all duration-500`}
                        style={{ width: `${percentage}%` }}
                      />
                      <div className="absolute inset-0 flex items-center justify-center text-xs font-semibold text-gray-700">
                        {count} packets
                      </div>
                    </div>
                    <div className="w-20 text-xs text-gray-500">{mcsLabel}</div>
                  </div>
                );
              })}
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
              {data.isBottleneck ? '⚠ Diagnosis: Signal Is a Bottleneck' : '✓ Diagnosis: Signal Quality Is Good'}
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

export default LinkQualitySection;
