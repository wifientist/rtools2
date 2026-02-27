interface SpeedNeedsSectionProps {
  viewMode: 'simple' | 'detailed';
}

const ACTIVITY_NEEDS = [
  { activity: 'Web browsing', speed: '5 Mbps', icon: '🌐', note: 'Text-heavy pages load instantly' },
  { activity: 'Video call (HD)', speed: '5-10 Mbps', icon: '📹', note: 'Latency matters more than speed' },
  { activity: '4K streaming', speed: '25 Mbps', icon: '📺', note: 'Per stream — 2 streams = 50 Mbps' },
  { activity: 'Online gaming', speed: '5-15 Mbps', icon: '🎮', note: 'Latency and jitter matter far more' },
  { activity: 'Music streaming', speed: '1-2 Mbps', icon: '🎵', note: 'Very low bandwidth needs' },
  { activity: 'File download (1 GB)', speed: '~2 min @ 100 Mbps', icon: '📁', note: 'Only heavy transfers need high speed' },
];

function SpeedNeedsSection({ viewMode }: SpeedNeedsSectionProps) {
  return (
    <div className="bg-white rounded-lg shadow p-6 mb-6">
      <div className="flex items-center gap-3 mb-4">
        <div className="text-3xl">🎯</div>
        <div>
          <h2 className="text-2xl font-bold text-gray-900">What Speed Do You Actually Need?</h2>
          <p className="text-gray-600">Most activities need far less bandwidth than you think</p>
        </div>
      </div>

      {/* Activity needs grid */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-4">
        {ACTIVITY_NEEDS.map((item) => (
          <div key={item.activity} className="bg-gray-50 border border-gray-200 rounded-lg p-3">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-lg">{item.icon}</span>
              <span className="text-sm font-semibold text-gray-800">{item.activity}</span>
            </div>
            <div className="text-lg font-bold text-blue-700">{item.speed}</div>
            <div className="text-xs text-gray-500 mt-1">{item.note}</div>
          </div>
        ))}
      </div>

      {/* Key takeaway */}
      <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
        <p className="text-blue-900 leading-relaxed">
          <strong>The takeaway:</strong> If your connection is 50 Mbps, you're fine for almost everything
          a single user does. Even 25 Mbps handles 4K streaming. The real question is usually not
          "is my speed fast enough?" — it's "why does my connection <em>feel</em> slow?" And that's
          usually about latency, jitter, or packet loss, not raw throughput.
        </p>
      </div>

      {/* Detailed view: nuance */}
      {viewMode === 'detailed' && (
        <div className="mt-6 space-y-4">
          <div className="prose max-w-none">
            <h3 className="text-lg font-semibold text-gray-900 mb-2">Speed Tests vs. Real-World Experience</h3>
            <p className="text-gray-700 leading-relaxed mb-3">
              A speed test measures <strong>bulk throughput</strong> to a single server — how fast you can fill a pipe.
              But real applications work differently: web pages make dozens of small requests, video calls
              need consistent low-latency delivery, and gaming needs packets delivered in under 30ms.
            </p>
            <p className="text-gray-700 leading-relaxed mb-3">
              This is why you can have a 500 Mbps speed test result and still have choppy Zoom calls —
              throughput isn't the bottleneck, <strong>latency and jitter</strong> are.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
              <h4 className="font-semibold text-yellow-900 mb-2">Upload vs. Download</h4>
              <p className="text-sm text-yellow-800">
                Video calls need 3-5 Mbps <em>upload</em>. Many connections have asymmetric speeds
                (e.g., 300 down / 30 up). Upload is often the real constraint for calls.
              </p>
            </div>
            <div className="bg-green-50 border border-green-200 rounded-lg p-4">
              <h4 className="font-semibold text-green-900 mb-2">Multiple Devices</h4>
              <p className="text-sm text-green-800">
                A household with 4 people streaming simultaneously needs ~100 Mbps. But 4 people
                browsing and on video calls? 30-40 Mbps is plenty — the bandwidth needs don't stack
                the way most people expect.
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default SpeedNeedsSection;
