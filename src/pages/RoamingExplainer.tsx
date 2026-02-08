import { useState, useEffect } from 'react';
import {
  RoamingScoreSummary,
  WhatIsRoamingSection,
  StickyClientsSection,
  MDUProblemsSection,
  TroubleshootingSection,
} from '@/components/roaming-explainer';
import type {
  RoamingDiagnosticData,
  RoamingScenario,
  RoamingScenariosFile,
  ViewMode,
  DataMode,
} from '@/types/roamingExplainer';
import mockScenariosData from '@/data/roaming-explainer/mock-scenarios.json';

function RoamingExplainer() {
  const [viewMode, setViewMode] = useState<ViewMode>('simple');
  const [dataMode, setDataMode] = useState<DataMode>('demo');
  const [diagnosticData, setDiagnosticData] = useState<RoamingDiagnosticData | null>(null);
  const [mockScenarios, setMockScenarios] = useState<RoamingScenario[]>([]);
  const [selectedScenario, setSelectedScenario] = useState<string | null>(null);

  // Load mock scenarios on mount
  useEffect(() => {
    const data = mockScenariosData as RoamingScenariosFile;
    setMockScenarios(data.scenarios);

    // Auto-select first scenario
    if (data.scenarios.length > 0) {
      setSelectedScenario(data.scenarios[0].id);
    }
  }, []);

  // Load data when scenario changes
  useEffect(() => {
    if (dataMode === 'demo') {
      const scenario = mockScenarios.find(s => s.id === selectedScenario);
      if (scenario) {
        setDiagnosticData(scenario.data);
      }
    } else {
      // Live mode - would fetch from API
      setDiagnosticData(null);
    }
  }, [dataMode, selectedScenario, mockScenarios]);

  return (
    <div className="min-h-screen bg-gray-50 p-6">
      {/* Floating Control Panel */}
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
            Demo
          </button>
          <button
            onClick={() => setDataMode('live')}
            disabled
            className={`px-4 py-2 rounded transition ${
              dataMode === 'live'
                ? 'bg-green-500 text-white'
                : 'bg-gray-100 text-gray-400 cursor-not-allowed'
            }`}
            title="Live mode coming soon"
          >
            Live (Soon)
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
            Simple
          </button>
          <button
            onClick={() => setViewMode('detailed')}
            className={`px-4 py-2 rounded transition ${
              viewMode === 'detailed'
                ? 'bg-blue-500 text-white'
                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
            }`}
          >
            Detailed
          </button>
        </div>
      </div>

      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-6">
          <h1 className="text-3xl font-bold text-gray-900">Why Won't My Device Roam?</h1>
          <p className="text-gray-600 mt-1">Understanding WiFi roaming, sticky clients, and MDU challenges</p>
        </div>

        {/* Demo Mode Banner */}
        {dataMode === 'demo' && (
          <div className="bg-purple-50 border-2 border-purple-300 rounded-lg p-4 mb-6">
            <div className="flex items-start gap-3 mb-4">
              <span className="text-3xl">🏢</span>
              <div className="flex-1">
                <h3 className="font-semibold text-purple-900">Demo Mode - MDU Scenarios</h3>
                <p className="text-purple-700 text-sm">
                  Explore common roaming problems in multi-dwelling unit environments with example data.
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

        {/* Live Mode Placeholder */}
        {dataMode === 'live' && (
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-8 text-center mb-6">
            <p className="text-blue-900 text-lg">
              Live roaming data will be available in a future update.
              For now, explore the demo scenarios to learn about common roaming issues.
            </p>
          </div>
        )}

        {/* Introduction */}
        <div className="bg-white rounded-lg shadow p-6 mb-6">
          <h2 className="text-xl font-semibold mb-3">The Roaming Paradox</h2>
          <p className="text-gray-700 leading-relaxed mb-4">
            One of the most misunderstood aspects of WiFi is <strong>who controls roaming</strong>. Many people
            assume that access points "hand off" clients like a cell tower does. In reality,
            <strong className="text-blue-700"> the client device decides when and where to roam</strong>—and
            many devices are very reluctant to do so.
          </p>
          <p className="text-gray-700 leading-relaxed">
            This page helps you understand why devices stick to distant APs, what the 802.11k/v/r standards
            do to help, and why <strong className="text-orange-700">MDU deployments are particularly challenging</strong>.
          </p>
        </div>

        {/* Diagnostic Sections */}
        {diagnosticData && (
          <>
            {/* Score Summary */}
            <RoamingScoreSummary
              health={diagnosticData.summary.health}
              score={diagnosticData.summary.score}
              headline={diagnosticData.summary.headline}
              subheadline={diagnosticData.summary.subheadline}
            />

            {/* What Is Roaming */}
            <div className="mb-6">
              <WhatIsRoamingSection
                data={diagnosticData.whatIsRoaming}
                viewMode={viewMode}
              />
            </div>

            {/* Sticky Clients */}
            <div className="mb-6">
              <StickyClientsSection
                data={diagnosticData.stickyClients}
                viewMode={viewMode}
              />
            </div>

            {/* MDU Problems */}
            <div className="mb-6">
              <MDUProblemsSection
                data={diagnosticData.mduProblems}
                viewMode={viewMode}
              />
            </div>

            {/* Troubleshooting */}
            <div className="mb-6">
              <TroubleshootingSection
                data={diagnosticData.troubleshooting}
                viewMode={viewMode}
              />
            </div>
          </>
        )}

        {/* Quick Reference Card */}
        <div className="bg-gradient-to-r from-blue-600 to-purple-600 rounded-lg shadow-lg p-6 text-white">
          <h3 className="text-xl font-bold mb-4">Quick Reference: 802.11k/v/r</h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div>
              <div className="text-2xl mb-2">📋 802.11k</div>
              <div className="font-semibold mb-1">Neighbor Reports</div>
              <p className="text-sm text-blue-100">
                APs tell clients about nearby APs, reducing scan time when the client decides to roam.
              </p>
            </div>
            <div>
              <div className="text-2xl mb-2">🔀 802.11v</div>
              <div className="font-semibold mb-1">BSS Transition</div>
              <p className="text-sm text-blue-100">
                APs can suggest a better AP to connect to. Helps nudge sticky clients to roam.
              </p>
            </div>
            <div>
              <div className="text-2xl mb-2">⚡ 802.11r</div>
              <div className="font-semibold mb-1">Fast Transition</div>
              <p className="text-sm text-blue-100">
                Pre-negotiates security keys. Reduces roam time from ~400ms to &lt;50ms. Critical for VoIP.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default RoamingExplainer;
