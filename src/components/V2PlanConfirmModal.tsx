/**
 * V2PlanConfirmModal - Workflow V2 plan review and confirmation modal
 *
 * Polls the V2 validation endpoint, displays the dry-run results
 * (resources to create/reuse, conflicts, estimated API calls),
 * and provides Confirm/Cancel buttons.
 *
 * Flow:
 *   Parent calls POST /per-unit-ssid/v2/plan → gets job_id
 *   → Opens this modal with jobId
 *   → Modal polls GET /per-unit-ssid/v2/{jobId}/plan
 *   → Shows validation result
 *   → User clicks Confirm → POST /per-unit-ssid/v2/{jobId}/confirm
 *   → onConfirm(jobId) → parent opens JobMonitorModal
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import WorkflowGraph from './WorkflowGraph';

const API_URL = import.meta.env.VITE_API_BASE_URL || "/api";

// ==================== Types ====================

interface ConflictItem {
  resource_type: string;
  resource_name: string;
  description: string;
  severity: 'error' | 'warning' | 'info';
  resolution?: string;
}

interface ActionItem {
  resource_type: string;
  resource_name: string;
  action: string;
  details?: string;
}

interface PlanResult {
  job_id: string;
  status: string;
  valid: boolean;
  summary: Record<string, any>;
  conflicts: ConflictItem[];
  unit_count: number;
  estimated_api_calls: number;
  actions: ActionItem[];
}

interface V2PlanConfirmModalProps {
  jobId: string;
  isOpen: boolean;
  workflowName: string;
  onClose: () => void;
  /** Called after confirm succeeds — parent should open JobMonitorModal */
  onConfirm: (jobId: string) => void;
  /** API prefix for plan/confirm endpoints (default: /per-unit-ssid/v2) */
  apiPrefix?: string;
}

// ==================== Component ====================

const V2PlanConfirmModal = ({
  jobId,
  isOpen,
  workflowName,
  onClose,
  onConfirm,
  apiPrefix = '/per-unit-ssid/v2',
}: V2PlanConfirmModalProps) => {
  const [plan, setPlan] = useState<PlanResult | null>(null);
  const [polling, setPolling] = useState(true);
  const [pollError, setPollError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [showGraph, setShowGraph] = useState(true);  // Expanded by default
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  const POLL_INTERVAL = 2000;
  const MAX_POLL_TIME = 5 * 60 * 1000; // 5 minutes

  const pollPlan = useCallback(async () => {
    const startTime = Date.now();

    const doPoll = async () => {
      if (!mountedRef.current) return;

      if (Date.now() - startTime > MAX_POLL_TIME) {
        setPollError('Validation timed out after 5 minutes');
        setPolling(false);
        return;
      }

      try {
        const response = await fetch(
          `${API_URL}${apiPrefix}/${jobId}/plan`,
          { credentials: 'include' }
        );

        if (!response.ok) {
          throw new Error(`Failed to fetch plan: ${response.status}`);
        }

        const data: PlanResult = await response.json();
        if (!mountedRef.current) return;

        setPlan(data);

        if (data.status === 'VALIDATING') {
          pollRef.current = setTimeout(doPoll, POLL_INTERVAL);
        } else {
          setPolling(false);
        }
      } catch (err) {
        if (!mountedRef.current) return;
        setPollError(err instanceof Error ? err.message : 'Failed to poll plan');
        setPolling(false);
      }
    };

    doPoll();
  }, [jobId]);

  useEffect(() => {
    mountedRef.current = true;

    if (isOpen && jobId) {
      setPolling(true);
      setPollError(null);
      setConfirmError(null);
      setPlan(null);
      pollPlan();
    }

    return () => {
      mountedRef.current = false;
      if (pollRef.current) {
        clearTimeout(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [isOpen, jobId, pollPlan]);

  const handleConfirm = async () => {
    setConfirming(true);
    setConfirmError(null);

    try {
      const response = await fetch(
        `${API_URL}${apiPrefix}/${jobId}/confirm`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
        }
      );

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(
          data.error || data.detail || `Confirm failed: ${response.status}`
        );
      }

      onConfirm(jobId);
    } catch (err) {
      setConfirmError(
        err instanceof Error ? err.message : 'Failed to confirm plan'
      );
    } finally {
      setConfirming(false);
    }
  };

  if (!isOpen) return null;

  const isValid = plan?.valid === true;
  const hasConflicts = (plan?.conflicts?.length ?? 0) > 0;
  const hasBlockingConflicts = plan?.conflicts?.some(
    (c) => c.severity === 'error'
  );

  // Categorize actions
  const createActions = plan?.actions?.filter(
    (a) => a.action === 'create'
  ) || [];
  const reuseActions = plan?.actions?.filter(
    (a) => a.action === 'reuse' || a.action === 'exists'
  ) || [];

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-4xl max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div
          className={`px-6 py-4 flex justify-between items-center ${
            polling
              ? 'bg-gradient-to-r from-blue-600 to-cyan-600'
              : isValid
              ? 'bg-gradient-to-r from-green-600 to-emerald-600'
              : 'bg-gradient-to-r from-red-600 to-orange-600'
          } text-white`}
        >
          <div>
            <h3 className="text-xl font-bold">
              {polling
                ? 'Validating Plan...'
                : isValid
                ? 'Plan Ready for Confirmation'
                : 'Validation Failed'}
            </h3>
            <p className="text-sm opacity-80 font-mono">{jobId}</p>
          </div>
          <button
            onClick={onClose}
            className="text-white hover:text-gray-200 text-2xl font-bold w-10 h-10 flex items-center justify-center rounded-full hover:bg-white hover:bg-opacity-20 transition-colors"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto flex-1 p-6">
          {/* Polling state */}
          {polling && (
            <div className="flex flex-col items-center justify-center py-12">
              <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mb-4" />
              <p className="text-gray-600 font-medium">
                Running validation (Phase 0)...
              </p>
              <p className="text-gray-400 text-sm mt-1">
                Checking existing resources and building execution plan
              </p>
            </div>
          )}

          {/* Poll error */}
          {pollError && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700 text-sm">
              {pollError}
            </div>
          )}

          {/* Plan result */}
          {!polling && plan && (
            <div className="space-y-6">
              {/* Summary cards */}
              <div className="grid grid-cols-3 gap-4">
                <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-center">
                  <div className="text-2xl font-bold text-blue-600">
                    {plan.unit_count}
                  </div>
                  <div className="text-xs text-gray-600 mt-1">Units</div>
                </div>
                <div className="bg-purple-50 border border-purple-200 rounded-lg p-4 text-center">
                  <div className="text-2xl font-bold text-purple-600">
                    {plan.estimated_api_calls}
                  </div>
                  <div className="text-xs text-gray-600 mt-1">
                    Estimated API Calls
                  </div>
                </div>
                <div
                  className={`rounded-lg p-4 text-center border ${
                    isValid
                      ? 'bg-green-50 border-green-200'
                      : 'bg-red-50 border-red-200'
                  }`}
                >
                  <div
                    className={`text-2xl font-bold ${
                      isValid ? 'text-green-600' : 'text-red-600'
                    }`}
                  >
                    {isValid ? 'Valid' : 'Invalid'}
                  </div>
                  <div className="text-xs text-gray-600 mt-1">
                    Validation Status
                  </div>
                </div>
              </div>

              {/* Validation summary details */}
              {plan.summary && Object.keys(plan.summary).length > 0 && (
                <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
                  <h4 className="text-sm font-semibold text-gray-700 mb-3">
                    Validation Summary
                  </h4>
                  <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                    {Object.entries(plan.summary).map(([key, value]) => (
                      <div key={key} className="text-sm">
                        <span className="text-gray-500">
                          {key.replace(/_/g, ' ')}:
                        </span>{' '}
                        <span className="font-medium text-gray-800">
                          {typeof value === 'object'
                            ? JSON.stringify(value)
                            : String(value)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Actions - what will be created vs reused */}
              {(createActions.length > 0 || reuseActions.length > 0) && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {/* Resources to create */}
                  {createActions.length > 0 && (
                    <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
                      <h4 className="text-sm font-semibold text-blue-800 mb-2">
                        Will Create ({createActions.length})
                      </h4>
                      <ul className="space-y-1.5 max-h-40 overflow-y-auto">
                        {createActions.map((action, idx) => (
                          <li
                            key={idx}
                            className="text-xs text-blue-700 flex items-start gap-1.5"
                          >
                            <span className="text-blue-400 mt-0.5 flex-shrink-0">
                              +
                            </span>
                            <span>
                              <span className="font-medium">
                                {action.resource_name}
                              </span>
                              <span className="text-blue-500 ml-1">
                                ({action.resource_type})
                              </span>
                              {action.details && (
                                <span className="text-blue-400 ml-1">
                                  - {action.details}
                                </span>
                              )}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Resources to reuse */}
                  {reuseActions.length > 0 && (
                    <div className="bg-green-50 border border-green-200 rounded-lg p-4">
                      <h4 className="text-sm font-semibold text-green-800 mb-2">
                        Will Reuse ({reuseActions.length})
                      </h4>
                      <ul className="space-y-1.5 max-h-40 overflow-y-auto">
                        {reuseActions.map((action, idx) => (
                          <li
                            key={idx}
                            className="text-xs text-green-700 flex items-start gap-1.5"
                          >
                            <span className="text-green-400 mt-0.5 flex-shrink-0">
                              =
                            </span>
                            <span>
                              <span className="font-medium">
                                {action.resource_name}
                              </span>
                              <span className="text-green-500 ml-1">
                                ({action.resource_type})
                              </span>
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}

              {/* Conflicts */}
              {hasConflicts && (
                <div
                  className={`border rounded-lg p-4 ${
                    hasBlockingConflicts
                      ? 'bg-red-50 border-red-200'
                      : 'bg-amber-50 border-amber-200'
                  }`}
                >
                  <h4
                    className={`text-sm font-semibold mb-2 ${
                      hasBlockingConflicts
                        ? 'text-red-800'
                        : 'text-amber-800'
                    }`}
                  >
                    {hasBlockingConflicts
                      ? 'Blocking Conflicts'
                      : 'Warnings'}{' '}
                    ({plan.conflicts.length})
                  </h4>
                  <ul className="space-y-2">
                    {plan.conflicts.map((conflict, idx) => (
                      <li
                        key={idx}
                        className="text-xs flex items-start gap-2"
                      >
                        <span className="flex-shrink-0 mt-0.5">
                          {conflict.severity === 'error'
                            ? '🚫'
                            : conflict.severity === 'warning'
                            ? '⚠️'
                            : 'ℹ️'}
                        </span>
                        <div>
                          <span className="font-medium text-gray-800">
                            {conflict.resource_type}:{' '}
                            {conflict.resource_name}
                          </span>
                          <p className="text-gray-600 mt-0.5">
                            {conflict.description}
                          </p>
                          {conflict.resolution && (
                            <p className="text-gray-500 italic mt-0.5">
                              Resolution: {conflict.resolution}
                            </p>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Workflow Graph toggle */}
              <div className="border border-gray-200 rounded-lg overflow-hidden">
                <button
                  onClick={() => setShowGraph(!showGraph)}
                  className="w-full px-4 py-3 flex items-center justify-between bg-gray-50 hover:bg-gray-100 transition-colors"
                >
                  <span className="text-sm font-medium text-gray-700">
                    {showGraph ? '▼' : '▶'} Workflow Execution Graph
                  </span>
                  <span className="text-xs text-gray-500">
                    {workflowName}
                  </span>
                </button>
                {showGraph && (
                  <WorkflowGraph
                    workflowName={workflowName}
                    height={300}
                    compact
                  />
                )}
              </div>

              {/* Confirm error */}
              {confirmError && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-red-700 text-sm">
                  {confirmError}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="bg-gray-50 px-6 py-4 flex justify-between items-center border-t">
          <div className="text-xs text-gray-400">
            V2 Workflow Engine
          </div>
          <div className="flex gap-3">
            <button
              onClick={onClose}
              disabled={confirming}
              className="px-5 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 font-medium transition-colors text-sm"
            >
              Cancel
            </button>
            {!polling && isValid && !hasBlockingConflicts && (
              <button
                onClick={handleConfirm}
                disabled={confirming}
                className={`px-6 py-2 rounded font-semibold text-sm transition-colors ${
                  confirming
                    ? 'bg-gray-400 cursor-not-allowed text-white'
                    : 'bg-green-600 hover:bg-green-700 text-white'
                }`}
              >
                {confirming ? 'Confirming...' : 'Confirm & Execute'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default V2PlanConfirmModal;
