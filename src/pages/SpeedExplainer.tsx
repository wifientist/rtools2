import { useState, useEffect } from 'react';
import { useAuth } from '@/context/AuthContext';
import ContextSelector from '@/components/speed-explainer/ContextSelector';
import SpeedScoreSummary from '@/components/speed-explainer/SpeedScoreSummary';
import LinkQualitySection from '@/components/speed-explainer/LinkQualitySection';
import PhyVsRealSection from '@/components/speed-explainer/PhyVsRealSection';
import InterferenceSection from '@/components/speed-explainer/InterferenceSection';
import BackhaulSection from '@/components/speed-explainer/BackhaulSection';
import ClientLimitationsSection from '@/components/speed-explainer/ClientLimitationsSection';
import type { DiagnosticData } from '@/types/speedExplainer';
import type {
  SpeedExplainerContext,
  MockScenariosFile,
  MockScenario
} from '@/types/speedDiagnostics';
import mockScenariosData from '@/data/speed-explainer/mock-scenarios.json';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

type ViewMode = 'simple' | 'detailed';
type DataMode = 'demo' | 'live';

function SpeedExplainer() {
  const { activeControllerId } = useAuth();
  const [viewMode, setViewMode] = useState<ViewMode>('simple');
  const [dataMode, setDataMode] = useState<DataMode>('demo');
  const [context, setContext] = useState<SpeedExplainerContext>({
    scopeType: 'client',
    scopeId: null,
    scopeName: null,
    timeWindow: '15min'
  });
  const [diagnosticData, setDiagnosticData] = useState<DiagnosticData | null>(null);
  const [loading, setLoading] = useState(false);
  const [mockScenarios, setMockScenarios] = useState<MockScenario[]>([]);
  const [selectedScenario, setSelectedScenario] = useState<string | null>(null);

  // Load mock scenarios from imported JSON on component mount
  useEffect(() => {
    const data = mockScenariosData as MockScenariosFile;
    setMockScenarios(data.scenarios);

    // Auto-select the first scenario
    if (data.scenarios.length > 0) {
      setSelectedScenario(data.scenarios[0].id);
    }
  }, []);

  // Load demo data immediately when switching to demo mode or changing scenario
  useEffect(() => {
    if (dataMode === 'demo') {
      const scenario = mockScenarios.find(s => s.id === selectedScenario);
      if (scenario) {
        setContext(scenario.context);
        setDiagnosticData(scenario.data);
      }
    } else {
      // When switching to live mode, reset context and clear data
      setContext({
        scopeType: 'client',
        scopeId: null,
        scopeName: null,
        timeWindow: '15min'
      });
      setDiagnosticData(null);
    }
  }, [dataMode, selectedScenario, mockScenarios]);

  // Fetch diagnostic data when context changes (live mode only)
  useEffect(() => {
    if (dataMode === 'demo') {
      return; // Don't fetch in demo mode
    }

    if (!activeControllerId || !context.scopeId) {
      setDiagnosticData(null);
      return;
    }

    const fetchDiagnostics = async () => {
      setLoading(true);
      try {
        const endpoint = `${API_BASE_URL}/r1/${activeControllerId}/diagnostics/${context.scopeType}/${context.scopeId}?timeWindow=${context.timeWindow}`;
        const response = await fetch(endpoint, { credentials: 'include' });

        if (!response.ok) {
          console.error('API endpoint returned error:', response.status);
          setDiagnosticData(null);
        } else {
          const data: DiagnosticData = await response.json();
          setDiagnosticData(data);
        }
      } catch (error) {
        console.error('Error fetching diagnostic data:', error);
        setDiagnosticData(null);
      } finally {
        setLoading(false);
      }
    };

    fetchDiagnostics();
  }, [activeControllerId, context, dataMode]);

  return (
    <div className="min-h-screen bg-gray-50 p-6">
      {/* Floating Control Panel - Fixed to top right */}
      <div className="fixed top-20 right-6 z-50 flex items-center gap-4">
        {/* Data Mode Toggle */}
        <div className="flex items-center gap-2 bg-white px-4 py-2 rounded-lg shadow-lg border-2 border-gray-200">
          <button
            onClick={() => setDataMode('demo')}
            className={`px-4 py-2 rounded transition ${
              dataMode === 'demo'
                ? 'bg-purple-500 text-white'
                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
            }`}
          >
            🎭 Demo
          </button>
          <button
            onClick={() => setDataMode('live')}
            className={`px-4 py-2 rounded transition ${
              dataMode === 'live'
                ? 'bg-green-500 text-white'
                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
            }`}
          >
            🔴 Live
          </button>
        </div>

        {/* View Mode Toggle */}
        <div className="flex items-center gap-2 bg-white px-4 py-2 rounded-lg shadow-lg">
          <button
            onClick={() => setViewMode('simple')}
            className={`px-4 py-2 rounded transition ${
              viewMode === 'simple'
                ? 'bg-blue-500 text-white'
                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
            }`}
          >
            🔰 Simple
          </button>
          <button
            onClick={() => setViewMode('detailed')}
            className={`px-4 py-2 rounded transition ${
              viewMode === 'detailed'
                ? 'bg-blue-500 text-white'
                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
            }`}
          >
            🤓 Detailed
          </button>
        </div>
      </div>

      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-6">
          <div>
            <h1 className="text-3xl font-bold text-gray-900">Why Is My Speed Slow?</h1>
            <p className="text-gray-600 mt-1">Interactive Wi-Fi performance diagnostics and explanation</p>
          </div>
        </div>

        {/* Demo Mode Banner & Scenario Selector */}
        {dataMode === 'demo' && (
          <div className="bg-purple-50 border-2 border-purple-300 rounded-lg p-4 mb-6">
            <div className="flex items-start gap-3 mb-4">
              <span className="text-3xl">🎭</span>
              <div className="flex-1">
                <h3 className="font-semibold text-purple-900">Demo Mode Active</h3>
                <p className="text-purple-700 text-sm">
                  You're viewing example diagnostic data. Switch to <strong>Live</strong> mode to analyze real devices from your network.
                </p>
              </div>
            </div>

            {/* Scenario Selector */}
            {mockScenarios.length > 0 && (
              <div className="mt-4 pt-4 border-t border-purple-200">
                <label className="block text-sm font-medium text-purple-900 mb-2">
                  Select Demo Scenario:
                </label>
                <select
                  value={selectedScenario || ''}
                  onChange={(e) => setSelectedScenario(e.target.value)}
                  className="w-full border border-purple-300 rounded px-3 py-2 text-sm bg-white text-purple-900"
                >
                  {mockScenarios.map((scenario) => (
                    <option key={scenario.id} value={scenario.id}>
                      {scenario.name}
                    </option>
                  ))}
                </select>
                {mockScenarios.find(s => s.id === selectedScenario)?.description && (
                  <p className="text-xs text-purple-600 mt-2">
                    {mockScenarios.find(s => s.id === selectedScenario)?.description}
                  </p>
                )}
              </div>
            )}
          </div>
        )}

        {/* Context Selector - only show in live mode */}
        {dataMode === 'live' && (
          <ContextSelector
            context={context}
            onContextChange={setContext}
          />
        )}

        {/* Article-style introduction */}
        <div className="bg-white rounded-lg shadow p-6 mb-6">
          <h2 className="text-xl font-semibold mb-3">Why Wi-Fi Speed Is Complicated</h2>
          <p className="text-gray-700 leading-relaxed">
            When you run a speed test and see "slow" results, the cause could be anywhere in a chain of systems:
            your device's Wi-Fi radio, signal strength, channel congestion, interference, the access point itself,
            your network's backhaul, or even your internet connection. This page helps you understand which part
            is the real bottleneck—using <strong>{dataMode === 'demo' ? 'example data to demonstrate the concept' : 'live data from your network'}</strong>.
          </p>
        </div>

        {loading && (
          <div className="flex justify-center items-center py-12">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500"></div>
          </div>
        )}

        {diagnosticData && (
          <>
            {/* Speed Score Summary */}
            <SpeedScoreSummary
              data={diagnosticData.summary}
              context={context}
            />

            {/* Step 1: Link Quality */}
            <div className="mb-6">
              <LinkQualitySection
                data={diagnosticData.linkQuality}
                viewMode={viewMode}
                context={context}
              />
            </div>

            {/* Step 2: PHY vs Reality */}
            <div className="mb-6">
              <PhyVsRealSection
                data={diagnosticData.phyVsReal}
                viewMode={viewMode}
                context={context}
              />
            </div>

            {/* Step 3: Interference & Retries */}
            <div className="mb-6">
              <InterferenceSection
                data={diagnosticData.interference}
                viewMode={viewMode}
                context={context}
              />
            </div>

            {/* Step 4: Backhaul */}
            <div className="mb-6">
              <BackhaulSection
                data={diagnosticData.backhaul}
                viewMode={viewMode}
                context={context}
              />
            </div>

            {/* Step 5: Client Limitations */}
            <div className="mb-6">
              <ClientLimitationsSection
                data={diagnosticData.clientLimitations}
                viewMode={viewMode}
                context={context}
              />
            </div>
          </>
        )}

        {dataMode === 'live' && !context.scopeId && !loading && (
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-8 text-center">
            <p className="text-blue-900 text-lg">
              Select a client, access point, or SSID above to begin diagnostics
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

export default SpeedExplainer;
