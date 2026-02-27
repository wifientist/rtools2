import type { BandContextData } from '@/types/speedExplainer';

interface BandContextSectionProps {
  data: BandContextData;
  viewMode: 'simple' | 'detailed';
}

const BAND_META: Record<string, { color: string; bgColor: string; label: string; speed: string }> = {
  '2.4GHz': { color: 'text-yellow-800', bgColor: 'bg-yellow-50 border-yellow-300', label: '2.4 GHz', speed: 'Slower, longer range' },
  '5GHz': { color: 'text-green-800', bgColor: 'bg-green-50 border-green-300', label: '5 GHz', speed: 'Fast, moderate range' },
  '6GHz': { color: 'text-blue-800', bgColor: 'bg-blue-50 border-blue-300', label: '6 GHz', speed: 'Fastest, cleanest spectrum' },
};

function BandContextSection({ data, viewMode }: BandContextSectionProps) {
  const bandInfo = BAND_META[data.connectedBand] || BAND_META['5GHz'];

  return (
    <div className={`rounded-lg border-2 p-4 mb-6 ${bandInfo.bgColor}`}>
      {/* Compact banner */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <span className="text-2xl">📡</span>
          <div>
            <div className={`font-semibold ${bandInfo.color}`}>
              Connected on {bandInfo.label} — Ch {data.connectedChannel} ({data.channelWidth} MHz)
            </div>
            <div className="text-sm text-gray-600">
              AP: {data.apName} {data.bandSteeringEnabled && '• Band steering active'}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {data.availableBands.map(band => {
            const meta = BAND_META[band];
            const isConnected = band === data.connectedBand;
            return (
              <span
                key={band}
                className={`px-2 py-1 rounded text-xs font-semibold ${
                  isConnected
                    ? `${meta.bgColor} ${meta.color} border`
                    : 'bg-gray-100 text-gray-500'
                }`}
              >
                {meta.label}{isConnected ? ' (connected)' : ''}
              </span>
            );
          })}
        </div>
      </div>

      {/* Detailed view: band tradeoffs */}
      {viewMode === 'detailed' && (
        <div className="mt-4 pt-4 border-t border-gray-200">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
            <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3">
              <div className="font-semibold text-yellow-800 text-sm">2.4 GHz</div>
              <p className="text-xs text-yellow-700 mt-1">
                Best range, penetrates walls well. But only 3 non-overlapping channels, crowded spectrum,
                and max ~600 Mbps even with Wi-Fi 6. Many IoT devices are 2.4-only.
              </p>
            </div>
            <div className="bg-green-50 border border-green-200 rounded-lg p-3">
              <div className="font-semibold text-green-800 text-sm">5 GHz</div>
              <p className="text-xs text-green-700 mt-1">
                Good balance of speed and range. Many non-overlapping channels, supports 80/160 MHz widths.
                Most modern devices prefer 5 GHz when signal is adequate.
              </p>
            </div>
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
              <div className="font-semibold text-blue-800 text-sm">6 GHz</div>
              <p className="text-xs text-blue-700 mt-1">
                Fastest and cleanest — no legacy devices. Requires Wi-Fi 6E or 7 on both AP and client.
                Shorter range than 5 GHz, but nearly interference-free.
              </p>
            </div>
          </div>

          <div className={`p-3 rounded-lg ${bandInfo.bgColor}`}>
            <p className={`text-sm ${bandInfo.color}`}>
              {data.diagnosis}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export default BandContextSection;
