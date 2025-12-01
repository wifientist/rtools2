import type { SpeedScoreSummary as SpeedScoreSummaryType } from '@/types/speedExplainer';

interface SpeedScoreSummaryProps {
  data: SpeedScoreSummaryType;
  context: {
    scopeType: string;
    scopeName: string | null;
    timeWindow: string;
  };
}

const scoreConfig = {
  excellent: { emoji: '🟢', color: 'green', label: 'Excellent' },
  good: { emoji: '🟡', color: 'yellow', label: 'Good' },
  fair: { emoji: '🟠', color: 'orange', label: 'Fair' },
  poor: { emoji: '🔴', color: 'red', label: 'Poor' },
};

const statusConfig = {
  good: { bg: 'bg-green-100', text: 'text-green-800', icon: '✓' },
  fair: { bg: 'bg-yellow-100', text: 'text-yellow-800', icon: '⚠' },
  poor: { bg: 'bg-red-100', text: 'text-red-800', icon: '✗' },
};

function SpeedScoreSummary({ data, context }: SpeedScoreSummaryProps) {
  const config = scoreConfig[data.score];

  return (
    <div className="bg-white rounded-lg shadow-lg p-6 mb-6 border-l-4 border-blue-500">
      <div className="flex items-start justify-between mb-4">
        <div>
          <h2 className="text-2xl font-bold text-gray-900 mb-1">
            {config.emoji} Your Wi-Fi feels: <span className={`text-${config.color}-600`}>{config.label}</span>
          </h2>
          <p className="text-gray-600">
            For <strong>{context.scopeName || 'selected scope'}</strong> over the last {context.timeWindow}
          </p>
        </div>
        <div className="text-right">
          <div className="text-4xl font-bold text-gray-900">{data.scoreValue}</div>
          <div className="text-sm text-gray-500">out of 100</div>
        </div>
      </div>

      {/* Primary Bottleneck */}
      <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-4">
        <h3 className="font-semibold text-blue-900 mb-1">Likely bottleneck:</h3>
        <p className="text-blue-800 text-lg mb-2">{data.primaryBottleneck.description}</p>
        <p className="text-blue-700 text-sm">
          💡 <strong>Recommendation:</strong> {data.primaryBottleneck.recommendation}
        </p>
      </div>

      {/* Quick Signals */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        {Object.entries(data.quickSignals).map(([key, signal]) => {
          const statusStyle = statusConfig[signal.status];
          return (
            <div
              key={key}
              className={`${statusStyle.bg} rounded-lg p-3 border border-gray-200`}
            >
              <div className={`text-xs font-semibold ${statusStyle.text} uppercase mb-1`}>
                {statusStyle.icon} {key.replace(/([A-Z])/g, ' $1').trim()}
              </div>
              <div className="text-sm text-gray-800">{signal.detail}</div>
            </div>
          );
        })}
      </div>

      {/* TL;DR */}
      <div className="border-t pt-4">
        <p className="text-gray-700 leading-relaxed">
          {data.tldr}
        </p>
      </div>
    </div>
  );
}

export default SpeedScoreSummary;
