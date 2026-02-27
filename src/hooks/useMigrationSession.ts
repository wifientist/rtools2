import { useEffect, useRef, useCallback, useState } from 'react';
import { apiGet, apiPost, apiPatch } from '@/utils/api';
import type { WizardState, WizardAction } from '@/types/szConfigMigration';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

export interface MigrationSessionSummary {
  id: number;
  status: string;
  created_at: string;
  updated_at: string;
  current_step: number;
  sz_zone_name: string | null;
  r1_venue_name: string | null;
  wlan_count: number | null;
  extraction_job_id: string | null;
  plan_job_id: string | null;
  execution_job_id: string | null;
  summary_json: Record<string, any> | null;
  // Full fields for resume
  sz_controller_id?: number | null;
  sz_domain_id?: string | null;
  sz_zone_id?: string | null;
  r1_controller_id?: number | null;
  r1_tenant_id?: string | null;
  r1_venue_id?: string | null;
  r1_snapshot_job_id?: string | null;
}

// Fields we track for sync — when these change, PATCH the session
function buildSyncPayload(state: WizardState): Record<string, any> {
  const statusMap: Record<number, string> = {
    1: 'draft',
    2: 'extracting',
    3: 'reviewing',
    4: 'reviewing',
    5: 'executing',
    6: 'completed',
  };

  const effectiveStep = state.highestStepReached ?? state.currentStep;
  return {
    current_step: effectiveStep,
    status: statusMap[effectiveStep] || 'draft',
    sz_zone_id: state.selectedZoneId,
    sz_zone_name: state.selectedZoneName,
    r1_controller_id: state.r1ControllerId,
    r1_tenant_id: state.tenantId,
    r1_venue_id: state.destVenueId,
    r1_venue_name: state.destVenueName,
    extraction_job_id: state.extractionJobId,
    r1_snapshot_job_id: state.r1SnapshotJobId,
    plan_job_id: state.planJobId,
    execution_job_id: state.finalJobId,
    wlan_count: state.snapshotSummary?.wlan_count ?? state.planResult?.unit_count ?? null,
  };
}

export function useMigrationSession(
  state: WizardState,
  dispatch: React.Dispatch<WizardAction>,
  szControllerId: number | null,
) {
  const [sessions, setSessions] = useState<MigrationSessionSummary[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const prevPayloadRef = useRef<string>('');
  const syncInFlightRef = useRef(false);

  // Fetch session list on mount
  useEffect(() => {
    fetchSessions();
  }, []);

  const fetchSessions = useCallback(async () => {
    setSessionsLoading(true);
    try {
      const data = await apiGet<MigrationSessionSummary[]>(`${API_URL}/sz-migration/sessions?limit=10`);
      setSessions(data);
    } catch {
      // Silently fail — sessions list is non-critical
    } finally {
      setSessionsLoading(false);
    }
  }, []);

  // Auto-create session when we have enough data (zone selected + extraction starting)
  useEffect(() => {
    if (state.sessionId || !szControllerId || !state.selectedZoneId) return;
    if (state.currentStep < 2) return; // Don't create until extraction starts

    createSession(szControllerId);
  }, [state.currentStep, state.selectedZoneId, szControllerId, state.sessionId]);

  const createSession = useCallback(async (controllerId: number) => {
    try {
      const data = await apiPost<MigrationSessionSummary>(`${API_URL}/sz-migration/sessions`, {
        sz_controller_id: controllerId,
        sz_domain_id: state.selectedDomainId,
        sz_zone_id: state.selectedZoneId,
        sz_zone_name: state.selectedZoneName,
      });
      dispatch({ type: 'SET_SESSION_ID', sessionId: data.id });
      fetchSessions(); // Refresh list
    } catch (e) {
      console.error('[session] Failed to create session:', e);
    }
  }, [state.selectedDomainId, state.selectedZoneId, state.selectedZoneName, dispatch, fetchSessions]);

  // Auto-sync state changes to DB (debounced)
  useEffect(() => {
    if (!state.sessionId) return;

    const payload = buildSyncPayload(state);
    const payloadStr = JSON.stringify(payload);

    // Skip if nothing changed
    if (payloadStr === prevPayloadRef.current) return;
    prevPayloadRef.current = payloadStr;

    // Debounce: wait 500ms before syncing
    const timer = setTimeout(() => {
      syncToDb(state.sessionId!, payload);
    }, 500);

    return () => clearTimeout(timer);
  }, [
    state.sessionId,
    state.currentStep,
    state.selectedZoneId,
    state.destVenueId,
    state.extractionJobId,
    state.r1SnapshotJobId,
    state.planJobId,
    state.finalJobId,
    state.r1ControllerId,
  ]);

  const syncToDb = useCallback(async (sessionId: number, payload: Record<string, any>) => {
    if (syncInFlightRef.current) return;
    syncInFlightRef.current = true;
    try {
      await apiPatch(`${API_URL}/sz-migration/sessions/${sessionId}`, payload);
    } catch (e) {
      console.error('[session] Sync failed:', e);
    } finally {
      syncInFlightRef.current = false;
    }
  }, []);

  // Save summary_json when plan completes (heavier payload, only on plan)
  useEffect(() => {
    if (!state.sessionId || !state.planResult) return;
    const summaryPayload = {
      summary_json: {
        plan_summary: state.planResult.summary,
        unit_count: state.planResult.unit_count,
        estimated_api_calls: state.planResult.estimated_api_calls,
        valid: state.planResult.valid,
        conflict_count: state.planResult.conflicts.length,
        action_count: state.planResult.actions.length,
      },
    };
    syncToDb(state.sessionId, summaryPayload);
  }, [state.sessionId, state.planResult]);

  // Load a session from history into wizard state
  const loadSession = useCallback(async (sessionId: number) => {
    try {
      const s = await apiGet<MigrationSessionSummary>(`${API_URL}/sz-migration/sessions/${sessionId}`);
      // Reset wizard then populate from DB session
      dispatch({ type: 'RESET' });

      // We can restore the structural fields; transient data (resolver, plan) will re-run
      dispatch({ type: 'SET_SESSION_ID', sessionId: s.id });

      if (s.sz_controller_id && s.sz_domain_id) {
        dispatch({
          type: 'SET_SOURCE',
          szControllerId: s.sz_controller_id,
          domainId: s.sz_domain_id,
          domainName: '', // Not stored in DB — will show as empty but functional
        });
      }
      if (s.sz_zone_id && s.sz_zone_name) {
        dispatch({ type: 'SET_ZONE', zoneId: s.sz_zone_id, zoneName: s.sz_zone_name });
      }
      if (s.extraction_job_id) {
        dispatch({ type: 'START_EXTRACTION', jobId: s.extraction_job_id });
        dispatch({ type: 'UPDATE_EXTRACTION', status: 'completed' });
      }
      if (s.r1_controller_id && s.r1_venue_id && s.r1_venue_name) {
        dispatch({
          type: 'SET_DESTINATION',
          r1ControllerId: s.r1_controller_id,
          tenantId: s.r1_tenant_id || null,
          venueId: s.r1_venue_id,
          venueName: s.r1_venue_name,
        });
      }
      if (s.execution_job_id) {
        dispatch({ type: 'SET_EXECUTION_STARTED', jobId: s.execution_job_id });
      }

      // Navigate to the saved step
      if (s.current_step > 1) {
        dispatch({ type: 'SET_STEP', step: s.current_step });
      }
    } catch (e) {
      console.error('[session] Failed to load session:', e);
    }
  }, [dispatch]);

  return {
    sessions,
    sessionsLoading,
    fetchSessions,
    loadSession,
    createSession,
  };
}
