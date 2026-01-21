import { useState, useEffect, useCallback } from "react";
import { useAuth } from "@/context/AuthContext";
import { apiFetch } from "@/utils/api";
import SingleVenueSelector from "@/components/SingleVenueSelector";
import DpskPoolSelector from "@/components/DpskPoolSelector";
import {
  Plus,
  RefreshCw,
  Trash2,
  Play,
  AlertTriangle,
  CheckCircle,
  Check,
  Clock,
  Settings,
  Edit2,
  Copy,
  Key,
  Shield,
  ChevronDown,
  ChevronUp,
  Users,
  Database,
  Layers,
} from "lucide-react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

// Types
interface SourcePool {
  id: number;
  pool_id: string;
  pool_name: string | null;
  identity_group_id: string | null;
  identity_group_name: string | null;
  last_sync_at: string | null;
  passphrase_count: number;
  discovered_at: string | null;
  // Pool details (populated when refresh_counts=true)
  passphrase_format: string | null;  // e.g., "KEYBOARD_FRIENDLY"
  passphrase_length: number | null;
  device_count_limit: number | null;  // null = unlimited
  expiration_type: string | null;  // e.g., "NEVER", "FIXED_DATE"
}

interface SyncEvent {
  id: number;
  event_type: string;
  status: string;
  added_count: number;
  updated_count: number;
  flagged_for_removal: number;
  orphans_found: number;
  errors: string[];
  started_at: string;
  completed_at: string | null;
}

interface Orchestrator {
  id: number;
  name: string;
  controller_id: number;
  tenant_id: string | null;
  venue_id: string | null;
  site_wide_pool_id: string;
  site_wide_pool_name: string | null;
  sync_interval_minutes: number;
  enabled: boolean;
  auto_delete: boolean;
  auto_discover_enabled: boolean;
  include_patterns: string[];
  exclude_patterns: string[];
  webhook_id: string | null;
  webhook_path: string | null;
  webhook_secret_configured: boolean;
  created_at: string;
  last_sync_at: string | null;
  last_discovery_at: string | null;
  source_pool_count: number;
  flagged_count: number;
  orphan_count: number;
}

interface OrchestratorDetail extends Orchestrator {
  source_pools: SourcePool[];
  recent_sync_events: SyncEvent[];
}

interface PassphraseMapping {
  id: number;
  source_pool_id: string;
  source_pool_name: string | null;
  source_passphrase_id: string | null;
  source_username: string | null;
  target_passphrase_id: string | null;
  sync_status: string;
  vlan_id: number | null;
  passphrase_preview: string | null;
  suggested_source_pool_id: string | null;
  created_at: string;
  last_synced_at: string | null;
  flagged_at: string | null;
}

interface DPSKPoolDetails {
  id: string;
  name: string;
  totalPassphrases?: number;
  maxDevicesPerPassphrase?: number;
  passphraseLength?: number;
  passphraseType?: string;  // e.g., "KEYBOARD_FRIENDLY", "ALPHANUMERIC"
  passphraseExpiration?: string | number | null;  // expiration setting
  defaultAccess?: string;  // e.g., "REJECT", "ACCEPT"
  adaptivePolicySetId?: string;
  adaptivePolicySetName?: string;
  identityGroupId?: string;
  identityGroupName?: string;
  createdAt?: string;
  modifiedAt?: string;
}

interface OrphanIdentity {
  id: string;
  name: string | null;
  display_name: string | null;
  description: string | null;
  vlan: number | null;
  created_at: string | null;
  also_exists_in: string[];  // List of other pool names where this identity exists
}

interface PoolIdentityAuditResult {
  pool_id: string;
  pool_name: string | null;
  pool_type: "site_wide" | "source";
  identity_group_id: string | null;
  identity_group_name: string | null;
  total_identities: number;
  total_passphrases: number;
  orphan_identities: number;
  orphans: OrphanIdentity[];
}

interface IdentityAuditResult {
  orchestrator_id: number;
  // Summary totals across all pools
  total_pools_audited: number;
  total_identities: number;
  total_passphrases: number;
  total_orphan_identities: number;
  // Per-pool breakdown
  site_wide_audit: PoolIdentityAuditResult | null;
  source_pool_audits: PoolIdentityAuditResult[];
  // Legacy fields for backwards compatibility
  identity_group_id: string | null;
  identity_group_name: string | null;
  orphan_identities: number;
  orphans: OrphanIdentity[];
}

function DPSKOrchestrator() {
  const { activeControllerId, controllers } = useAuth();

  // List of orchestrators
  const [orchestrators, setOrchestrators] = useState<Orchestrator[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Selected orchestrator for detail view
  const [selectedOrchestrator, setSelectedOrchestrator] = useState<OrchestratorDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Create modal
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [createLoading, setCreateLoading] = useState(false);

  // Flagged/orphan modal
  const [showFlaggedModal, setShowFlaggedModal] = useState(false);
  const [flaggedItems, setFlaggedItems] = useState<PassphraseMapping[]>([]);
  const [flaggedLoading, setFlaggedLoading] = useState(false);

  // Copy-to-source state (for orphan items)
  const [copyToSourceItem, setCopyToSourceItem] = useState<number | null>(null);  // mapping ID with dropdown open
  const [copyToSourceLoading, setCopyToSourceLoading] = useState(false);

  // Identity audit state
  const [showIdentityAuditModal, setShowIdentityAuditModal] = useState(false);
  const [identityAuditResult, setIdentityAuditResult] = useState<IdentityAuditResult | null>(null);
  const [identityAuditLoading, setIdentityAuditLoading] = useState(false);
  const [deleteIdentityLoading, setDeleteIdentityLoading] = useState<string | null>(null);
  const [refreshIdentityGroupsLoading, setRefreshIdentityGroupsLoading] = useState(false);

  // Create form state
  const [createForm, setCreateForm] = useState({
    name: "",
    venue_id: null as string | null,
    venue_name: null as string | null,
    site_wide_pool_id: "",
    site_wide_pool_name: "",
    sync_interval_minutes: 30,
    source_pool_filter: "Unit*",  // Used to filter source pools in selector
    source_pools: [] as { id: string; name: string }[],
  });

  // Edit modal state
  const [showEditModal, setShowEditModal] = useState(false);
  const [editLoading, setEditLoading] = useState(false);
  const [editForm, setEditForm] = useState({
    name: "",
    sync_interval_minutes: 30,
  });

  // Webhook state
  const [generatedSecret, setGeneratedSecret] = useState<string | null>(null);
  const [webhookLoading, setWebhookLoading] = useState(false);
  const [webhookExpanded, setWebhookExpanded] = useState(true);

  // Edit pools modal state
  const [showEditPoolsModal, setShowEditPoolsModal] = useState(false);
  const [editPoolsLoading, setEditPoolsLoading] = useState(false);
  const [editPools, setEditPools] = useState<{ id: string; name: string }[]>([]);

  // Expanded warnings state (tracks which sync event warnings are expanded)
  const [expandedWarnings, setExpandedWarnings] = useState<Set<number>>(new Set());

  // Destination pool details
  const [destinationPool, setDestinationPool] = useState<DPSKPoolDetails | null>(null);
  const [destinationPoolLoading, setDestinationPoolLoading] = useState(false);

  const activeController = controllers.find((c) => c.id === activeControllerId);
  const tenantId = activeController?.r1_tenant_id || null;

  // Fetch orchestrators list
  const fetchOrchestrators = useCallback(async () => {
    if (!activeControllerId) return;

    setLoading(true);
    setError("");

    try {
      const response = await fetch(`${API_BASE_URL}/api/orchestrators/`, {
        credentials: "include",
      });

      if (!response.ok) {
        throw new Error("Failed to fetch orchestrators");
      }

      const data = await response.json();
      setOrchestrators(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [activeControllerId]);

  // Fetch destination pool details from RuckusONE
  const fetchDestinationPoolDetails = async (poolId: string, controllerId: number) => {
    setDestinationPoolLoading(true);
    try {
      const params = new URLSearchParams();
      if (tenantId) params.append("tenant_id", tenantId);
      params.append("include_passphrase_count", "true");

      const response = await fetch(
        `${API_BASE_URL}/r1/${controllerId}/dpsk/pools/${poolId}?${params}`,
        { credentials: "include" }
      );

      if (!response.ok) {
        console.warn("Failed to fetch destination pool details");
        return;
      }

      const data = await response.json();

      // Map API fields to our interface
      // API returns: passphraseFormat, policyDefaultAccess (bool), deviceCountLimit, policySetId, expirationType
      setDestinationPool({
        id: data.id,
        name: data.name,
        totalPassphrases: data.passphraseCount ?? data.totalPassphrases,
        maxDevicesPerPassphrase: data.deviceCountLimit ?? data.maxDevicesPerPassphrase ?? data.maxDevices,
        passphraseLength: data.passphraseLength,
        passphraseType: data.passphraseFormat ?? data.passphraseType,
        passphraseExpiration: data.expirationType ?? data.passphraseExpiration,
        defaultAccess: data.policyDefaultAccess === true ? "ACCEPT" : data.policyDefaultAccess === false ? "REJECT" : data.defaultAccess,
        adaptivePolicySetId: data.policySetId ?? data.adaptivePolicySetId,
        adaptivePolicySetName: data.policySetName ?? data.adaptivePolicySetName ?? data.adaptivePolicySet,
        identityGroupId: data.identityGroupId,
        identityGroupName: data.identityGroupName,
        createdAt: data.createdDate,
        modifiedAt: data.lastModifiedDate ?? data.modifiedDate,
      });
    } catch (err) {
      console.warn("Error fetching destination pool details:", err);
    } finally {
      setDestinationPoolLoading(false);
    }
  };

  // Fetch orchestrator detail
  const fetchOrchestratorDetail = async (id: number, refreshCounts: boolean = false) => {
    setDetailLoading(true);
    setDestinationPool(null); // Reset destination pool when switching orchestrators
    try {
      const params = new URLSearchParams();
      if (refreshCounts) params.append("refresh_counts", "true");
      const url = `${API_BASE_URL}/api/orchestrators/${id}${params.toString() ? `?${params}` : ""}`;

      const response = await fetch(url, {
        credentials: "include",
      });

      if (!response.ok) {
        throw new Error("Failed to fetch orchestrator details");
      }

      const data = await response.json();
      setSelectedOrchestrator(data);

      // Also fetch destination pool details
      if (data.site_wide_pool_id && data.controller_id) {
        fetchDestinationPoolDetails(data.site_wide_pool_id, data.controller_id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setDetailLoading(false);
    }
  };

  // Create orchestrator
  const createOrchestrator = async () => {
    if (!activeControllerId) return;

    setCreateLoading(true);
    try {
      const payload = {
        name: createForm.name,
        controller_id: activeControllerId,
        tenant_id: tenantId,
        venue_id: createForm.venue_id,
        site_wide_pool_id: createForm.site_wide_pool_id,
        site_wide_pool_name: createForm.site_wide_pool_name,
        sync_interval_minutes: createForm.sync_interval_minutes,
        source_pools: createForm.source_pools.map((pool) => ({
          pool_id: pool.id,
          pool_name: pool.name,
        })),
      };

      const response = await fetch(`${API_BASE_URL}/api/orchestrators/`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const errData = await response.json();
        throw new Error(errData.detail || "Failed to create orchestrator");
      }

      setShowCreateModal(false);
      resetCreateForm();
      fetchOrchestrators();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setCreateLoading(false);
    }
  };

  // Delete orchestrator
  const deleteOrchestrator = async (id: number) => {
    if (!confirm("Are you sure you want to delete this orchestrator?")) return;

    try {
      const response = await fetch(`${API_BASE_URL}/api/orchestrators/${id}`, {
        method: "DELETE",
        credentials: "include",
      });

      if (!response.ok) {
        throw new Error("Failed to delete orchestrator");
      }

      setSelectedOrchestrator(null);
      fetchOrchestrators();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    }
  };

  // Trigger manual sync
  const triggerSync = async (id: number) => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/orchestrators/${id}/sync`, {
        method: "POST",
        credentials: "include",
      });

      if (!response.ok) {
        throw new Error("Failed to trigger sync");
      }

      // Refresh detail view after a short delay
      setTimeout(() => fetchOrchestratorDetail(id), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    }
  };

  // Toggle enabled state
  const toggleEnabled = async (id: number, currentState: boolean) => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/orchestrators/${id}`, {
        method: "PUT",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !currentState }),
      });

      if (!response.ok) {
        throw new Error("Failed to update orchestrator");
      }

      fetchOrchestrators();
      if (selectedOrchestrator?.id === id) {
        fetchOrchestratorDetail(id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    }
  };

  // Fetch flagged/orphan items
  const fetchFlagged = async (id: number) => {
    setFlaggedLoading(true);
    try {
      const response = await fetch(`${API_BASE_URL}/api/orchestrators/${id}/flagged`, {
        credentials: "include",
      });

      if (!response.ok) {
        throw new Error("Failed to fetch flagged items");
      }

      const data = await response.json();
      setFlaggedItems(data);
      setShowFlaggedModal(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setFlaggedLoading(false);
    }
  };

  // Resolve flagged item
  const resolveFlagged = async (orchestratorId: number, mappingId: number, action: string) => {
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/orchestrators/${orchestratorId}/flagged/${mappingId}/resolve?action=${action}`,
        {
          method: "POST",
          credentials: "include",
        }
      );

      if (!response.ok) {
        throw new Error("Failed to resolve flagged item");
      }

      // Refresh flagged items
      fetchFlagged(orchestratorId);
      fetchOrchestratorDetail(orchestratorId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    }
  };

  // Copy orphan passphrase to a source pool (creates proper mapping)
  const copyToSource = async (orchestratorId: number, mappingId: number, targetPoolId: string) => {
    setCopyToSourceLoading(true);
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/orchestrators/${orchestratorId}/orphans/${mappingId}/copy-to-source`,
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ target_pool_id: targetPoolId }),
        }
      );

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || "Failed to copy to source pool");
      }

      // Success - close dropdown and refresh
      setCopyToSourceItem(null);
      fetchFlagged(orchestratorId);
      fetchOrchestratorDetail(orchestratorId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setCopyToSourceLoading(false);
    }
  };

  // Run identity audit
  const runIdentityAudit = async (orchestratorId: number) => {
    setIdentityAuditLoading(true);
    setShowIdentityAuditModal(true);
    setIdentityAuditResult(null);
    try {
      // Use apiFetch with auto token refresh on 401
      const response = await apiFetch(
        `${API_BASE_URL}/api/orchestrators/${orchestratorId}/identity-audit`
      );

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || "Failed to run identity audit");
      }

      const data = await response.json();
      setIdentityAuditResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
      setShowIdentityAuditModal(false);
    } finally {
      setIdentityAuditLoading(false);
    }
  };

  // Refresh identity group links for all pools
  const refreshIdentityGroups = async (orchestratorId: number) => {
    setRefreshIdentityGroupsLoading(true);
    try {
      const response = await apiFetch(
        `${API_BASE_URL}/api/orchestrators/${orchestratorId}/refresh-identity-groups`,
        { method: "POST" }
      );

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || "Failed to refresh identity groups");
      }

      const result = await response.json();

      // Refresh the orchestrator detail to show updated links
      await fetchOrchestratorDetail(orchestratorId);

      // Show success message
      if (result.updated_count > 0) {
        alert(`Updated ${result.updated_count} pool(s) with identity group links. You can now run Identity Audit.`);
      } else if (result.errors?.length > 0) {
        alert(`No updates made. Errors: ${result.errors.map((e: any) => e.error).join(', ')}`);
      } else {
        alert('All pools already have identity group links configured.');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setRefreshIdentityGroupsLoading(false);
    }
  };

  // Delete orphan identity
  const deleteOrphanIdentity = async (orchestratorId: number, identityId: string, poolId?: string) => {
    setDeleteIdentityLoading(identityId);
    try {
      const url = poolId
        ? `${API_BASE_URL}/api/orchestrators/${orchestratorId}/identity-audit/${identityId}?pool_id=${poolId}`
        : `${API_BASE_URL}/api/orchestrators/${orchestratorId}/identity-audit/${identityId}`;

      const response = await fetch(url, {
        method: "DELETE",
        credentials: "include",
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || "Failed to delete identity");
      }

      // Refresh audit results
      runIdentityAudit(orchestratorId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setDeleteIdentityLoading(null);
    }
  };

  // Bulk delete orphan identities
  const bulkDeleteOrphanIdentities = async (orchestratorId: number, identityIds: string[], poolId?: string) => {
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/orchestrators/${orchestratorId}/identity-audit/bulk-delete`,
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ identity_ids: identityIds, pool_id: poolId }),
        }
      );

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || "Failed to bulk delete identities");
      }

      const result = await response.json();
      // Refresh audit results
      runIdentityAudit(orchestratorId);
      return result;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
      throw err;
    }
  };

  const resetCreateForm = () => {
    setCreateForm({
      name: "",
      venue_id: null,
      venue_name: null,
      site_wide_pool_id: "",
      site_wide_pool_name: "",
      sync_interval_minutes: 30,
      source_pool_filter: "Unit*",
      source_pools: [],
    });
  };

  // Open edit modal with current values
  const openEditModal = () => {
    if (!selectedOrchestrator) return;
    setEditForm({
      name: selectedOrchestrator.name,
      sync_interval_minutes: selectedOrchestrator.sync_interval_minutes,
    });
    setShowEditModal(true);
  };

  // Update orchestrator
  const updateOrchestrator = async () => {
    if (!selectedOrchestrator) return;

    setEditLoading(true);
    try {
      const response = await fetch(`${API_BASE_URL}/api/orchestrators/${selectedOrchestrator.id}`, {
        method: "PUT",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: editForm.name,
          sync_interval_minutes: editForm.sync_interval_minutes,
        }),
      });

      if (!response.ok) {
        const errData = await response.json();
        throw new Error(errData.detail || "Failed to update orchestrator");
      }

      setShowEditModal(false);
      fetchOrchestratorDetail(selectedOrchestrator.id);
      fetchOrchestrators();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setEditLoading(false);
    }
  };

  // Generate webhook secret
  const generateWebhookSecret = async () => {
    if (!selectedOrchestrator) return;

    setWebhookLoading(true);
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/orchestrators/${selectedOrchestrator.id}/webhook/generate-secret`,
        {
          method: "POST",
          credentials: "include",
        }
      );

      if (!response.ok) {
        throw new Error("Failed to generate webhook secret");
      }

      const data = await response.json();
      setGeneratedSecret(data.webhook_secret);
      fetchOrchestratorDetail(selectedOrchestrator.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setWebhookLoading(false);
    }
  };

  // Clear webhook secret
  const clearWebhookSecret = async () => {
    if (!selectedOrchestrator) return;
    if (!confirm("Are you sure you want to clear the webhook secret? Signature verification will be disabled.")) return;

    setWebhookLoading(true);
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/orchestrators/${selectedOrchestrator.id}/webhook/secret`,
        {
          method: "DELETE",
          credentials: "include",
        }
      );

      if (!response.ok) {
        throw new Error("Failed to clear webhook secret");
      }

      setGeneratedSecret(null);
      fetchOrchestratorDetail(selectedOrchestrator.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setWebhookLoading(false);
    }
  };

  // Copy to clipboard
  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
  };

  // Open edit pools modal
  const openEditPoolsModal = () => {
    if (!selectedOrchestrator) return;
    // Initialize with current source pools (id and name)
    const currentPools = selectedOrchestrator.source_pools.map(p => ({
      id: p.pool_id,
      name: p.pool_name || p.pool_id
    }));
    setEditPools(currentPools);
    setShowEditPoolsModal(true);
  };

  // Save source pools
  const saveSourcePools = async () => {
    if (!selectedOrchestrator) return;

    setEditPoolsLoading(true);
    const payload = {
      pools: editPools.map(p => ({ pool_id: p.id, pool_name: p.name }))
    };

    try {
      const response = await fetch(
        `${API_BASE_URL}/api/orchestrators/${selectedOrchestrator.id}/source-pools`,
        {
          method: "PUT",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        }
      );

      const responseData = await response.json();

      if (!response.ok) {
        throw new Error(responseData.detail || "Failed to update source pools");
      }

      setShowEditPoolsModal(false);
      // Fetch updated data
      await fetchOrchestratorDetail(selectedOrchestrator.id);
      await fetchOrchestrators();
    } catch (err) {
      console.error("Save error:", err);
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setEditPoolsLoading(false);
    }
  };

  // Initial load
  useEffect(() => {
    fetchOrchestrators();
  }, [fetchOrchestrators]);

  // Update webhook expanded state based on configuration status
  useEffect(() => {
    if (selectedOrchestrator) {
      // Collapse if webhook is configured, expand if not
      setWebhookExpanded(!selectedOrchestrator.webhook_secret_configured);
    }
  }, [selectedOrchestrator?.id, selectedOrchestrator?.webhook_secret_configured]);

  // Handle venue selection in create form
  const handleVenueSelect = (venueId: string | null, venueName: string | null) => {
    setCreateForm((prev) => ({ ...prev, venue_id: venueId, venue_name: venueName }));
  };

  // Handle DPSK pool selection
  const handlePoolSelect = (poolId: string | null, pool: any) => {
    setCreateForm((prev) => ({
      ...prev,
      site_wide_pool_id: poolId || "",
      site_wide_pool_name: pool?.name || ""
    }));
  };

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return "Never";
    return new Date(dateStr).toLocaleString() + " UTC";
  };

  return (
    <div className="p-4 max-w-6xl mx-auto">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-3xl font-bold mb-2">DPSK Orchestrator</h1>
          <p className="text-gray-600">
            Sync passphrases from per-unit DPSK pools to a site-wide pool for seamless roaming
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => {
              fetchOrchestrators();
              if (selectedOrchestrator) {
                // Refresh with live passphrase counts from RuckusONE
                fetchOrchestratorDetail(selectedOrchestrator.id, true);
              }
            }}
            className="px-3 py-2 bg-gray-200 rounded-lg hover:bg-gray-300 transition-colors"
          >
            <RefreshCw size={18} />
          </button>
          <button
            onClick={() => setShowCreateModal(true)}
            className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors flex items-center gap-2"
          >
            <Plus size={18} />
            New Orchestrator
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 p-4 bg-red-100 border border-red-300 rounded-lg text-red-800">
          {error}
          <button onClick={() => setError("")} className="ml-2 underline">
            Dismiss
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Orchestrators List */}
        <div className="lg:col-span-1">
          <div className="bg-white rounded-xl shadow-sm border border-gray-200">
            <div className="p-4 border-b border-gray-200 bg-gradient-to-r from-indigo-50 to-blue-50">
              <h2 className="font-semibold text-gray-900">Orchestrators</h2>
            </div>
            <div className="divide-y divide-gray-200">
              {loading ? (
                <div className="p-4 text-center text-gray-500">Loading...</div>
              ) : orchestrators.length === 0 ? (
                <div className="p-4 text-center text-gray-500">
                  No orchestrators configured. Create one to get started.
                </div>
              ) : (
                orchestrators.map((orch) => {
                  const orchestratorController = controllers.find((c) => c.id === orch.controller_id);
                  return (
                  <div
                    key={orch.id}
                    onClick={() => fetchOrchestratorDetail(orch.id, true)}
                    className={`p-4 cursor-pointer hover:bg-gray-50 transition-colors ${
                      selectedOrchestrator?.id === orch.id ? "bg-indigo-50" : ""
                    }`}
                  >
                    <div className="flex justify-between items-start">
                      <div>
                        <h3 className="font-medium text-gray-900">{orch.name}</h3>
                        <p className="text-sm text-gray-500">
                          {orch.source_pool_count} source pools
                          {orchestratorController && orch.controller_id !== activeControllerId && (
                            <span className="ml-2 text-xs text-indigo-600">
                              ({orchestratorController.name})
                            </span>
                          )}
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        {orch.flagged_count + orch.orphan_count > 0 && (
                          <span className="px-2 py-1 text-xs bg-yellow-100 text-yellow-800 rounded-full">
                            {orch.flagged_count + orch.orphan_count} needs attention
                          </span>
                        )}
                        <span
                          className={`w-3 h-3 rounded-full ${
                            orch.enabled ? "bg-green-500" : "bg-gray-400"
                          }`}
                        />
                      </div>
                    </div>
                    <p className="text-xs text-gray-400 mt-1">Last sync: {formatDate(orch.last_sync_at)}</p>
                  </div>
                );})
              )}
            </div>
          </div>
        </div>

        {/* Detail View */}
        <div className="lg:col-span-2">
          {detailLoading ? (
            <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-8 text-center">
              <RefreshCw className="w-8 h-8 animate-spin mx-auto text-indigo-500" />
              <p className="mt-2 text-gray-500">Loading details...</p>
            </div>
          ) : selectedOrchestrator ? (
            <div className="space-y-4">
              {/* Header Card */}
              <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <h2 className="text-xl font-bold text-gray-900">
                      {selectedOrchestrator.name}
                    </h2>
                    <p className="text-sm text-gray-500">
                      Target: {selectedOrchestrator.site_wide_pool_name || selectedOrchestrator.site_wide_pool_id}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => triggerSync(selectedOrchestrator.id)}
                      className="px-3 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors flex items-center gap-1"
                    >
                      <Play size={16} />
                      Sync Now
                    </button>
                    <button
                      onClick={openEditModal}
                      className="px-3 py-2 bg-indigo-100 text-indigo-800 rounded-lg hover:bg-indigo-200 transition-colors flex items-center gap-1"
                    >
                      <Edit2 size={16} />
                      Edit
                    </button>
                    <button
                      onClick={() => toggleEnabled(selectedOrchestrator.id, selectedOrchestrator.enabled)}
                      className={`px-3 py-2 rounded-lg transition-colors ${
                        selectedOrchestrator.enabled
                          ? "bg-yellow-100 text-yellow-800 hover:bg-yellow-200"
                          : "bg-gray-100 text-gray-800 hover:bg-gray-200"
                      }`}
                    >
                      {selectedOrchestrator.enabled ? "Disable" : "Enable"}
                    </button>
                    <button
                      onClick={() => deleteOrchestrator(selectedOrchestrator.id)}
                      className="px-3 py-2 bg-red-100 text-red-800 rounded-lg hover:bg-red-200 transition-colors"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                </div>

                {/* Stats */}
                <div className="grid grid-cols-4 gap-4 mt-4">
                  <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-3">
                    <p className="text-sm text-gray-600">Source Pools</p>
                    <p className="text-2xl font-bold text-indigo-600">
                      {selectedOrchestrator.source_pool_count}
                    </p>
                  </div>
                  <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
                    <p className="text-sm text-gray-600">Sync Interval</p>
                    <p className="text-2xl font-bold text-blue-600">
                      {selectedOrchestrator.sync_interval_minutes}m
                    </p>
                  </div>
                  <div
                    className={`rounded-lg p-3 cursor-pointer border ${
                      selectedOrchestrator.flagged_count > 0
                        ? "bg-yellow-50 border-yellow-200"
                        : "bg-gray-50 border-gray-200"
                    }`}
                    onClick={() => selectedOrchestrator.flagged_count > 0 && fetchFlagged(selectedOrchestrator.id)}
                  >
                    <p className="text-sm text-gray-600">Flagged</p>
                    <p className="text-2xl font-bold text-yellow-600">{selectedOrchestrator.flagged_count}</p>
                  </div>
                  <div
                    className={`rounded-lg p-3 cursor-pointer border ${
                      selectedOrchestrator.orphan_count > 0
                        ? "bg-orange-50 border-orange-200"
                        : "bg-gray-50 border-gray-200"
                    }`}
                    onClick={() => selectedOrchestrator.orphan_count > 0 && fetchFlagged(selectedOrchestrator.id)}
                  >
                    <p className="text-sm text-gray-600">Orphans</p>
                    <p className="text-2xl font-bold text-orange-600">{selectedOrchestrator.orphan_count}</p>
                  </div>
                </div>

                {/* Sync Timing Info */}
                <div className="mt-3 flex items-center gap-6 text-sm">
                  {/* Last Completed Sync */}
                  <div className="flex items-center gap-2">
                    <CheckCircle size={14} className="text-gray-400" />
                    <span className="text-gray-500">Last sync:</span>
                    <span className="text-gray-700 font-medium">
                      {selectedOrchestrator.last_sync_at ? (
                        (() => {
                          const lastSync = new Date(selectedOrchestrator.last_sync_at + (selectedOrchestrator.last_sync_at.endsWith('Z') ? '' : 'Z'));
                          const now = new Date();
                          const diffMs = now.getTime() - lastSync.getTime();
                          const diffMin = Math.round(diffMs / 60000);

                          const timeStr = lastSync.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

                          if (diffMin < 1) return `just now`;
                          if (diffMin < 60) return `${diffMin} min ago (${timeStr})`;
                          if (diffMin < 1440) return `${Math.round(diffMin / 60)} hr ago (${timeStr})`;
                          return `${Math.round(diffMin / 1440)} days ago (${lastSync.toLocaleDateString()})`;
                        })()
                      ) : (
                        <span className="text-gray-400 italic">Never</span>
                      )}
                    </span>
                  </div>

                  {/* Next Scheduled Sync */}
                  <div className="flex items-center gap-2">
                    <Clock size={14} className="text-gray-400" />
                    <span className="text-gray-500">Next sync:</span>
                    {selectedOrchestrator.enabled ? (
                      <span className="text-gray-700 font-medium">
                        {selectedOrchestrator.last_sync_at ? (
                          (() => {
                            const lastSync = new Date(selectedOrchestrator.last_sync_at + (selectedOrchestrator.last_sync_at.endsWith('Z') ? '' : 'Z'));
                            const intervalMs = selectedOrchestrator.sync_interval_minutes * 60 * 1000;
                            let nextRun = new Date(lastSync.getTime() + intervalMs);
                            const now = new Date();

                            // If the calculated next run is in the past, advance to the next interval
                            while (nextRun.getTime() <= now.getTime()) {
                              nextRun = new Date(nextRun.getTime() + intervalMs);
                            }

                            const diffMs = nextRun.getTime() - now.getTime();
                            const diffMin = Math.round(diffMs / 60000);

                            const timeStr = nextRun.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

                            if (diffMin < 60) return `in ${diffMin} min (${timeStr})`;
                            if (diffMin < 1440) return `in ${Math.round(diffMin / 60)} hr (${timeStr})`;
                            return `in ${Math.round(diffMin / 1440)} days (${nextRun.toLocaleDateString()})`;
                          })()
                        ) : (
                          "Pending first sync"
                        )}
                      </span>
                    ) : (
                      <span className="text-gray-400 italic">Disabled</span>
                    )}
                  </div>
                </div>
              </div>

              {/* Webhook Configuration Section */}
              <div className="bg-white rounded-xl shadow-sm border border-gray-200">
                <div
                  className="p-4 border-b border-gray-200 bg-gradient-to-r from-amber-50 to-yellow-50 cursor-pointer"
                  onClick={() => setWebhookExpanded(!webhookExpanded)}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Shield size={18} className="text-amber-600" />
                      <h3 className="font-semibold text-gray-900">Webhook Configuration</h3>
                      {selectedOrchestrator.webhook_secret_configured && (
                        <span className="px-2 py-0.5 text-xs bg-green-100 text-green-800 rounded-full">
                          Configured
                        </span>
                      )}
                    </div>
                    {webhookExpanded ? (
                      <ChevronUp size={18} className="text-gray-500" />
                    ) : (
                      <ChevronDown size={18} className="text-gray-500" />
                    )}
                  </div>
                </div>
                {webhookExpanded && (
                <div className="p-4 space-y-4">
                  <p className="text-sm text-gray-500">
                    Configure a webhook in RuckusONE to receive real-time updates when DPSK passphrases change.
                    This enables immediate sync instead of waiting for the scheduled interval.
                  </p>

                  {/* Webhook URL */}
                  <div className="bg-gray-50 border border-gray-200 rounded-lg p-3">
                    <p className="text-sm font-medium text-gray-700 mb-2">Webhook URL</p>
                    <div className="flex items-center gap-2">
                      <code className="flex-1 text-sm bg-white border border-gray-300 rounded px-3 py-2 font-mono text-gray-800 overflow-x-auto">
                        {window.location.origin}{selectedOrchestrator.webhook_path}
                      </code>
                      <button
                        onClick={() => copyToClipboard(`${window.location.origin}${selectedOrchestrator.webhook_path}`)}
                        className="px-3 py-2 bg-gray-200 hover:bg-gray-300 rounded transition-colors"
                        title="Copy to clipboard"
                      >
                        <Copy size={16} />
                      </button>
                    </div>
                    <p className="text-xs text-gray-400 mt-2">
                      Configure this URL in RuckusONE under Administration → Webhooks
                    </p>
                  </div>

                  {/* Webhook Secret (Required) */}
                  <div className={`border rounded-lg p-3 ${
                    selectedOrchestrator.webhook_secret_configured
                      ? "bg-green-50 border-green-200"
                      : "bg-red-50 border-red-200"
                  }`}>
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <Key size={16} className={selectedOrchestrator.webhook_secret_configured ? "text-green-600" : "text-red-600"} />
                        <p className="text-sm font-medium text-gray-700">Webhook Secret (Required)</p>
                      </div>
                      <span
                        className={`px-2 py-0.5 text-xs font-medium rounded-full ${
                          selectedOrchestrator.webhook_secret_configured
                            ? "bg-green-100 text-green-800"
                            : "bg-red-100 text-red-800"
                        }`}
                      >
                        {selectedOrchestrator.webhook_secret_configured ? "Configured" : "Not Configured"}
                      </span>
                    </div>
                    <p className="text-xs text-gray-600 mb-2">
                      {selectedOrchestrator.webhook_secret_configured
                        ? "Webhook is ready. The secret identifies this orchestrator when RuckusONE sends events."
                        : "A webhook secret is required to receive webhooks. Generate one below and configure it in RuckusONE."}
                    </p>
                    <div className="bg-white/50 border border-gray-200 rounded p-2 mb-3">
                      <p className="text-xs font-medium text-gray-700">Header to configure in RuckusONE:</p>
                      <code className="text-xs font-mono text-indigo-700">X-Webhook-Secret: &lt;your-secret&gt;</code>
                    </div>

                    {/* Generated Secret Display */}
                    {generatedSecret && (
                      <div className="mb-3 p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
                        <p className="text-sm font-medium text-yellow-800 mb-1">
                          New Secret Generated - Copy Now!
                        </p>
                        <div className="flex items-center gap-2">
                          <code className="flex-1 text-sm bg-white border border-yellow-300 rounded px-2 py-1 font-mono">
                            {generatedSecret}
                          </code>
                          <button
                            onClick={() => copyToClipboard(generatedSecret)}
                            className="px-2 py-1 bg-yellow-200 hover:bg-yellow-300 rounded transition-colors"
                          >
                            <Copy size={14} />
                          </button>
                        </div>
                        <p className="text-xs text-yellow-700 mt-1">
                          Configure this as the <strong>X-Webhook-Secret</strong> header value in RuckusONE. This secret will not be shown again.
                        </p>
                      </div>
                    )}

                    <div className="flex gap-2">
                      <button
                        onClick={generateWebhookSecret}
                        disabled={webhookLoading}
                        className="px-3 py-1.5 text-sm bg-indigo-100 text-indigo-800 rounded hover:bg-indigo-200 transition-colors disabled:opacity-50 flex items-center gap-1"
                      >
                        <Key size={14} />
                        {selectedOrchestrator.webhook_secret_configured ? "Regenerate Secret" : "Generate Secret"}
                      </button>
                      {selectedOrchestrator.webhook_secret_configured && (
                        <button
                          onClick={clearWebhookSecret}
                          disabled={webhookLoading}
                          className="px-3 py-1.5 text-sm bg-red-100 text-red-800 rounded hover:bg-red-200 transition-colors disabled:opacity-50"
                        >
                          Clear Secret
                        </button>
                      )}
                    </div>
                  </div>
                </div>
                )}
              </div>

              {/* Destination Pool */}
              <div className="bg-white rounded-xl shadow-sm border border-gray-200">
                <div className="p-4 border-b border-gray-200 bg-gradient-to-r from-blue-50 to-cyan-50">
                  <h3 className="font-semibold text-gray-900">Destination Pool</h3>
                  <p className="text-sm text-gray-500 mt-1">All passphrases from source pools are synced here</p>
                </div>
                <div className="p-4">
                  <div className="flex items-center gap-3 mb-4">
                    <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
                      <svg className="w-5 h-5 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
                      </svg>
                    </div>
                    <div>
                      <p className="font-medium text-gray-900">
                        {selectedOrchestrator.site_wide_pool_name || "Site-Wide Pool"}
                      </p>
                      <p className="text-sm text-gray-500 font-mono">
                        {selectedOrchestrator.site_wide_pool_id}
                      </p>
                    </div>
                  </div>

                  {/* Pool Details */}
                  {destinationPoolLoading ? (
                    <div className="flex items-center justify-center py-4">
                      <RefreshCw className="w-5 h-5 animate-spin text-blue-500" />
                      <span className="ml-2 text-sm text-gray-500">Loading pool details...</span>
                    </div>
                  ) : destinationPool ? (
                    <div className="bg-gray-50 rounded-lg border border-gray-200 p-3">
                      <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
                        <div className="flex justify-between">
                          <span className="text-gray-500">Total Passphrases</span>
                          <span className="font-medium text-gray-900">
                            {destinationPool.totalPassphrases ?? "—"}
                          </span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-gray-500">Passphrase Format</span>
                          <span className="font-medium text-gray-900">
                            {destinationPool.passphraseType
                              ?.replace(/_/g, " ")
                              .replace(/\b\w/g, (c) => c.toUpperCase()) ?? "—"}
                          </span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-gray-500">Passphrase Length</span>
                          <span className="font-medium text-gray-900">
                            {destinationPool.passphraseLength != null
                              ? `${destinationPool.passphraseLength} Characters`
                              : "—"}
                          </span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-gray-500">Passphrase Expiration</span>
                          <span className="font-medium text-gray-900">
                            {!destinationPool.passphraseExpiration ||
                             destinationPool.passphraseExpiration === "UNLIMITED" ||
                             destinationPool.passphraseExpiration === "NEVER"
                              ? "Never"
                              : destinationPool.passphraseExpiration}
                          </span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-gray-500">Devices per Passphrase</span>
                          <span className="font-medium text-gray-900">
                            {destinationPool.maxDevicesPerPassphrase === 0 ||
                             destinationPool.maxDevicesPerPassphrase === null ||
                             destinationPool.maxDevicesPerPassphrase === undefined
                              ? "Unlimited"
                              : destinationPool.maxDevicesPerPassphrase}
                          </span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-gray-500">Default Access</span>
                          <span className={`font-medium ${
                            destinationPool.defaultAccess === "REJECT"
                              ? "text-red-600"
                              : destinationPool.defaultAccess === "ACCEPT"
                                ? "text-green-600"
                                : "text-gray-900"
                          }`}>
                            {destinationPool.defaultAccess ?? "—"}
                          </span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-gray-500">Adaptive Policy Set</span>
                          <span className="font-medium text-gray-900">
                            {destinationPool.adaptivePolicySetName || "None"}
                          </span>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <p className="text-sm text-gray-400 italic">Unable to load pool details</p>
                  )}

                  {/* Identity Audit Button */}
                  <div className="mt-4 pt-4 border-t border-gray-200">
                    <div className="flex gap-2 flex-wrap">
                      <button
                        onClick={() => runIdentityAudit(selectedOrchestrator.id)}
                        disabled={identityAuditLoading || refreshIdentityGroupsLoading}
                        className="px-4 py-2 bg-purple-100 text-purple-800 rounded-lg hover:bg-purple-200 transition-colors disabled:opacity-50 flex items-center gap-2 text-sm font-medium"
                      >
                        {identityAuditLoading ? (
                          <RefreshCw className="w-4 h-4 animate-spin" />
                        ) : (
                          <Users size={16} />
                        )}
                        Audit Identities
                      </button>
                      <button
                        onClick={() => refreshIdentityGroups(selectedOrchestrator.id)}
                        disabled={refreshIdentityGroupsLoading || identityAuditLoading}
                        className="px-4 py-2 bg-blue-100 text-blue-800 rounded-lg hover:bg-blue-200 transition-colors disabled:opacity-50 flex items-center gap-2 text-sm font-medium"
                        title="Fetch identity group IDs from R1 for all pools"
                      >
                        {refreshIdentityGroupsLoading ? (
                          <RefreshCw className="w-4 h-4 animate-spin" />
                        ) : (
                          <Database size={16} />
                        )}
                        Refresh Pool Links
                      </button>
                    </div>
                    <p className="text-xs text-gray-500 mt-1">
                      Audit finds orphan identities. Refresh links identity groups from R1 if pools show "no identity group".
                    </p>
                  </div>
                </div>
              </div>

              {/* Source Pools */}
              <div className="bg-white rounded-xl shadow-sm border border-gray-200">
                <div className="p-4 border-b border-gray-200 bg-gradient-to-r from-green-50 to-emerald-50 flex justify-between items-center">
                  <div>
                    <h3 className="font-semibold text-gray-900">Source Pools</h3>
                    <p className="text-sm text-gray-500 mt-1">Per-unit pools that feed into the destination</p>
                  </div>
                  <button
                    onClick={openEditPoolsModal}
                    className="px-3 py-1.5 text-sm bg-green-100 text-green-800 rounded-lg hover:bg-green-200 transition-colors flex items-center gap-1"
                  >
                    <Edit2 size={14} />
                    Edit Pools
                  </button>
                </div>
                <div className="overflow-x-auto max-h-80 overflow-y-auto">
                  {selectedOrchestrator.source_pools.length === 0 ? (
                    <div className="p-4 text-center text-gray-500">
                      No source pools configured. Click "Edit Pools" to add them.
                    </div>
                  ) : (
                    <table className="w-full text-sm">
                      <thead className="bg-gray-50 sticky top-0">
                        <tr className="text-left text-gray-600 text-xs uppercase tracking-wider">
                          <th className="px-3 py-2 font-medium">Pool Name</th>
                          <th className="px-3 py-2 font-medium text-center">Count</th>
                          <th className="px-3 py-2 font-medium">Format</th>
                          <th className="px-3 py-2 font-medium text-center">Length</th>
                          <th className="px-3 py-2 font-medium text-center">Max Devices</th>
                          <th className="px-3 py-2 font-medium">Expiration</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-200">
                        {selectedOrchestrator.source_pools.map((pool) => (
                          <tr key={pool.id} className="hover:bg-gray-50">
                            <td className="px-3 py-2 font-medium text-gray-900">
                              {pool.pool_name || pool.pool_id}
                            </td>
                            <td className="px-3 py-2 text-center text-gray-700">
                              {pool.passphrase_count}
                            </td>
                            <td className="px-3 py-2 text-gray-600">
                              {pool.passphrase_format
                                ? pool.passphrase_format.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, c => c.toUpperCase())
                                : '—'}
                            </td>
                            <td className="px-3 py-2 text-center text-gray-600">
                              {pool.passphrase_length ?? '—'}
                            </td>
                            <td className="px-3 py-2 text-center text-gray-600">
                              {pool.device_count_limit === null ? '∞' : pool.device_count_limit === undefined ? '—' : pool.device_count_limit === 0 ? '∞' : pool.device_count_limit}
                            </td>
                            <td className="px-3 py-2 text-gray-600">
                              {!pool.expiration_type || pool.expiration_type === 'NEVER' || pool.expiration_type === 'UNLIMITED' ? 'Never' : pool.expiration_type.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, c => c.toUpperCase())}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              </div>

              {/* Recent Sync Events */}
              <div className="bg-white rounded-xl shadow-sm border border-gray-200">
                <div className="p-4 border-b border-gray-200 bg-gradient-to-r from-purple-50 to-pink-50">
                  <h3 className="font-semibold text-gray-900">Recent Sync Events</h3>
                </div>
                <div className="divide-y divide-gray-200">
                  {selectedOrchestrator.recent_sync_events.length === 0 ? (
                    <div className="p-4 text-center text-gray-500">No sync events yet.</div>
                  ) : (
                    selectedOrchestrator.recent_sync_events.map((event) => (
                      <div key={event.id} className="p-4">
                        <div className="flex justify-between items-center">
                          <div className="flex items-center gap-3">
                            {event.status === "success" ? (
                              <CheckCircle className="w-5 h-5 text-green-500" />
                            ) : event.status === "running" ? (
                              <Clock className="w-5 h-5 text-indigo-500 animate-spin" />
                            ) : (
                              <div className="relative group">
                                <AlertTriangle className="w-5 h-5 text-yellow-500 cursor-help" />
                                {event.errors && event.errors.length > 0 && (
                                  <div className="absolute left-0 top-6 z-10 hidden group-hover:block w-64 p-2 bg-gray-900 text-white text-xs rounded shadow-lg">
                                    <p>{event.errors.length} warning{event.errors.length !== 1 ? 's' : ''} during sync. Click to expand details below.</p>
                                  </div>
                                )}
                              </div>
                            )}
                            <div>
                              <p className="font-medium text-gray-900 capitalize">
                                {event.event_type} sync
                              </p>
                              <p className="text-sm text-gray-500">{formatDate(event.started_at)}</p>
                            </div>
                          </div>
                          <div className="text-right text-sm">
                            <p className="text-green-600">+{event.added_count} added</p>
                            <p className="text-blue-600">~{event.updated_count} updated</p>
                            {event.flagged_for_removal > 0 && (
                              <p className="text-yellow-600">-{event.flagged_for_removal} flagged</p>
                            )}
                          </div>
                        </div>
                        {/* Expandable warnings drawer */}
                        {event.errors && event.errors.length > 0 && (
                          <div className="mt-2 border border-yellow-200 rounded overflow-hidden">
                            <button
                              onClick={() => {
                                setExpandedWarnings(prev => {
                                  const next = new Set(prev);
                                  if (next.has(event.id)) {
                                    next.delete(event.id);
                                  } else {
                                    next.add(event.id);
                                  }
                                  return next;
                                });
                              }}
                              className="w-full flex items-center justify-between p-2 bg-yellow-50 hover:bg-yellow-100 transition-colors text-xs text-yellow-800"
                            >
                              <span className="font-medium">
                                {event.errors.length} warning{event.errors.length !== 1 ? 's' : ''}
                              </span>
                              <span className="flex items-center gap-1 text-yellow-600">
                                {expandedWarnings.has(event.id) ? (
                                  <>
                                    Hide details
                                    <ChevronUp size={14} />
                                  </>
                                ) : (
                                  <>
                                    Show details
                                    <ChevronDown size={14} />
                                  </>
                                )}
                              </span>
                            </button>
                            {expandedWarnings.has(event.id) && (
                              <div className="p-2 bg-yellow-50/50 border-t border-yellow-200 text-xs text-yellow-800 max-h-48 overflow-y-auto">
                                <ul className="space-y-1">
                                  {event.errors.map((err, i) => (
                                    <li key={i} className="break-words pl-3 relative before:content-['•'] before:absolute before:left-0 before:text-yellow-600">
                                      {err}
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          ) : (
            <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-8 text-center">
              <Settings className="w-12 h-12 mx-auto text-gray-400" />
              <p className="mt-4 text-gray-500">Select an orchestrator to view details</p>
            </div>
          )}
        </div>
      </div>

      {/* Create Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-5xl w-full max-h-[90vh] overflow-hidden flex flex-col">
            {/* Modal Header */}
            <div className="bg-gradient-to-r from-indigo-600 to-blue-600 text-white px-6 py-4 flex justify-between items-center">
              <div>
                <h2 className="text-2xl font-bold">Create Orchestrator</h2>
                {activeController ? (
                  <p className="text-indigo-100 text-sm">
                    Creating on controller: {activeController.name}
                  </p>
                ) : (
                  <p className="text-yellow-200 text-sm">
                    No active controller selected
                  </p>
                )}
              </div>
              <button
                onClick={() => {
                  setShowCreateModal(false);
                  resetCreateForm();
                }}
                className="text-white hover:text-gray-200 text-2xl font-bold"
              >
                ×
              </button>
            </div>
            <div className="p-6 space-y-4 overflow-y-auto flex-1">
              {/* Controller Warning */}
              {!activeController && (
                <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
                  <p className="text-sm text-yellow-800">
                    Please select a controller from the navbar first.
                  </p>
                </div>
              )}

              {/* Name */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Name
                </label>
                <input
                  type="text"
                  value={createForm.name}
                  onChange={(e) => setCreateForm((prev) => ({ ...prev, name: e.target.value }))}
                  placeholder="e.g., Parkview Apartments"
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                />
              </div>

              {/* Venue Selection */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Venue
                </label>
                <SingleVenueSelector
                  controllerId={activeControllerId}
                  tenantId={tenantId}
                  selectedVenueId={createForm.venue_id}
                  onVenueSelect={(id, venue) => handleVenueSelect(id, venue?.name || null)}
                />
              </div>

              {/* Site-wide Pool Selection */}
              {createForm.venue_id && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Site-Wide DPSK Pool (Target)
                  </label>
                  <DpskPoolSelector
                    controllerId={activeControllerId}
                    tenantId={tenantId}
                    selectedPoolId={createForm.site_wide_pool_id || null}
                    onPoolSelect={handlePoolSelect}
                  />
                </div>
              )}

              {/* Sync Interval */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Sync Interval (minutes)
                </label>
                <input
                  type="number"
                  min={5}
                  max={1440}
                  value={createForm.sync_interval_minutes}
                  onChange={(e) =>
                    setCreateForm((prev) => ({ ...prev, sync_interval_minutes: parseInt(e.target.value) }))
                  }
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                />
              </div>

              {/* Source Pool Selection */}
              {createForm.site_wide_pool_id && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Source DPSK Pools (Per-Unit)
                  </label>
                  <p className="text-sm text-gray-500 mb-3">
                    Select the per-unit DPSK pools that will be synced to the site-wide pool.
                    Use glob patterns to filter (e.g., "Unit*" to include, "SiteWide*" to exclude).
                  </p>
                  <DpskPoolSelector
                    controllerId={activeControllerId}
                    tenantId={tenantId}
                    multiSelect={true}
                    selectedPoolIds={createForm.source_pools.map(p => p.id)}
                    excludePoolId={createForm.site_wide_pool_id}
                    initialFilter={createForm.source_pool_filter}
                    onPoolsSelect={(poolIds, pools) => {
                      setCreateForm((prev) => ({
                        ...prev,
                        source_pools: pools.map(p => ({ id: p.id, name: p.name }))
                      }));
                    }}
                  />
                </div>
              )}
            </div>
            {/* Modal Footer */}
            <div className="bg-gray-50 px-6 py-4 flex justify-end gap-3 border-t">
              <button
                onClick={() => {
                  setShowCreateModal(false);
                  resetCreateForm();
                }}
                className="px-6 py-2 bg-gray-600 text-white rounded hover:bg-gray-700 font-semibold"
              >
                Cancel
              </button>
              <button
                onClick={createOrchestrator}
                disabled={!activeControllerId || !createForm.name || !createForm.site_wide_pool_id || createForm.source_pools.length === 0 || createLoading}
                className="px-6 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 font-semibold disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {createLoading ? "Creating..." : "Create Orchestrator"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Flagged/Orphan Modal */}
      {showFlaggedModal && selectedOrchestrator && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] overflow-hidden flex flex-col">
            {/* Modal Header */}
            <div className="bg-gradient-to-r from-yellow-600 to-orange-600 text-white px-6 py-4 flex justify-between items-center">
              <div>
                <h2 className="text-2xl font-bold">Flagged & Orphan Passphrases</h2>
                <p className="text-yellow-100 text-sm">
                  {selectedOrchestrator.name}
                </p>
              </div>
              <button onClick={() => setShowFlaggedModal(false)} className="text-white hover:text-gray-200 text-2xl font-bold">
                ×
              </button>
            </div>
            <div className="p-6 overflow-y-auto flex-1">
              {flaggedLoading ? (
                <div className="text-center py-8">
                  <RefreshCw className="w-8 h-8 animate-spin mx-auto text-blue-500" />
                </div>
              ) : flaggedItems.length === 0 ? (
                <div className="text-center py-8 text-gray-500">No flagged or orphan items.</div>
              ) : (
                <div className="space-y-4">
                  {/* Legend Table */}
                  <div className="bg-gray-50 border border-gray-200 rounded-lg p-4 mb-4">
                    <h4 className="text-sm font-semibold text-gray-700 mb-2">Resolution Actions</h4>
                    <table className="w-full text-sm">
                      <tbody>
                        <tr>
                          <td className="py-1 pr-4 font-medium text-red-700">Delete</td>
                          <td className="py-1 text-gray-600">Remove passphrase from site-wide pool (user loses access)</td>
                        </tr>
                        <tr>
                          <td className="py-1 pr-4 font-medium text-green-700">Keep</td>
                          <td className="py-1 text-gray-600">Keep in site-wide pool, stop tracking (becomes unmanaged)</td>
                        </tr>
                        <tr>
                          <td className="py-1 pr-4 font-medium text-gray-700">Ignore</td>
                          <td className="py-1 text-gray-600">Keep in site-wide pool, mark as ignored (won't flag again)</td>
                        </tr>
                        <tr>
                          <td className="py-1 pr-4 font-medium text-indigo-700">Resync</td>
                          <td className="py-1 text-gray-600">Re-create passphrase in site-wide from source (for target_missing only)</td>
                        </tr>
                        <tr>
                          <td className="py-1 pr-4 font-medium text-purple-700">Create Source</td>
                          <td className="py-1 text-gray-600">Copy passphrase to a per-unit pool, establishing proper sync relationship (for orphans only)</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  {flaggedItems.map((item) => (
                    <div
                      key={item.id}
                      className="border border-gray-200 rounded-lg p-4"
                    >
                      <div className="flex justify-between items-start">
                        <div>
                          <div className="flex items-center gap-2">
                            {item.sync_status === "flagged_removal" ? (
                              <span className="px-2 py-0.5 text-xs bg-yellow-100 text-yellow-800 rounded">
                                Flagged for Removal
                              </span>
                            ) : item.sync_status === "target_missing" ? (
                              <span className="px-2 py-0.5 text-xs bg-red-100 text-red-800 rounded">
                                Target Missing
                              </span>
                            ) : (
                              <span className="px-2 py-0.5 text-xs bg-orange-100 text-orange-800 rounded">
                                Orphan
                              </span>
                            )}
                          </div>
                          <p className="font-medium text-gray-900 mt-1">
                            {item.source_username || "Unknown User"}
                          </p>
                          <p className="text-sm text-gray-500">
                            {item.source_pool_name || item.source_pool_id ? (
                              <>Pool: {item.source_pool_name || item.source_pool_id}</>
                            ) : null}
                            {item.vlan_id && `${item.source_pool_name || item.source_pool_id ? ' | ' : ''}VLAN: ${item.vlan_id}`}
                          </p>
                          <p className="text-xs text-gray-500 mt-1 italic">
                            {item.sync_status === "flagged_removal"
                              ? `Previously synced from "${item.source_pool_name || 'source pool'}", but no longer found in source`
                              : item.sync_status === "target_missing"
                              ? `Was synced to site-wide pool, but target passphrase was deleted externally`
                              : "Exists in site-wide pool but not found in any source pool"
                            }
                          </p>
                          {item.flagged_at && (
                            <p className="text-xs text-gray-400 mt-1">
                              Flagged: {formatDate(item.flagged_at)}
                            </p>
                          )}
                        </div>
                        <div className="flex flex-wrap gap-2 items-start">
                          {/* Resync button for target_missing */}
                          {item.sync_status === "target_missing" && (
                            item.source_passphrase_id ? (
                              <button
                                onClick={() => resolveFlagged(selectedOrchestrator.id, item.id, "resync")}
                                className="px-3 py-1.5 text-sm bg-indigo-100 text-indigo-800 rounded hover:bg-indigo-200 transition-colors"
                              >
                                Resync
                              </button>
                            ) : (
                              <span className="px-3 py-1.5 text-sm text-gray-400 italic" title="Source passphrase ID not available - cannot resync">
                                (no source)
                              </span>
                            )
                          )}

                          {/* Create Source button/dropdown for orphans */}
                          {item.sync_status === "orphan" && (
                            <div className="relative">
                              <button
                                onClick={() => setCopyToSourceItem(copyToSourceItem === item.id ? null : item.id)}
                                className="px-3 py-1.5 text-sm bg-purple-100 text-purple-800 rounded hover:bg-purple-200 transition-colors"
                                disabled={copyToSourceLoading}
                              >
                                {copyToSourceLoading && copyToSourceItem === item.id ? "..." : "Create Source ▼"}
                              </button>
                              {copyToSourceItem === item.id && selectedOrchestrator.source_pools.length > 0 && (
                                <div className="absolute right-0 mt-1 w-64 bg-white border border-gray-200 rounded-lg shadow-lg z-10">
                                  <div className="p-2 border-b border-gray-100">
                                    <p className="text-xs text-gray-500">Select source pool to copy to:</p>
                                    {item.vlan_id && (
                                      <p className="text-xs text-purple-600 mt-1">
                                        Tip: Pick pool for VLAN {item.vlan_id}
                                      </p>
                                    )}
                                  </div>
                                  <div className="max-h-48 overflow-y-auto">
                                    {selectedOrchestrator.source_pools.map((pool) => (
                                      <button
                                        key={pool.pool_id}
                                        onClick={() => copyToSource(selectedOrchestrator.id, item.id, pool.pool_id)}
                                        className="w-full px-3 py-2 text-left text-sm hover:bg-purple-50 transition-colors flex justify-between items-center"
                                        disabled={copyToSourceLoading}
                                      >
                                        <span className="truncate">{pool.pool_name || pool.pool_id}</span>
                                        <span className="text-xs text-gray-400 ml-2">{pool.passphrase_count} psk</span>
                                      </button>
                                    ))}
                                  </div>
                                </div>
                              )}
                            </div>
                          )}

                          <button
                            onClick={() => resolveFlagged(selectedOrchestrator.id, item.id, "delete")}
                            className="px-3 py-1.5 text-sm bg-red-100 text-red-800 rounded hover:bg-red-200 transition-colors"
                          >
                            Delete
                          </button>
                          <button
                            onClick={() => resolveFlagged(selectedOrchestrator.id, item.id, "keep")}
                            className="px-3 py-1.5 text-sm bg-green-100 text-green-800 rounded hover:bg-green-200 transition-colors"
                          >
                            Keep
                          </button>
                          <button
                            onClick={() => resolveFlagged(selectedOrchestrator.id, item.id, "ignore")}
                            className="px-3 py-1.5 text-sm bg-gray-100 text-gray-800 rounded hover:bg-gray-200 transition-colors"
                          >
                            Ignore
                          </button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
            {/* Modal Footer */}
            <div className="bg-gray-50 px-6 py-4 flex justify-end border-t">
              <button
                onClick={() => setShowFlaggedModal(false)}
                className="px-6 py-2 bg-gray-600 text-white rounded hover:bg-gray-700 font-semibold"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit Modal */}
      {showEditModal && selectedOrchestrator && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full max-h-[90vh] overflow-hidden flex flex-col">
            {/* Modal Header */}
            <div className="bg-gradient-to-r from-indigo-600 to-blue-600 text-white px-6 py-4 flex justify-between items-center">
              <div>
                <h2 className="text-2xl font-bold">Edit Orchestrator</h2>
                <p className="text-indigo-100 text-sm">{selectedOrchestrator.name}</p>
              </div>
              <button
                onClick={() => setShowEditModal(false)}
                className="text-white hover:text-gray-200 text-2xl font-bold"
              >
                ×
              </button>
            </div>
            <div className="p-6 space-y-4 overflow-y-auto flex-1">
              {/* Name */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
                <input
                  type="text"
                  value={editForm.name}
                  onChange={(e) => setEditForm((prev) => ({ ...prev, name: e.target.value }))}
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                />
              </div>

              {/* Sync Interval */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Sync Interval (minutes)
                </label>
                <input
                  type="number"
                  min={5}
                  max={1440}
                  value={editForm.sync_interval_minutes}
                  onChange={(e) =>
                    setEditForm((prev) => ({ ...prev, sync_interval_minutes: parseInt(e.target.value) || 30 }))
                  }
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                />
                <p className="text-xs text-gray-500 mt-1">How often to run scheduled sync (5-1440 minutes)</p>
              </div>
            </div>
            {/* Modal Footer */}
            <div className="bg-gray-50 px-6 py-4 flex justify-end gap-3 border-t">
              <button
                onClick={() => setShowEditModal(false)}
                className="px-6 py-2 bg-gray-600 text-white rounded hover:bg-gray-700 font-semibold"
              >
                Cancel
              </button>
              <button
                onClick={updateOrchestrator}
                disabled={!editForm.name || editLoading}
                className="px-6 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 font-semibold disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {editLoading ? "Saving..." : "Save Changes"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit Source Pools Modal */}
      {showEditPoolsModal && selectedOrchestrator && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] overflow-hidden flex flex-col">
            {/* Modal Header */}
            <div className="bg-gradient-to-r from-green-600 to-emerald-600 text-white px-6 py-4 flex justify-between items-center">
              <div>
                <h2 className="text-2xl font-bold">Edit Source Pools</h2>
                <p className="text-green-100 text-sm">{selectedOrchestrator.name}</p>
              </div>
              <button
                onClick={() => setShowEditPoolsModal(false)}
                className="text-white hover:text-gray-200 text-2xl font-bold"
              >
                ×
              </button>
            </div>
            <div className="p-6 overflow-y-auto flex-1">
              <p className="text-sm text-gray-500 mb-4">
                Select the per-unit DPSK pools that should be synced to the site-wide pool.
                Use the filters to find pools by name pattern.
              </p>
              <DpskPoolSelector
                controllerId={selectedOrchestrator.controller_id}
                tenantId={selectedOrchestrator.tenant_id}
                multiSelect={true}
                selectedPoolIds={editPools.map(p => p.id)}
                excludePoolId={selectedOrchestrator.site_wide_pool_id}
                initialFilter="Unit*"
                onPoolsSelect={(_poolIds, pools) => setEditPools(pools.map(p => ({ id: p.id, name: p.name })))}
              />
            </div>
            {/* Modal Footer */}
            <div className="bg-gray-50 px-6 py-4 flex justify-between items-center border-t">
              <p className="text-sm text-gray-500">
                {editPools.length} pool{editPools.length !== 1 ? 's' : ''} selected
              </p>
              <div className="flex gap-3">
                <button
                  onClick={() => setShowEditPoolsModal(false)}
                  className="px-6 py-2 bg-gray-600 text-white rounded hover:bg-gray-700 font-semibold"
                >
                  Cancel
                </button>
                <button
                  onClick={saveSourcePools}
                  disabled={editPoolsLoading}
                  className="px-6 py-2 bg-green-600 text-white rounded hover:bg-green-700 font-semibold disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {editPoolsLoading ? "Saving..." : "Save Changes"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Identity Audit Modal */}
      {showIdentityAuditModal && selectedOrchestrator && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] overflow-hidden flex flex-col">
            {/* Modal Header */}
            <div className="bg-gradient-to-r from-purple-600 to-violet-600 text-white px-6 py-4 flex justify-between items-center">
              <div>
                <h2 className="text-2xl font-bold flex items-center gap-2">
                  <Users className="w-6 h-6" />
                  Identity Audit - All Pools
                </h2>
                <p className="text-purple-100 text-sm">{selectedOrchestrator.name}</p>
              </div>
              <button
                onClick={() => setShowIdentityAuditModal(false)}
                className="text-white hover:text-gray-200 text-2xl font-bold"
              >
                ×
              </button>
            </div>

            <div className="p-6 overflow-y-auto flex-1">
              {identityAuditLoading ? (
                <div className="flex items-center justify-center py-12">
                  <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-purple-600"></div>
                  <span className="ml-3 text-gray-600">Running identity audit across all pools...</span>
                </div>
              ) : identityAuditResult ? (
                <div className="space-y-6">
                  {/* Summary Stats */}
                  <div className="grid grid-cols-4 gap-4">
                    <div className="bg-gray-50 rounded-lg p-4 text-center">
                      <p className="text-2xl font-bold text-gray-900">{identityAuditResult.total_pools_audited}</p>
                      <p className="text-sm text-gray-500">Pools Audited</p>
                    </div>
                    <div className="bg-gray-50 rounded-lg p-4 text-center">
                      <p className="text-2xl font-bold text-gray-900">{identityAuditResult.total_identities}</p>
                      <p className="text-sm text-gray-500">Total Identities</p>
                    </div>
                    <div className="bg-blue-50 rounded-lg p-4 text-center">
                      <p className="text-2xl font-bold text-blue-600">{identityAuditResult.total_passphrases}</p>
                      <p className="text-sm text-gray-500">Total Passphrases</p>
                    </div>
                    <div className={`rounded-lg p-4 text-center ${identityAuditResult.total_orphan_identities > 0 ? 'bg-orange-50' : 'bg-green-50'}`}>
                      <p className={`text-2xl font-bold ${identityAuditResult.total_orphan_identities > 0 ? 'text-orange-600' : 'text-green-600'}`}>
                        {identityAuditResult.total_orphan_identities}
                      </p>
                      <p className="text-sm text-gray-500">Total Orphans</p>
                    </div>
                  </div>

                  {/* Site-Wide Pool Audit */}
                  {identityAuditResult.site_wide_audit && (
                    <div className="border rounded-lg overflow-hidden">
                      <div className={`px-4 py-3 flex justify-between items-center ${
                        identityAuditResult.site_wide_audit.orphan_identities > 0 ? 'bg-orange-50' : 'bg-green-50'
                      }`}>
                        <div>
                          <h3 className="font-semibold text-gray-900 flex items-center gap-2">
                            <Database className="w-4 h-4" />
                            Site-Wide Pool: {identityAuditResult.site_wide_audit.pool_name || 'Unnamed'}
                          </h3>
                          <p className="text-xs text-gray-500">
                            {identityAuditResult.site_wide_audit.total_identities} identities, {identityAuditResult.site_wide_audit.total_passphrases} passphrases
                            {identityAuditResult.site_wide_audit.orphan_identities > 0 && (
                              <span className="text-orange-600 font-medium ml-2">
                                ({identityAuditResult.site_wide_audit.orphan_identities} orphans)
                              </span>
                            )}
                          </p>
                        </div>
                        {identityAuditResult.site_wide_audit.orphan_identities > 0 && (
                          <button
                            onClick={() => bulkDeleteOrphanIdentities(
                              selectedOrchestrator.id,
                              identityAuditResult.site_wide_audit!.orphans.map(o => o.id)
                            )}
                            className="px-3 py-1 bg-red-600 text-white rounded text-xs font-medium hover:bg-red-700"
                          >
                            Delete All {identityAuditResult.site_wide_audit.orphan_identities}
                          </button>
                        )}
                      </div>
                      {identityAuditResult.site_wide_audit.orphan_identities > 0 && (
                        <div className="max-h-48 overflow-y-auto">
                          <table className="w-full text-sm">
                            <thead className="bg-gray-50 sticky top-0">
                              <tr>
                                <th className="px-4 py-2 text-left font-medium text-gray-600">Name</th>
                                <th className="px-4 py-2 text-left font-medium text-gray-600">VLAN</th>
                                <th className="px-4 py-2 text-left font-medium text-gray-600">Also In</th>
                                <th className="px-4 py-2 text-right font-medium text-gray-600">Action</th>
                              </tr>
                            </thead>
                            <tbody className="divide-y">
                              {identityAuditResult.site_wide_audit.orphans.map((identity) => (
                                <tr key={identity.id} className="hover:bg-gray-50">
                                  <td className="px-4 py-2">
                                    <span className="font-medium text-gray-900">{identity.name || '—'}</span>
                                    <span className="block text-xs text-gray-400 font-mono">{identity.id.substring(0, 8)}...</span>
                                  </td>
                                  <td className="px-4 py-2">
                                    {identity.vlan ? (
                                      <span className="px-2 py-0.5 bg-blue-100 text-blue-800 rounded text-xs">
                                        {identity.vlan}
                                      </span>
                                    ) : '—'}
                                  </td>
                                  <td className="px-4 py-2 text-xs text-gray-500">
                                    {identity.also_exists_in && identity.also_exists_in.length > 0
                                      ? identity.also_exists_in.join(', ')
                                      : '—'}
                                  </td>
                                  <td className="px-4 py-2 text-right">
                                    <button
                                      onClick={() => deleteOrphanIdentity(selectedOrchestrator.id, identity.id)}
                                      disabled={deleteIdentityLoading === identity.id}
                                      className="px-2 py-1 bg-red-100 text-red-700 rounded hover:bg-red-200 text-xs disabled:opacity-50"
                                    >
                                      {deleteIdentityLoading === identity.id ? '...' : 'Delete'}
                                    </button>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Source Pool Audits */}
                  {identityAuditResult.source_pool_audits && identityAuditResult.source_pool_audits.length > 0 && (
                    <div className="space-y-3">
                      <h3 className="font-semibold text-gray-900 flex items-center gap-2">
                        <Layers className="w-4 h-4" />
                        Source Pools ({identityAuditResult.source_pool_audits.length})
                      </h3>
                      {identityAuditResult.source_pool_audits.map((poolAudit) => (
                        <div key={poolAudit.pool_id} className="border rounded-lg overflow-hidden">
                          <div className={`px-4 py-3 flex justify-between items-center ${
                            poolAudit.orphan_identities > 0 ? 'bg-orange-50' : 'bg-gray-50'
                          }`}>
                            <div>
                              <h4 className="font-medium text-gray-900">{poolAudit.pool_name || poolAudit.pool_id}</h4>
                              <p className="text-xs text-gray-500">
                                {poolAudit.total_identities} identities, {poolAudit.total_passphrases} passphrases
                                {poolAudit.orphan_identities > 0 && (
                                  <span className="text-orange-600 font-medium ml-2">
                                    ({poolAudit.orphan_identities} orphans)
                                  </span>
                                )}
                              </p>
                            </div>
                            {poolAudit.orphan_identities > 0 && (
                              <button
                                onClick={() => bulkDeleteOrphanIdentities(
                                  selectedOrchestrator.id,
                                  poolAudit.orphans.map(o => o.id),
                                  poolAudit.pool_id
                                )}
                                className="px-3 py-1 bg-red-600 text-white rounded text-xs font-medium hover:bg-red-700"
                              >
                                Delete All {poolAudit.orphan_identities}
                              </button>
                            )}
                          </div>
                          {poolAudit.orphan_identities > 0 && (
                            <div className="max-h-32 overflow-y-auto">
                              <table className="w-full text-sm">
                                <tbody className="divide-y">
                                  {poolAudit.orphans.map((identity) => (
                                    <tr key={identity.id} className="hover:bg-gray-50">
                                      <td className="px-4 py-2">
                                        <span className="font-medium text-gray-900">{identity.name || '—'}</span>
                                        <span className="ml-2 text-xs text-gray-400 font-mono">{identity.id.substring(0, 8)}...</span>
                                      </td>
                                      <td className="px-4 py-2">
                                        {identity.vlan && (
                                          <span className="px-2 py-0.5 bg-blue-100 text-blue-800 rounded text-xs">
                                            {identity.vlan}
                                          </span>
                                        )}
                                      </td>
                                      <td className="px-4 py-2 text-right">
                                        <button
                                          onClick={() => deleteOrphanIdentity(selectedOrchestrator.id, identity.id, poolAudit.pool_id)}
                                          disabled={deleteIdentityLoading === identity.id}
                                          className="px-2 py-1 bg-red-100 text-red-700 rounded hover:bg-red-200 text-xs disabled:opacity-50"
                                        >
                                          {deleteIdentityLoading === identity.id ? '...' : 'Delete'}
                                        </button>
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}

                  {/* All Healthy Message */}
                  {identityAuditResult.total_orphan_identities === 0 && (
                    <div className="bg-green-50 rounded-lg p-6 text-center">
                      <Check className="w-12 h-12 text-green-500 mx-auto mb-3" />
                      <h3 className="font-semibold text-green-800 mb-1">All Identities Healthy</h3>
                      <p className="text-sm text-green-600">
                        No orphan identities found across any pools. All identities have associated passphrases.
                      </p>
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-center py-12 text-gray-500">
                  <Users className="w-12 h-12 mx-auto mb-3 text-gray-400" />
                  <p>Click "Run Audit" to scan all pools for identity issues</p>
                </div>
              )}
            </div>

            {/* Modal Footer */}
            <div className="bg-gray-50 px-6 py-4 flex justify-between items-center border-t">
              <div className="text-sm text-gray-500">
                {identityAuditResult && identityAuditResult.total_orphan_identities > 0 && (
                  <span className="text-orange-600 font-medium">
                    {identityAuditResult.total_orphan_identities} orphan{identityAuditResult.total_orphan_identities !== 1 ? 's' : ''} across {identityAuditResult.total_pools_audited} pools need attention
                  </span>
                )}
              </div>
              <div className="flex gap-3">
                <button
                  onClick={() => runIdentityAudit(selectedOrchestrator.id)}
                  disabled={identityAuditLoading}
                  className="px-4 py-2 bg-purple-600 text-white rounded hover:bg-purple-700 font-medium disabled:opacity-50"
                >
                  {identityAuditLoading ? 'Scanning...' : 'Run Audit'}
                </button>
                <button
                  onClick={() => setShowIdentityAuditModal(false)}
                  className="px-4 py-2 bg-gray-600 text-white rounded hover:bg-gray-700 font-medium"
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default DPSKOrchestrator;
