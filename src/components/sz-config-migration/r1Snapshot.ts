import { apiFetch } from '@/utils/api';
import type { WizardState, WizardAction } from '@/types/szConfigMigration';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

/**
 * Read the destination venue as R1 has it now and make it the wizard's R1
 * snapshot. Destination takes the first one; the WLANs step re-reads on entry
 * because Create WLANs changes R1 (a stale snapshot showed 163 groups that
 * existed as still "to create").
 */
export async function captureR1Snapshot(
  state: WizardState, dispatch: React.Dispatch<WizardAction>,
): Promise<void> {
  if (!state.destVenueId || !state.r1ControllerId) return;
  const res = await apiFetch(`${API_URL}/sz-migration/r1-snapshot`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      controller_id: state.r1ControllerId,
      tenant_id: state.tenantId,
      venue_id: state.destVenueId,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `R1 snapshot failed: ${res.status}`);
  }
  const { job_id: jobId } = await res.json();
  const invRes = await apiFetch(`${API_URL}/sz-migration/r1-snapshot/${jobId}`);
  if (!invRes.ok) throw new Error('Failed to fetch R1 inventory');
  const inv = await invRes.json();
  dispatch({
    type: 'SET_R1_SNAPSHOT',
    jobId,
    summary: {
      venue_name: inv.venue_name || state.destVenueName || '',
      wifi_network_count: inv.wifi_networks?.length || 0,
      ap_group_count: inv.ap_groups?.length || 0,
      ap_count: inv.aps?.length || 0,
      dpsk_pool_count: inv.dpsk_pools?.length || 0,
      identity_group_count: inv.identity_groups?.length || 0,
    },
  });
}
