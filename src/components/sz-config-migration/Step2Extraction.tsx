import { useState, useEffect, useRef } from 'react';
import { Download, CheckCircle, AlertCircle, Loader2, ArrowRight } from 'lucide-react';
import { apiFetch } from '@/utils/api';
import type { WizardState, WizardAction, ExtractionProgress } from '@/types/szConfigMigration';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

interface Props {
  state: WizardState;
  dispatch: React.Dispatch<WizardAction>;
}

const PHASE_ORDER = ['zone', 'wlans', 'wlan_groups', 'ap_groups', 'aps', 'references', 'complete'];
const PHASE_LABELS: Record<string, string> = {
  starting: 'Initializing',
  zone: 'Zone Details',
  wlans: 'WLANs',
  wlan_groups: 'WLAN Groups',
  ap_groups: 'AP Groups',
  aps: 'Access Points',
  references: 'Referenced Objects',
  complete: 'Complete',
};

export default function Step2Extraction({ state, dispatch }: Props) {
  const [events, setEvents] = useState<string[]>([]);
  const [currentPhase, setCurrentPhase] = useState<string>('starting');
  const eventSourceRef = useRef<EventSource | null>(null);
  const eventsEndRef = useRef<HTMLDivElement>(null);

  // On mount: if we have a job ID but no summary (e.g. page reload), poll for status
  useEffect(() => {
    if (state.extractionJobId && !state.snapshotSummary && state.extractionStatus !== 'running') {
      pollStatus();
    }
  }, []);

  useEffect(() => {
    if (!state.extractionJobId || state.extractionStatus !== 'running') return;

    const es = new EventSource(
      `${API_URL}/sz-migration/extract/${state.extractionJobId}/sse`,
      { withCredentials: true }
    );
    eventSourceRef.current = es;

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        const phase = data.phase || data.progress?.phase;
        const message = data.message || data.progress?.message || '';

        if (phase) setCurrentPhase(phase);

        if (message) {
          setEvents(prev => [...prev.slice(-49), `[${new Date().toLocaleTimeString()}] ${message}`]);
        }

        if (phase === 'complete') {
          const summary = data.data || data.snapshot_summary || data;
          dispatch({
            type: 'UPDATE_EXTRACTION',
            status: 'completed',
            summary: {
              zone_name: summary.zone_name || '',
              wlan_count: summary.wlan_count || summary.wlans || 0,
              wlan_group_count: summary.wlan_group_count || summary.wlan_groups || 0,
              ap_group_count: summary.ap_group_count || summary.ap_groups || 0,
              ap_count: summary.ap_count || summary.aps || 0,
              referenced_objects_count: summary.referenced_objects_count || summary.referenced_objects || 0,
              warning_count: summary.warning_count || summary.warnings || 0,
            },
          });
          es.close();
        } else if (phase === 'error') {
          dispatch({
            type: 'UPDATE_EXTRACTION',
            status: 'failed',
          });
          dispatch({ type: 'SET_ERROR', error: message || 'Extraction failed' });
          es.close();
        } else {
          dispatch({
            type: 'UPDATE_EXTRACTION',
            status: 'running',
            progress: { phase, message, data: data.data || {} },
          });
        }
      } catch {
        // Non-JSON data (keepalive), ignore
      }
    };

    es.onerror = () => {
      // Check if extraction might have completed via polling
      if (state.extractionStatus === 'running') {
        pollStatus();
      }
      es.close();
    };

    return () => {
      es.close();
      eventSourceRef.current = null;
    };
  }, [state.extractionJobId]);

  // Auto-scroll events
  useEffect(() => {
    eventsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events]);

  const pollStatus = async () => {
    if (!state.extractionJobId) return;
    try {
      const res = await apiFetch(
        `${API_URL}/sz-migration/extract/${state.extractionJobId}/status`,
      );
      if (res.ok) {
        const data = await res.json();
        if (data.status === 'completed' && data.snapshot_summary) {
          const s = data.snapshot_summary;
          dispatch({
            type: 'UPDATE_EXTRACTION',
            status: 'completed',
            summary: {
              zone_name: s.zone_name || '',
              wlan_count: s.wlan_count || s.wlans || 0,
              wlan_group_count: s.wlan_group_count || s.wlan_groups || 0,
              ap_group_count: s.ap_group_count || s.ap_groups || 0,
              ap_count: s.ap_count || s.aps || 0,
              referenced_objects_count: s.referenced_objects_count || s.referenced_objects || 0,
              warning_count: s.warning_count || s.warnings || 0,
            },
          });
        } else if (data.status === 'failed') {
          dispatch({ type: 'UPDATE_EXTRACTION', status: 'failed' });
          dispatch({ type: 'SET_ERROR', error: data.error || 'Extraction failed' });
        }
      }
    } catch {
      // Polling failure, non-critical
    }
  };

  const downloadSnapshot = () => {
    if (!state.extractionJobId) return;
    window.open(`${API_URL}/sz-migration/snapshot/${state.extractionJobId}/download`, '_blank');
  };

  const phaseIndex = PHASE_ORDER.indexOf(currentPhase);

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-lg shadow p-6">
        <h3 className="text-lg font-semibold mb-4">Deep Extraction</h3>

        {/* Phase progress */}
        <div className="mb-6">
          <div className="flex items-center gap-1 mb-3">
            {PHASE_ORDER.map((phase, idx) => {
              const isDone = idx < phaseIndex || state.extractionStatus === 'completed';
              const isCurrent = idx === phaseIndex && state.extractionStatus === 'running';
              return (
                <div key={phase} className="flex items-center gap-1 flex-1">
                  <div
                    className={`h-2 flex-1 rounded-full transition-colors ${
                      isDone ? 'bg-green-500' : isCurrent ? 'bg-blue-500 animate-pulse' : 'bg-gray-200'
                    }`}
                  />
                </div>
              );
            })}
          </div>
          <div className="flex justify-between text-xs text-gray-500">
            {PHASE_ORDER.map(phase => (
              <span key={phase} className={currentPhase === phase ? 'font-semibold text-blue-600' : ''}>
                {PHASE_LABELS[phase] || phase}
              </span>
            ))}
          </div>
        </div>

        {/* Status indicator */}
        <div className="flex items-center gap-2 mb-4">
          {state.extractionStatus === 'running' && (
            <>
              <Loader2 size={18} className="text-blue-600 animate-spin" />
              <span className="text-blue-600 font-medium text-sm">
                {state.extractionProgress?.message || 'Extracting...'}
              </span>
            </>
          )}
          {state.extractionStatus === 'completed' && (
            <>
              <CheckCircle size={18} className="text-green-600" />
              <span className="text-green-600 font-medium text-sm">Extraction complete</span>
            </>
          )}
          {state.extractionStatus === 'failed' && (
            <>
              <AlertCircle size={18} className="text-red-600" />
              <span className="text-red-600 font-medium text-sm">Extraction failed</span>
            </>
          )}
        </div>

        {/* Live events log */}
        {events.length > 0 && (
          <div className="bg-gray-900 text-green-400 rounded-lg p-3 text-xs font-mono max-h-48 overflow-y-auto mb-4">
            {events.map((e, i) => (
              <div key={i}>{e}</div>
            ))}
            <div ref={eventsEndRef} />
          </div>
        )}

        {/* Summary */}
        {state.extractionStatus === 'completed' && state.snapshotSummary && (
          <div className="bg-green-50 border border-green-200 rounded-lg p-4 mb-4">
            <h4 className="font-semibold text-green-800 mb-2">Snapshot Summary</h4>
            <div className="grid grid-cols-3 md:grid-cols-6 gap-3 text-sm">
              <SummaryItem label="WLANs" value={state.snapshotSummary.wlan_count} />
              <SummaryItem label="WLAN Groups" value={state.snapshotSummary.wlan_group_count} />
              <SummaryItem label="AP Groups" value={state.snapshotSummary.ap_group_count} />
              <SummaryItem label="APs" value={state.snapshotSummary.ap_count} />
              <SummaryItem label="Refs" value={state.snapshotSummary.referenced_objects_count} />
              <SummaryItem label="Warnings" value={state.snapshotSummary.warning_count} color={state.snapshotSummary.warning_count > 0 ? 'amber' : undefined} />
            </div>
          </div>
        )}

        {/* Actions */}
        <div className="flex items-center gap-3">
          {state.extractionStatus === 'completed' && (
            <>
              <button
                onClick={downloadSnapshot}
                className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm font-medium flex items-center gap-2"
              >
                <Download size={14} />
                Download Snapshot
              </button>
              <button
                onClick={() => dispatch({ type: 'SET_STEP', step: 3 })}
                className="px-6 py-2 bg-green-600 text-white rounded hover:bg-green-700 font-semibold text-sm flex items-center gap-2"
              >
                Continue
                <ArrowRight size={14} />
              </button>
            </>
          )}
          {state.extractionStatus === 'failed' && (
            <button
              onClick={() => dispatch({ type: 'SET_STEP', step: 1 })}
              className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 text-sm font-medium"
            >
              Back to Source Selection
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function SummaryItem({ label, value, color }: { label: string; value: number; color?: string }) {
  const textColor = color === 'amber' ? 'text-amber-700' : 'text-green-700';
  return (
    <div className="text-center">
      <div className={`text-lg font-bold ${textColor}`}>{value}</div>
      <div className="text-xs text-gray-600">{label}</div>
    </div>
  );
}
