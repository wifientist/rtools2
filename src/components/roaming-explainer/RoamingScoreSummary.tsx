import type { RoamingHealth } from '@/types/roamingExplainer';

interface Props {
  health: RoamingHealth;
  score: number;
  headline: string;
  subheadline: string;
}

const healthConfig: Record<RoamingHealth, { color: string; bgColor: string; icon: string }> = {
  excellent: { color: 'text-green-700', bgColor: 'bg-green-100 border-green-300', icon: '✅' },
  good: { color: 'text-blue-700', bgColor: 'bg-blue-100 border-blue-300', icon: '👍' },
  fair: { color: 'text-yellow-700', bgColor: 'bg-yellow-100 border-yellow-300', icon: '⚠️' },
  poor: { color: 'text-orange-700', bgColor: 'bg-orange-100 border-orange-300', icon: '🔶' },
  critical: { color: 'text-red-700', bgColor: 'bg-red-100 border-red-300', icon: '🚨' },
};

function getScoreColor(score: number): string {
  if (score >= 80) return 'text-green-600';
  if (score >= 60) return 'text-blue-600';
  if (score >= 40) return 'text-yellow-600';
  if (score >= 20) return 'text-orange-600';
  return 'text-red-600';
}

function getScoreRingColor(score: number): string {
  if (score >= 80) return 'stroke-green-500';
  if (score >= 60) return 'stroke-blue-500';
  if (score >= 40) return 'stroke-yellow-500';
  if (score >= 20) return 'stroke-orange-500';
  return 'stroke-red-500';
}

export default function RoamingScoreSummary({ health, score, headline, subheadline }: Props) {
  const config = healthConfig[health];

  // SVG circle math for score ring
  const radius = 45;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = circumference - (score / 100) * circumference;

  return (
    <div className={`rounded-lg border-2 p-6 mb-6 ${config.bgColor}`}>
      <div className="flex items-center gap-6">
        {/* Score Ring */}
        <div className="relative w-28 h-28 flex-shrink-0">
          <svg className="w-28 h-28 transform -rotate-90">
            {/* Background circle */}
            <circle
              cx="56"
              cy="56"
              r={radius}
              fill="none"
              stroke="currentColor"
              strokeWidth="8"
              className="text-gray-200"
            />
            {/* Score arc */}
            <circle
              cx="56"
              cy="56"
              r={radius}
              fill="none"
              strokeWidth="8"
              strokeLinecap="round"
              className={getScoreRingColor(score)}
              strokeDasharray={circumference}
              strokeDashoffset={strokeDashoffset}
              style={{ transition: 'stroke-dashoffset 0.5s ease-in-out' }}
            />
          </svg>
          <div className="absolute inset-0 flex items-center justify-center">
            <span className={`text-3xl font-bold ${getScoreColor(score)}`}>{score}</span>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-2xl">{config.icon}</span>
            <h2 className={`text-2xl font-bold ${config.color}`}>{headline}</h2>
          </div>
          <p className={`text-lg ${config.color} opacity-80`}>{subheadline}</p>
        </div>

        {/* Health Badge */}
        <div className={`px-4 py-2 rounded-full font-semibold uppercase text-sm ${config.color} bg-white/50`}>
          {health}
        </div>
      </div>
    </div>
  );
}
