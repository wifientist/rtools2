import type { TroubleshootingData, ViewMode } from '@/types/roamingExplainer';

interface Props {
  data: TroubleshootingData;
  viewMode: ViewMode;
}

function getSeverityConfig(severity: string): { color: string; bgColor: string; icon: string; borderColor: string } {
  switch (severity) {
    case 'critical':
      return { color: 'text-red-700', bgColor: 'bg-red-50', icon: '🚨', borderColor: 'border-red-300' };
    case 'warning':
      return { color: 'text-yellow-700', bgColor: 'bg-yellow-50', icon: '⚠️', borderColor: 'border-yellow-300' };
    case 'info':
      return { color: 'text-blue-700', bgColor: 'bg-blue-50', icon: 'ℹ️', borderColor: 'border-blue-300' };
    default:
      return { color: 'text-gray-700', bgColor: 'bg-gray-50', icon: '•', borderColor: 'border-gray-300' };
  }
}

function getCategoryIcon(category: string): string {
  switch (category) {
    case 'config': return '⚙️';
    case 'client': return '📱';
    case 'infrastructure': return '🏗️';
    case 'design': return '📐';
    default: return '❓';
  }
}

export default function TroubleshootingSection({ data, viewMode }: Props) {
  const { config, issues, scoreBreakdown } = data;

  const criticalCount = issues.filter(i => i.severity === 'critical').length;
  const warningCount = issues.filter(i => i.severity === 'warning').length;

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <h3 className="text-xl font-semibold mb-4 flex items-center gap-2">
        <span className="text-2xl">🔧</span>
        Troubleshooting & Configuration
      </h3>

      {/* 802.11k/v/r Status */}
      <div className="mb-6">
        <h4 className="font-semibold mb-3 flex items-center gap-2">
          <span>📡</span> Roaming Standards (802.11k/v/r)
        </h4>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <div className={`rounded-lg p-3 text-center border ${config.roamingStandards.dot11k ? 'bg-green-50 border-green-300' : 'bg-red-50 border-red-300'}`}>
            <div className={`text-2xl mb-1 ${config.roamingStandards.dot11k ? 'text-green-600' : 'text-red-600'}`}>
              {config.roamingStandards.dot11k ? '✓' : '✗'}
            </div>
            <div className="text-sm font-semibold">802.11k</div>
            <div className="text-xs text-gray-500">Neighbor Reports</div>
          </div>
          <div className={`rounded-lg p-3 text-center border ${config.roamingStandards.dot11v ? 'bg-green-50 border-green-300' : 'bg-red-50 border-red-300'}`}>
            <div className={`text-2xl mb-1 ${config.roamingStandards.dot11v ? 'text-green-600' : 'text-red-600'}`}>
              {config.roamingStandards.dot11v ? '✓' : '✗'}
            </div>
            <div className="text-sm font-semibold">802.11v</div>
            <div className="text-xs text-gray-500">BSS Transition</div>
          </div>
          <div className={`rounded-lg p-3 text-center border ${config.roamingStandards.dot11r ? 'bg-green-50 border-green-300' : 'bg-red-50 border-red-300'}`}>
            <div className={`text-2xl mb-1 ${config.roamingStandards.dot11r ? 'text-green-600' : 'text-red-600'}`}>
              {config.roamingStandards.dot11r ? '✓' : '✗'}
            </div>
            <div className="text-sm font-semibold">802.11r</div>
            <div className="text-xs text-gray-500">Fast Transition</div>
          </div>
          <div className={`rounded-lg p-3 text-center border ${config.roamingStandards.okcEnabled ? 'bg-green-50 border-green-300' : 'bg-gray-50 border-gray-300'}`}>
            <div className={`text-2xl mb-1 ${config.roamingStandards.okcEnabled ? 'text-green-600' : 'text-gray-400'}`}>
              {config.roamingStandards.okcEnabled ? '✓' : '○'}
            </div>
            <div className="text-sm font-semibold">OKC</div>
            <div className="text-xs text-gray-500">Key Caching</div>
          </div>
          <div className={`rounded-lg p-3 text-center border ${config.roamingStandards.preauthEnabled ? 'bg-green-50 border-green-300' : 'bg-gray-50 border-gray-300'}`}>
            <div className={`text-2xl mb-1 ${config.roamingStandards.preauthEnabled ? 'text-green-600' : 'text-gray-400'}`}>
              {config.roamingStandards.preauthEnabled ? '✓' : '○'}
            </div>
            <div className="text-sm font-semibold">Preauth</div>
            <div className="text-xs text-gray-500">Pre-authentication</div>
          </div>
        </div>

        {viewMode === 'detailed' && (
          <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-3 text-sm">
            <div className="bg-gray-50 p-3 rounded">
              <div className="font-semibold">802.11k</div>
              <p className="text-gray-600">
                Enables APs to tell clients about neighboring APs, so clients don't have to scan all channels.
              </p>
            </div>
            <div className="bg-gray-50 p-3 rounded">
              <div className="font-semibold">802.11v</div>
              <p className="text-gray-600">
                Allows APs to suggest a better AP to connect to. Helpful for pushing sticky clients.
              </p>
            </div>
            <div className="bg-gray-50 p-3 rounded">
              <div className="font-semibold">802.11r</div>
              <p className="text-gray-600">
                Pre-negotiates authentication. Reduces roam time from ~400ms to &lt;50ms. Critical for VoIP.
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Other Config */}
      <div className="mb-6 border-t pt-4">
        <h4 className="font-semibold mb-3 flex items-center gap-2">
          <span>⚙️</span> Other Roaming Settings
        </h4>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className={`rounded-lg p-3 text-center ${config.minRssiThreshold ? 'bg-green-50' : 'bg-yellow-50'}`}>
            <div className={`text-xl font-bold ${config.minRssiThreshold ? 'text-green-700' : 'text-yellow-700'}`}>
              {config.minRssiThreshold ? `${config.minRssiThreshold} dBm` : 'Not Set'}
            </div>
            <div className="text-sm text-gray-600">Min RSSI Threshold</div>
          </div>
          <div className={`rounded-lg p-3 text-center ${config.bssMinRate ? 'bg-green-50' : 'bg-gray-50'}`}>
            <div className={`text-xl font-bold ${config.bssMinRate ? 'text-green-700' : 'text-gray-500'}`}>
              {config.bssMinRate ? `${config.bssMinRate} Mbps` : 'Default'}
            </div>
            <div className="text-sm text-gray-600">BSS Min Rate</div>
          </div>
          <div className={`rounded-lg p-3 text-center ${config.bandSteeringEnabled ? 'bg-green-50' : 'bg-gray-50'}`}>
            <div className={`text-xl ${config.bandSteeringEnabled ? 'text-green-600' : 'text-gray-400'}`}>
              {config.bandSteeringEnabled ? '✓ Enabled' : '○ Disabled'}
            </div>
            <div className="text-sm text-gray-600">Band Steering</div>
          </div>
          <div className={`rounded-lg p-3 text-center ${config.loadBalancingEnabled ? 'bg-green-50' : 'bg-gray-50'}`}>
            <div className={`text-xl ${config.loadBalancingEnabled ? 'text-green-600' : 'text-gray-400'}`}>
              {config.loadBalancingEnabled ? '✓ Enabled' : '○ Disabled'}
            </div>
            <div className="text-sm text-gray-600">Load Balancing</div>
          </div>
        </div>
      </div>

      {/* Issues Summary */}
      {issues.length > 0 && (
        <div className="mb-6 border-t pt-4">
          <div className="flex items-center justify-between mb-3">
            <h4 className="font-semibold flex items-center gap-2">
              <span>🔍</span> Identified Issues
            </h4>
            <div className="flex items-center gap-3 text-sm">
              {criticalCount > 0 && (
                <span className="text-red-600 font-semibold">{criticalCount} Critical</span>
              )}
              {warningCount > 0 && (
                <span className="text-yellow-600 font-semibold">{warningCount} Warning</span>
              )}
            </div>
          </div>

          <div className="space-y-3">
            {issues.map((issue, idx) => {
              const severityConfig = getSeverityConfig(issue.severity);
              return (
                <div key={idx} className={`rounded-lg border p-4 ${severityConfig.bgColor} ${severityConfig.borderColor}`}>
                  <div className="flex items-start gap-3">
                    <span className="text-xl">{severityConfig.icon}</span>
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-sm text-gray-500">{getCategoryIcon(issue.category)}</span>
                        <span className={`font-semibold ${severityConfig.color}`}>{issue.issue}</span>
                      </div>
                      <p className="text-sm text-gray-700">
                        <span className="font-medium">Recommendation: </span>
                        {issue.recommendation}
                      </p>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Score Breakdown */}
      {viewMode === 'detailed' && scoreBreakdown.length > 0 && (
        <div className="border-t pt-4">
          <h4 className="font-semibold mb-3 flex items-center gap-2">
            <span>📊</span> Score Breakdown
          </h4>
          <div className="space-y-3">
            {scoreBreakdown.map((item, idx) => {
              const percentage = (item.score / item.maxScore) * 100;
              return (
                <div key={idx}>
                  <div className="flex items-center justify-between text-sm mb-1">
                    <span className="font-medium">{item.category}</span>
                    <span className={`${percentage >= 80 ? 'text-green-600' : percentage >= 50 ? 'text-yellow-600' : 'text-red-600'}`}>
                      {item.score}/{item.maxScore}
                    </span>
                  </div>
                  <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full ${percentage >= 80 ? 'bg-green-500' : percentage >= 50 ? 'bg-yellow-500' : 'bg-red-500'}`}
                      style={{ width: `${percentage}%` }}
                    />
                  </div>
                  <p className="text-xs text-gray-500 mt-1">{item.notes}</p>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
