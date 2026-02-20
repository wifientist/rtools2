import { useEffect, useState, useRef } from 'react';

const API_URL = import.meta.env.VITE_API_BASE_URL || "/api";

interface Task {
  id: string;
  name: string;
  status: string;
  started_at?: string;
  completed_at?: string;
  error_message?: string;
}

interface Phase {
  id: string;
  name: string;
  status: string;
  started_at?: string;
  completed_at?: string;
  duration_seconds?: number;
  tasks?: Task[];
  result?: Record<string, any>;
}

interface PhaseStat {
  name: string;
  completed?: number;
  failed?: number;
  running?: number;
  pending?: number;
  total?: number;
  status?: string;  // For global phases
}

interface Progress {
  total_tasks: number;
  completed: number;
  failed: number;
  pending: number;
  percent: number;
  // Phase-level progress
  total_phases?: number;
  completed_phases?: number;
  failed_phases?: number;
  running_phases?: number;
  phase_percent?: number;
  // Parallel job progress (items instead of tasks)
  total_items?: number;
  total_work?: number;
  completed_work?: number;
  units_completed?: number;
  units_failed?: number;
  running?: number;
  // Per-phase stats (V2 workflows)
  phase_stats?: Record<string, PhaseStat>;
}

interface ChildJob {
  job_id: string;
  item_id: string | number;
  status: string;
  current_phase: string | null;
  progress: Progress;
  errors: string[];
}

interface ParallelProgress {
  total_items: number;
  completed: number;
  failed: number;
  running: number;
  pending: number;
  percent: number;
}

interface JobStatus {
  job_id: string;
  status: string;
  progress: Progress;
  current_phase?: {
    id: string;
    name: string;
    status: string;
    tasks_completed: number;
    tasks_total: number;
  };
  phases: Phase[];
  created_resources: Record<string, any[]>;
  errors: string[];
  summary: Record<string, any>;
  // Parallel execution fields
  is_parallel: boolean;
  parallel_progress?: ParallelProgress;
  child_jobs?: ChildJob[];
}

export interface JobResult {
  job_id: string;
  status: string;
  progress: {
    total_phases: number;
    completed_phases: number;
    failed_phases: number;
    total_tasks: number;
    completed: number;
    failed: number;
  };
  summary?: Record<string, any>;
  created_resources?: Record<string, any[]>;
  errors?: string[];
}

interface JobMonitorViewProps {
  jobId: string;
  onClose?: () => void;
  showFullPageLink?: boolean;
  onCleanup?: (jobId: string) => void;
  onJobComplete?: (result: JobResult) => void;
}

const JobMonitorView = ({ jobId, onClose, showFullPageLink = false, onCleanup, onJobComplete }: JobMonitorViewProps) => {
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedPhases, setExpandedPhases] = useState<Set<string>>(new Set());
  const [expandedChildJobs, setExpandedChildJobs] = useState<Set<string>>(new Set());
  const [liveEvents, setLiveEvents] = useState<string[]>([]);
  const [cancelling, setCancelling] = useState(false);
  const [sseStatus, setSseStatus] = useState<'connecting' | 'connected' | 'disconnected'>('connecting');
  const [sseReconnects, setSseReconnects] = useState(0);

  const eventSourceRef = useRef<EventSource | null>(null);
  const lastRefreshRef = useRef<number>(0);
  const refreshPendingRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const fallbackPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const jobStatusRef = useRef<JobStatus | null>(null);

  // Keep ref in sync with state so event handlers always see latest
  useEffect(() => {
    jobStatusRef.current = jobStatus;
  }, [jobStatus]);

  const toggleChildJob = (jobId: string) => {
    setExpandedChildJobs(prev => {
      const next = new Set(prev);
      if (next.has(jobId)) {
        next.delete(jobId);
      } else {
        next.add(jobId);
      }
      return next;
    });
  };

  // Fetch initial job status
  useEffect(() => {
    const fetchJobStatus = async () => {
      try {
        setLoading(true);

        const response = await fetch(`${API_URL}/jobs/${jobId}/status`, {
          credentials: 'include',
        });

        if (!response.ok) {
          if (response.status === 404) {
            throw new Error('Job not found');
          } else if (response.status === 403) {
            throw new Error('Access denied to this job');
          }
          throw new Error('Failed to fetch job status');
        }

        const data = await response.json();
        setJobStatus(data);
        setError(null);
      } catch (err: any) {
        console.error('Error fetching job status:', err);
        setError(err.message || 'Failed to load job');
      } finally {
        setLoading(false);
      }
    };

    if (jobId) {
      fetchJobStatus();
    }
  }, [jobId]);

  // Set up SSE streaming for live updates
  useEffect(() => {
    if (!jobId) return;

    const eventSource = new EventSource(
      `${API_URL}/jobs/${jobId}/stream`,
      { withCredentials: true }
    );

    eventSourceRef.current = eventSource;

    eventSource.onopen = () => {
      setSseStatus('connected');
      // Stop fallback polling — SSE is back
      if (fallbackPollRef.current) {
        clearInterval(fallbackPollRef.current);
        fallbackPollRef.current = null;
      }
      setSseReconnects(prev => {
        if (prev > 0) {
          addLiveEvent('🔗 Reconnected to live stream');
          // Refresh full status on reconnect to catch anything we missed
          doRefresh();
        }
        return prev;
      });
    };

    eventSource.addEventListener('connected', (e) => {
      console.log('SSE connected:', e.data);
      setSseStatus('connected');
      addLiveEvent('🔗 Connected to live stream');
    });

    eventSource.addEventListener('status', (e) => {
      const data = JSON.parse(e.data);
      console.log('Status update:', data);
      addLiveEvent(`📊 Status: ${data.status} (${data.progress?.percent || 0}%)`);
    });

    eventSource.addEventListener('phase_started', (e) => {
      const data = JSON.parse(e.data);
      console.log('Phase started:', data);
      addLiveEvent(`▶️  Phase started: ${data.phase_name}`);
      refreshJobStatus();
    });

    eventSource.addEventListener('phase_completed', (e) => {
      const data = JSON.parse(e.data);
      console.log('Phase completed:', data);
      addLiveEvent(`✅ Phase completed: ${data.phase_name}`);
      refreshJobStatus();
    });

    eventSource.addEventListener('validation_failed', (e) => {
      const data = JSON.parse(e.data);
      console.log('Validation failed:', data);
      addLiveEvent(`❌ Validation failed: ${data.error || 'Unknown error'}`);
      refreshJobStatus();
    });

    eventSource.addEventListener('task_started', (e) => {
      const data = JSON.parse(e.data);
      console.log('Task started:', data);
      addLiveEvent(`⏳ Starting: ${data.task_name || data.task_id}`);
    });

    eventSource.addEventListener('task_completed', (e) => {
      const data = JSON.parse(e.data);
      const status = data.status === 'FAILED' ? '❌' : '✓';
      addLiveEvent(`${status} Completed: ${data.task_name || data.task_id}`);
      refreshJobStatus();
    });

    // Handler for progress events (used by both V1 'progress' and V2 'progress_update')
    // NOTE: Does NOT trigger refreshJobStatus - progress events are high-frequency
    // and the SSE data already contains the info we need for the live feed.
    // The throttled refresh from phase_started/phase_completed events keeps the
    // full job status up to date.
    const handleProgress = (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      // V2 wraps progress in a 'progress' field
      const progress = data.progress || data;

      // Show appropriate progress based on job type
      if (progress.total_work !== undefined) {
        // V2 workflow - show work progress
        const failed = progress.units_failed ? ` (${progress.units_failed} failed)` : '';
        addLiveEvent(`📈 Progress: ${progress.completed_work}/${progress.total_work} units${failed}`);
      } else if (progress.total_items !== undefined) {
        // Parallel job - show items progress
        const running = progress.running ? ` (${progress.running} running)` : '';
        addLiveEvent(`📈 Progress: ${progress.completed}/${progress.total_items} items complete${running}`);
      } else if (progress.total_phases) {
        // Sequential job - show phase progress
        addLiveEvent(`📈 Progress: ${progress.completed_phases}/${progress.total_phases} phases complete`);
      } else {
        // Fallback
        addLiveEvent(`📈 Progress: ${progress.percent}% (${progress.completed}/${progress.total_tasks || progress.total || '?'})`);
      }
      // Throttled refresh to keep phase stats / progress bar updated
      refreshJobStatus();
    };

    eventSource.addEventListener('progress', handleProgress);
    eventSource.addEventListener('progress_update', handleProgress);

    eventSource.addEventListener('message', (e) => {
      const data = JSON.parse(e.data);
      console.log('Message:', data);
      const icons: Record<string, string> = {
        info: 'ℹ️',
        warning: '⚠️',
        error: '❌',
        success: '✅'
      };
      const icon = icons[data.level] || 'ℹ️';
      addLiveEvent(`${icon} ${data.message}`);
    });

    // Child job events (for parallel execution)
    eventSource.addEventListener('child_completed', (e) => {
      const data = JSON.parse(e.data);
      console.log('Child completed:', data);
      addLiveEvent(`✓ Batch ${data.item_id} completed`);
      refreshJobStatus();
    });

    eventSource.addEventListener('child_failed', (e) => {
      const data = JSON.parse(e.data);
      console.log('Child failed:', data);
      addLiveEvent(`❌ Batch ${data.item_id} failed: ${data.errors?.[0] || 'Unknown error'}`);
      refreshJobStatus();
    });

    eventSource.addEventListener('job_completed', async (e) => {
      const data = JSON.parse(e.data);
      addLiveEvent(`🎉 Job completed!`);
      await doRefresh();  // Immediate final refresh
      // Notify parent of completion
      if (onJobComplete) {
        onJobComplete({
          job_id: jobId,
          status: data.status || 'COMPLETED',
          progress: data.progress || {
            total_phases: data.total_phases || 0,
            completed_phases: data.completed_phases || 0,
            failed_phases: data.failed_phases || 0,
            total_tasks: data.total_tasks || 0,
            completed: data.completed || 0,
            failed: data.failed || 0,
          },
          summary: data.summary,
          created_resources: data.created_resources,
          errors: data.errors,
        });
      }
      eventSource.close();
    });

    eventSource.addEventListener('job_failed', async (e) => {
      const data = JSON.parse(e.data);
      addLiveEvent(`❌ Job failed: ${data.error || 'Unknown error'}`);
      await doRefresh();  // Immediate final refresh
      // Notify parent of completion (even for failures)
      if (onJobComplete) {
        onJobComplete({
          job_id: jobId,
          status: data.status || 'FAILED',
          progress: data.progress || {
            total_phases: data.total_phases || 0,
            completed_phases: data.completed_phases || 0,
            failed_phases: data.failed_phases || 0,
            total_tasks: data.total_tasks || 0,
            completed: data.completed || 0,
            failed: data.failed || 0,
          },
          summary: data.summary,
          errors: data.errors || [data.error || 'Unknown error'],
        });
      }
      eventSource.close();
    });

    eventSource.addEventListener('job_cancelled', async (e) => {
      const data = JSON.parse(e.data);
      addLiveEvent(`🛑 Job cancelled by user`);
      setCancelling(false);
      await doRefresh();  // Immediate final refresh
      // Notify parent of completion
      if (onJobComplete) {
        onJobComplete({
          job_id: jobId,
          status: 'CANCELLED',
          progress: data.progress || {
            total_phases: data.total_phases || 0,
            completed_phases: data.completed_phases || 0,
            failed_phases: data.failed_phases || 0,
            total_tasks: data.total_tasks || 0,
            completed: data.completed || 0,
            failed: data.failed || 0,
          },
          summary: data.summary,
          errors: ['Job cancelled by user'],
        });
      }
      eventSource.close();
    });

    eventSource.onerror = (err) => {
      console.error('SSE error:', err);
      setSseStatus('disconnected');

      // Use ref to get latest job status (avoids stale closure)
      const currentStatus = jobStatusRef.current?.status;
      if (currentStatus === 'COMPLETED' || currentStatus === 'FAILED' || currentStatus === 'PARTIAL' || currentStatus === 'CANCELLED') {
        console.log('Job in terminal state, closing SSE connection');
        addLiveEvent('Stream closed (job finished)');
        eventSource.close();
      } else {
        setSseReconnects(prev => prev + 1);
        addLiveEvent('⚠️ Stream disconnected, auto-reconnecting...');
        // Start fallback polling while SSE is down
        if (!fallbackPollRef.current) {
          fallbackPollRef.current = setInterval(() => {
            doRefresh();
          }, 10000); // Poll every 10s as fallback
        }
      }
    };

    return () => {
      eventSource.close();
      if (refreshPendingRef.current) {
        clearTimeout(refreshPendingRef.current);
        refreshPendingRef.current = null;
      }
      if (fallbackPollRef.current) {
        clearInterval(fallbackPollRef.current);
        fallbackPollRef.current = null;
      }
    };
  }, [jobId]);

  // Throttled refresh: at most once every 2 seconds to prevent request storms
  // with 300+ parallel units firing events rapidly
  const REFRESH_THROTTLE_MS = 2000;

  const doRefresh = async () => {
    if (!jobId) return;
    lastRefreshRef.current = Date.now();
    try {
      const response = await fetch(`${API_URL}/jobs/${jobId}/status`, {
        credentials: 'include',
      });
      if (response.ok) {
        const data = await response.json();
        setJobStatus(data);
      }
    } catch (err) {
      console.error('Error refreshing job status:', err);
    }
  };

  const refreshJobStatus = (immediate = false) => {
    if (immediate) {
      // Clear any pending throttled refresh
      if (refreshPendingRef.current) {
        clearTimeout(refreshPendingRef.current);
        refreshPendingRef.current = null;
      }
      doRefresh();
      return;
    }

    const now = Date.now();
    const elapsed = now - lastRefreshRef.current;

    if (elapsed >= REFRESH_THROTTLE_MS) {
      // Enough time has passed, refresh immediately
      doRefresh();
    } else if (!refreshPendingRef.current) {
      // Schedule a refresh for when the throttle window expires
      const delay = REFRESH_THROTTLE_MS - elapsed;
      refreshPendingRef.current = setTimeout(() => {
        refreshPendingRef.current = null;
        doRefresh();
      }, delay);
    }
    // else: a refresh is already scheduled, skip
  };

  const addLiveEvent = (message: string) => {
    const timestamp = new Date().toLocaleTimeString();
    setLiveEvents(prev => [`[${timestamp}] ${message}`, ...prev].slice(0, 50));
  };

  const reconnectSSE = () => {
    // Close existing connection
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }
    // Stop fallback polling
    if (fallbackPollRef.current) {
      clearInterval(fallbackPollRef.current);
      fallbackPollRef.current = null;
    }
    // Create new connection
    setSseStatus('connecting');
    addLiveEvent('🔄 Manual reconnect...');
    const newEventSource = new EventSource(
      `${API_URL}/jobs/${jobId}/stream`,
      { withCredentials: true }
    );
    eventSourceRef.current = newEventSource;

    // Re-attach the same handlers (simplified — key ones)
    newEventSource.onopen = () => {
      setSseStatus('connected');
      if (fallbackPollRef.current) {
        clearInterval(fallbackPollRef.current);
        fallbackPollRef.current = null;
      }
      addLiveEvent('🔗 Reconnected to live stream');
      doRefresh();
    };

    newEventSource.addEventListener('connected', () => {
      setSseStatus('connected');
      addLiveEvent('🔗 Connected to live stream');
    });

    const handleProgress = (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      const progress = data.progress || data;
      if (progress.total_work !== undefined) {
        const failed = progress.units_failed ? ` (${progress.units_failed} failed)` : '';
        addLiveEvent(`📈 Progress: ${progress.completed_work}/${progress.total_work} units${failed}`);
      } else {
        addLiveEvent(`📈 Progress: ${progress.percent || 0}%`);
      }
      refreshJobStatus();
    };

    newEventSource.addEventListener('progress', handleProgress);
    newEventSource.addEventListener('progress_update', handleProgress);

    newEventSource.addEventListener('message', (e) => {
      const data = JSON.parse(e.data);
      const icons: Record<string, string> = { info: 'ℹ️', warning: '⚠️', error: '❌', success: '✅' };
      addLiveEvent(`${icons[data.level] || 'ℹ️'} ${data.message}`);
    });

    newEventSource.addEventListener('phase_started', (e) => {
      const data = JSON.parse(e.data);
      addLiveEvent(`▶️  Phase started: ${data.phase_name}`);
      refreshJobStatus();
    });

    newEventSource.addEventListener('phase_completed', (e) => {
      const data = JSON.parse(e.data);
      addLiveEvent(`✅ Phase completed: ${data.phase_name}`);
      refreshJobStatus();
    });

    newEventSource.addEventListener('task_completed', (e) => {
      const data = JSON.parse(e.data);
      const status = data.status === 'FAILED' ? '❌' : '✓';
      addLiveEvent(`${status} Completed: ${data.task_name || data.task_id}`);
      refreshJobStatus();
    });

    newEventSource.addEventListener('job_completed', async (e) => {
      const data = JSON.parse(e.data);
      addLiveEvent('🎉 Job completed!');
      await doRefresh();
      if (onJobComplete) {
        onJobComplete({
          job_id: jobId, status: data.status || 'COMPLETED',
          progress: data.progress || { total_phases: 0, completed_phases: 0, failed_phases: 0, total_tasks: 0, completed: 0, failed: 0 },
          summary: data.summary, created_resources: data.created_resources, errors: data.errors,
        });
      }
      newEventSource.close();
    });

    newEventSource.addEventListener('job_failed', async (e) => {
      const data = JSON.parse(e.data);
      addLiveEvent(`❌ Job failed: ${data.error || 'Unknown error'}`);
      await doRefresh();
      if (onJobComplete) {
        onJobComplete({
          job_id: jobId, status: data.status || 'FAILED',
          progress: data.progress || { total_phases: 0, completed_phases: 0, failed_phases: 0, total_tasks: 0, completed: 0, failed: 0 },
          errors: data.errors || [data.error || 'Unknown error'],
        });
      }
      newEventSource.close();
    });

    newEventSource.onerror = () => {
      setSseStatus('disconnected');
      const currentStatus = jobStatusRef.current?.status;
      if (currentStatus === 'COMPLETED' || currentStatus === 'FAILED' || currentStatus === 'PARTIAL' || currentStatus === 'CANCELLED') {
        newEventSource.close();
      } else {
        addLiveEvent('⚠️ Stream disconnected, auto-reconnecting...');
        if (!fallbackPollRef.current) {
          fallbackPollRef.current = setInterval(() => doRefresh(), 10000);
        }
      }
    };
  };

  const cancelJob = async () => {
    if (!jobId || cancelling) return;

    setCancelling(true);
    addLiveEvent('🛑 Requesting job cancellation...');

    try {
      const response = await fetch(`${API_URL}/jobs/${jobId}/cancel`, {
        method: 'POST',
        credentials: 'include',
      });

      if (!response.ok) {
        throw new Error('Failed to cancel job');
      }

      const data = await response.json();
      addLiveEvent(`🛑 ${data.message}`);
      await doRefresh();
    } catch (err: any) {
      console.error('Error cancelling job:', err);
      addLiveEvent(`❌ Failed to cancel: ${err.message}`);
      setCancelling(false);
    }
  };

  const togglePhase = (phaseId: string) => {
    setExpandedPhases(prev => {
      const next = new Set(prev);
      if (next.has(phaseId)) {
        next.delete(phaseId);
      } else {
        next.add(phaseId);
      }
      return next;
    });
  };

  const getStatusColor = (status: string): string => {
    switch (status.toUpperCase()) {
      case 'COMPLETED':
        return 'text-green-600 bg-green-50 border-green-200';
      case 'RUNNING':
      case 'IN_PROGRESS':
        return 'text-blue-600 bg-blue-50 border-blue-200';
      case 'VALIDATING':
        return 'text-cyan-600 bg-cyan-50 border-cyan-200';
      case 'AWAITING_CONFIRMATION':
        return 'text-purple-600 bg-purple-50 border-purple-200';
      case 'FAILED':
        return 'text-red-600 bg-red-50 border-red-200';
      case 'CANCELLED':
        return 'text-orange-600 bg-orange-50 border-orange-200';
      case 'PENDING':
        return 'text-gray-600 bg-gray-50 border-gray-200';
      case 'SKIPPED':
        return 'text-yellow-600 bg-yellow-50 border-yellow-200';
      default:
        return 'text-gray-600 bg-gray-50 border-gray-200';
    }
  };

  const getStatusIcon = (status: string): string => {
    switch (status.toUpperCase()) {
      case 'COMPLETED':
        return '✅';
      case 'RUNNING':
      case 'IN_PROGRESS':
        return '▶️';
      case 'FAILED':
        return '❌';
      case 'CANCELLED':
        return '🛑';
      case 'PENDING':
        return '⏸️';
      case 'SKIPPED':
        return '⏭️';
      default:
        return '⚪';
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto mb-4"></div>
          <p className="text-gray-600">Loading job status...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6">
          <h2 className="text-xl font-bold text-red-800 mb-2">Error</h2>
          <p className="text-red-600">{error}</p>
          {onClose && (
            <button
              onClick={onClose}
              className="mt-4 px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700"
            >
              Close
            </button>
          )}
        </div>
      </div>
    );
  }

  if (!jobStatus) {
    return (
      <div className="p-6">
        <p className="text-gray-600">No job data available</p>
      </div>
    );
  }

  return (
    <div className="p-6">
      {/* Header with optional full page link */}
      {showFullPageLink && (
        <div className="mb-4 flex justify-end">
          <a
            href={`/jobs/${jobId}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm text-blue-600 hover:text-blue-800 hover:underline"
          >
            Open in Full Page →
          </a>
        </div>
      )}

      {/* Job Overview */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <p className="text-sm text-gray-500">Job ID</p>
            <p className="font-mono text-sm">{jobStatus.job_id}</p>
          </div>
          <div>
            <p className="text-sm text-gray-500">Status</p>
            <span className={`inline-flex items-center gap-1 px-3 py-1 rounded-full text-sm font-medium border ${getStatusColor(jobStatus.status)}`}>
              {getStatusIcon(jobStatus.status)} {jobStatus.status}
            </span>
            {jobStatus.is_parallel && (
              <span className="ml-2 px-2 py-0.5 text-xs bg-purple-100 text-purple-700 rounded">Parallel</span>
            )}
          </div>
          {jobStatus.is_parallel ? (
            <>
              <div>
                <p className="text-sm text-gray-500">Units</p>
                <p className="text-lg font-bold">
                  {jobStatus.parallel_progress?.completed ?? 0}/{jobStatus.parallel_progress?.total_items ?? jobStatus.child_jobs?.length ?? 0}
                  {(jobStatus.parallel_progress?.running ?? 0) > 0 && (
                    <span className="text-blue-600 text-sm font-normal ml-1">({jobStatus.parallel_progress?.running} running)</span>
                  )}
                </p>
              </div>
              <div>
                <p className="text-sm text-gray-500">Progress</p>
                <p className="text-sm">
                  {jobStatus.parallel_progress?.completed ?? 0} completed
                  {(jobStatus.parallel_progress?.failed ?? 0) > 0 && (
                    <span className="text-red-600 ml-2">({jobStatus.parallel_progress?.failed} failed)</span>
                  )}
                  {(jobStatus.parallel_progress?.pending ?? 0) > 0 && (
                    <span className="text-gray-500 ml-2">({jobStatus.parallel_progress?.pending} pending)</span>
                  )}
                </p>
              </div>
            </>
          ) : (
            <>
              <div>
                <p className="text-sm text-gray-500">Phases</p>
                <p className="text-lg font-bold">
                  {jobStatus.progress?.completed_phases ?? 0}/{jobStatus.progress?.total_phases ?? jobStatus.phases?.length ?? 0}
                  {jobStatus.progress?.running_phases ? (
                    <span className="text-blue-600 text-sm font-normal ml-1">(1 running)</span>
                  ) : null}
                </p>
              </div>
              <div>
                <p className="text-sm text-gray-500">Tasks</p>
                <p className="text-sm">
                  {jobStatus.progress?.completed ?? 0}/{jobStatus.progress?.total_tasks ?? 0} completed
                  {(jobStatus.progress?.failed ?? 0) > 0 && (
                    <span className="text-red-600 ml-2">({jobStatus.progress?.failed} failed)</span>
                  )}
                </p>
              </div>
            </>
          )}
        </div>

        {/* Progress Bar */}
        <div className="mt-4">
          <div className="w-full bg-gray-200 rounded-full h-3 overflow-hidden">
            <div
              className={`h-full transition-all duration-500 ${
                jobStatus.progress.failed > 0 ? 'bg-gradient-to-r from-blue-600 to-red-500' : 'bg-blue-600'
              }`}
              style={{ width: `${jobStatus.parallel_progress?.percent ?? jobStatus.progress.phase_percent ?? jobStatus.progress.percent}%` }}
            />
          </div>
        </div>

        {/* Phase Progress Stats (V2 workflows) */}
        {jobStatus.progress?.phase_stats && Object.keys(jobStatus.progress.phase_stats).length > 0 && (
          <div className="mt-4 grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
            {Object.entries(jobStatus.progress.phase_stats)
              .filter(([, stat]) => stat.status !== 'SKIPPED')
              .map(([phaseId, stat]) => {
              const isPerUnit = stat.total !== undefined;
              const isRunning = isPerUnit ? (stat.running ?? 0) > 0 : stat.status === 'RUNNING';
              const isComplete = isPerUnit ? stat.completed === stat.total : stat.status === 'COMPLETED';
              const hasFailed = isPerUnit && (stat.failed ?? 0) > 0;

              return (
                <div
                  key={phaseId}
                  className={`p-2 rounded border text-xs ${
                    isComplete
                      ? 'bg-green-50 border-green-200'
                      : isRunning
                      ? 'bg-blue-50 border-blue-200'
                      : hasFailed
                      ? 'bg-red-50 border-red-200'
                      : 'bg-gray-50 border-gray-200'
                  }`}
                >
                  <div className="font-medium text-gray-700 truncate" title={stat.name}>
                    {stat.name}
                  </div>
                  {isPerUnit ? (
                    <div className="mt-1 flex items-center gap-1 text-gray-600">
                      <span className={isComplete ? 'text-green-600 font-semibold' : ''}>
                        {stat.completed ?? 0}/{stat.total}
                      </span>
                      {isRunning && (
                        <span className="text-blue-600">({stat.running} running)</span>
                      )}
                      {hasFailed && (
                        <span className="text-red-600">({stat.failed} failed)</span>
                      )}
                    </div>
                  ) : (
                    <div className={`mt-1 ${
                      stat.status === 'COMPLETED' ? 'text-green-600' :
                      stat.status === 'RUNNING' ? 'text-blue-600' :
                      stat.status === 'FAILED' ? 'text-red-600' :
                      'text-gray-500'
                    }`}>
                      {stat.status || 'PENDING'}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Parallel Job Quick Summary - counts only, details in phase */}
        {jobStatus.is_parallel && jobStatus.child_jobs && jobStatus.status === 'RUNNING' && (
          <div className="mt-4 p-3 bg-purple-50 border border-purple-200 rounded flex items-center justify-between">
            <p className="text-sm text-purple-800 font-medium">Parallel Batches</p>
            <div className="flex gap-3 text-xs">
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-green-500"></span>
                {jobStatus.parallel_progress?.completed || 0} done
              </span>
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse"></span>
                {jobStatus.parallel_progress?.running || 0} running
              </span>
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-gray-300"></span>
                {jobStatus.parallel_progress?.pending || 0} pending
              </span>
              {(jobStatus.parallel_progress?.failed || 0) > 0 && (
                <span className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-red-500"></span>
                  {jobStatus.parallel_progress?.failed} failed
                </span>
              )}
            </div>
          </div>
        )}

        {/* Current Phase (for non-parallel jobs) */}
        {!jobStatus.is_parallel && jobStatus.current_phase && (
          <div className="mt-4 p-4 bg-blue-50 border border-blue-200 rounded">
            <p className="text-sm text-blue-800 font-medium">Currently Running:</p>
            <p className="text-blue-900">{jobStatus.current_phase.name}</p>
            <p className="text-sm text-blue-600 mt-1">
              {jobStatus.current_phase.tasks_completed}/{jobStatus.current_phase.tasks_total} tasks completed
            </p>
          </div>
        )}

        {/* Stop Job Button for Running Jobs */}
        {(jobStatus.status === 'RUNNING' || jobStatus.status === 'PENDING') && (
          <div className="mt-4 p-4 bg-orange-50 border border-orange-200 rounded">
            <p className="text-sm text-orange-800 mb-3">
              Job is currently {jobStatus.status.toLowerCase()}. You can stop it at any time.
            </p>
            <button
              onClick={cancelJob}
              disabled={cancelling}
              className={`px-4 py-2 rounded font-semibold transition-colors ${
                cancelling
                  ? 'bg-gray-400 cursor-not-allowed text-white'
                  : 'bg-orange-600 hover:bg-orange-700 text-white'
              }`}
            >
              {cancelling ? '🛑 Cancelling...' : '🛑 Stop Job'}
            </button>
          </div>
        )}

        {/* Cleanup Button for Failed Jobs */}
        {(jobStatus.status === 'FAILED' || jobStatus.status === 'PARTIAL') && onCleanup && Object.keys(jobStatus.created_resources).length > 0 && (
          <div className="mt-4 p-4 bg-yellow-50 border border-yellow-200 rounded">
            <p className="text-sm text-yellow-800 mb-3">
              {jobStatus.status === 'FAILED' ? 'The job failed.' : 'The job partially completed.'} You can clean up the created resources.
            </p>
            <button
              onClick={() => onCleanup(jobId)}
              className="px-4 py-2 rounded font-semibold bg-yellow-600 hover:bg-yellow-700 text-white transition-colors"
            >
              🧹 Cleanup Resources
            </button>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Main Panel - Show phases OR unit progress depending on parallel pattern */}
        <div className="lg:col-span-2">
          {/*
            Two parallel patterns:
            1. Batch parallelism: Parent has phases defined, child jobs are batches within one phase
            2. Unit parallelism: Parent has NO phases, each child runs full workflow independently

            Always show phases view if parent has phases defined (phases.length > 0)
          */}
          {jobStatus.is_parallel && jobStatus.child_jobs && jobStatus.phases.length === 0 ? (
            /* Unit Parallelism - Full workflows per unit (e.g., per-unit SSID) */
            <div className="bg-white rounded-lg shadow">
              <div className="p-4 border-b border-gray-200">
                <h2 className="text-xl font-bold text-gray-900">Unit Progress</h2>
                <p className="text-sm text-gray-500 mt-1">
                  Each unit runs through all phases independently
                </p>
              </div>
              <div className="divide-y divide-gray-200 max-h-[600px] overflow-y-auto">
                {jobStatus.child_jobs.map((child) => (
                  <div key={child.job_id} className="p-4">
                    <div
                      className="flex items-center justify-between cursor-pointer"
                      onClick={() => toggleChildJob(child.job_id)}
                    >
                      <div className="flex items-center gap-3">
                        <span className="text-xl">{getStatusIcon(child.status)}</span>
                        <div>
                          <h3 className="font-medium text-gray-900">Unit {child.item_id}</h3>
                          <div className="flex items-center gap-2 mt-1">
                            <span className={`inline-block px-2 py-0.5 text-xs rounded ${getStatusColor(child.status)}`}>
                              {child.status}
                            </span>
                            {child.current_phase && child.status === 'RUNNING' && (
                              <span className="text-xs text-blue-600">
                                → {child.current_phase}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-3">
                        {child.progress && (child.progress.total_phases ?? 0) > 0 && (
                          <div className="flex items-center gap-2">
                            <div className="w-20 bg-gray-200 rounded-full h-2 overflow-hidden">
                              <div
                                className={`h-full transition-all duration-300 ${
                                  child.status === 'FAILED' ? 'bg-red-500' :
                                  child.status === 'COMPLETED' ? 'bg-green-500' : 'bg-blue-500'
                                }`}
                                style={{ width: `${child.progress.phase_percent ?? 0}%` }}
                              />
                            </div>
                            <span className="text-xs text-gray-500 whitespace-nowrap">
                              {child.progress.completed_phases ?? 0}/{child.progress.total_phases ?? 0}
                            </span>
                          </div>
                        )}
                        <span className="text-gray-400">
                          {expandedChildJobs.has(child.job_id) ? '▼' : '▶'}
                        </span>
                      </div>
                    </div>
                    {expandedChildJobs.has(child.job_id) && (
                      <div className="mt-3 ml-8 space-y-2">
                        {child.errors && child.errors.length > 0 && (
                          <div className="p-3 bg-red-50 rounded border border-red-200">
                            <p className="text-xs font-medium text-red-800 mb-1">Errors:</p>
                            {child.errors.map((error, idx) => (
                              <p key={idx} className="text-xs text-red-600">{error}</p>
                            ))}
                          </div>
                        )}
                        {child.current_phase && child.status === 'RUNNING' && (
                          <div className="p-3 bg-blue-50 rounded border border-blue-200">
                            <p className="text-xs text-blue-800">Running: {child.current_phase}</p>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ) : (
            /* Sequential OR Batch Parallelism - Show phases (with batches under parallel phase) */
            <div className="bg-white rounded-lg shadow">
              <div className="p-4 border-b border-gray-200">
                <h2 className="text-xl font-bold text-gray-900">Phases</h2>
              </div>
              <div className="divide-y divide-gray-200 max-h-[600px] overflow-y-auto">
                {jobStatus.phases.map((phase) => {
                  // Get per-unit stats from phase_stats if available
                  const phaseStat = jobStatus.progress?.phase_stats?.[phase.id] as PhaseStat | undefined;
                  const isPerUnit = phaseStat && phaseStat.total !== undefined;
                  const statCompleted = phaseStat?.completed ?? 0;
                  const statFailed = phaseStat?.failed ?? 0;
                  const statRunning = phaseStat?.running ?? 0;
                  const statTotal = phaseStat?.total ?? 0;
                  const statPercent = statTotal > 0 ? ((statCompleted + statFailed) / statTotal) * 100 : 0;

                  // Use SKIPPED status from backend if phase was skip_if'd
                  const isSkipped = phase.status === 'SKIPPED' || phaseStat?.status === 'SKIPPED';

                  // Derive a richer status for per-unit phases
                  const effectiveStatus = isSkipped
                    ? 'SKIPPED'
                    : isPerUnit
                    ? (statCompleted === statTotal && statTotal > 0 ? 'COMPLETED'
                      : statRunning > 0 ? 'RUNNING'
                      : statCompleted > 0 || statFailed > 0 ? 'RUNNING'
                      : phase.status)
                    : phase.status;

                  return (
                  <div key={phase.id} className="p-4">
                    <div
                      className="flex items-center justify-between cursor-pointer"
                      onClick={() => togglePhase(phase.id)}
                    >
                      <div className="flex items-center gap-3">
                        <span className="text-xl">{getStatusIcon(effectiveStatus)}</span>
                        <div>
                          <h3 className="font-medium text-gray-900">{phase.name}</h3>
                          <div className="flex items-center gap-2 mt-0.5">
                            <span className={`inline-block px-2 py-0.5 text-xs rounded ${getStatusColor(effectiveStatus)}`}>
                              {effectiveStatus}
                            </span>
                            {/* Inline per-unit counts next to status badge */}
                            {isPerUnit && statTotal > 0 && (
                              <span className="text-xs text-gray-500">
                                {statCompleted}/{statTotal} units
                                {statRunning > 0 && (
                                  <span className="text-blue-600 ml-1">({statRunning} active)</span>
                                )}
                                {statFailed > 0 && (
                                  <span className="text-red-600 ml-1">({statFailed} failed)</span>
                                )}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-3">
                        {/* Per-unit progress bar from phase_stats */}
                        {isPerUnit && statTotal > 0 && (
                          <div className="flex items-center gap-2">
                            <div className="w-24 bg-gray-200 rounded-full h-2 overflow-hidden">
                              {/* Stacked bar: green for completed, red for failed */}
                              <div className="h-full flex">
                                <div
                                  className="h-full bg-green-500 transition-all duration-300"
                                  style={{ width: `${(statCompleted / statTotal) * 100}%` }}
                                />
                                {statFailed > 0 && (
                                  <div
                                    className="h-full bg-red-500 transition-all duration-300"
                                    style={{ width: `${(statFailed / statTotal) * 100}%` }}
                                  />
                                )}
                              </div>
                            </div>
                            <span className="text-xs text-gray-500 whitespace-nowrap font-medium">
                              {Math.round(statPercent)}%
                            </span>
                          </div>
                        )}
                        {/* Legacy task-based progress bar */}
                        {!isPerUnit && phase.tasks && phase.tasks.length > 0 && (
                          <div className="flex items-center gap-2">
                            <div className="w-20 bg-gray-200 rounded-full h-2 overflow-hidden">
                              <div
                                className={`h-full transition-all duration-300 ${
                                  phase.status === 'FAILED' ? 'bg-red-500' :
                                  phase.status === 'COMPLETED' ? 'bg-green-500' : 'bg-blue-500'
                                }`}
                                style={{
                                  width: `${phase.tasks.length > 0
                                    ? (phase.tasks.filter(t => t.status === 'COMPLETED' || t.status === 'FAILED').length / phase.tasks.length) * 100
                                    : 0}%`
                                }}
                              />
                            </div>
                            <span className="text-xs text-gray-500 whitespace-nowrap">
                              {phase.tasks.filter(t => t.status === 'COMPLETED').length}/{phase.tasks.length}
                            </span>
                          </div>
                        )}
                        {phase.duration_seconds !== undefined && phase.duration_seconds !== null && (
                          <span className="text-sm text-gray-500">
                            {phase.duration_seconds.toFixed(1)}s
                          </span>
                        )}
                        <span className="text-gray-400">
                          {expandedPhases.has(phase.id) ? '▼' : '▶'}
                        </span>
                      </div>
                    </div>

                    {/* Expanded Phase Details */}
                    {expandedPhases.has(phase.id) && (
                      <div className="mt-3 ml-8 space-y-3">
                        {/* Per-unit phase breakdown */}
                        {isPerUnit && statTotal > 0 && (
                          <div className={`p-3 rounded border ${
                            statCompleted === statTotal ? 'bg-green-50 border-green-200' :
                            statFailed > 0 ? 'bg-red-50 border-red-200' :
                            statRunning > 0 ? 'bg-blue-50 border-blue-200' :
                            'bg-gray-50 border-gray-200'
                          }`}>
                            <div className="grid grid-cols-4 gap-3 text-center">
                              <div>
                                <div className="text-lg font-bold text-green-700">{statCompleted}</div>
                                <div className="text-xs text-gray-500">Completed</div>
                              </div>
                              <div>
                                <div className={`text-lg font-bold ${statRunning > 0 ? 'text-blue-700' : 'text-gray-400'}`}>{statRunning}</div>
                                <div className="text-xs text-gray-500">Running</div>
                              </div>
                              <div>
                                <div className={`text-lg font-bold ${statFailed > 0 ? 'text-red-700' : 'text-gray-400'}`}>{statFailed}</div>
                                <div className="text-xs text-gray-500">Failed</div>
                              </div>
                              <div>
                                <div className={`text-lg font-bold ${(statTotal - statCompleted - statFailed - statRunning) > 0 ? 'text-gray-700' : 'text-gray-400'}`}>
                                  {statTotal - statCompleted - statFailed - statRunning}
                                </div>
                                <div className="text-xs text-gray-500">Pending</div>
                              </div>
                            </div>
                          </div>
                        )}

                        {/* Phase Result Summary */}
                        {phase.result && Object.keys(phase.result).length > 0 && (
                          <div className="p-3 bg-blue-50 rounded border border-blue-200">
                            <p className="text-xs font-medium text-blue-900 mb-2">Phase Summary:</p>
                            <div className="text-xs text-blue-800 space-y-1">
                              {Object.entries(phase.result).map(([key, value]) => {
                                if (key === 'created_count' || key === 'total_count' || key === 'processed_count') {
                                  return (
                                    <div key={key} className="flex justify-between">
                                      <span className="font-medium">{key.replace(/_/g, ' ')}:</span>
                                      <span>{String(value)}</span>
                                    </div>
                                  );
                                }
                                return null;
                              })}
                            </div>
                          </div>
                        )}

                        {/* Tasks List (for non-parallel phases) */}
                        {phase.tasks && phase.tasks.length > 0 ? (
                          <div className="space-y-2">
                            {phase.tasks.map((task) => (
                              <div
                                key={task.id}
                                className="p-3 bg-gray-50 rounded border border-gray-200"
                              >
                                <div className="flex items-center justify-between mb-2">
                                  <span className="flex items-center gap-2 font-medium">
                                    {getStatusIcon(task.status)}
                                    <span className={task.error_message ? 'text-red-600' : 'text-gray-900'}>
                                      {task.name}
                                    </span>
                                  </span>
                                  <span className={`text-xs px-2 py-0.5 rounded ${getStatusColor(task.status)}`}>
                                    {task.status}
                                  </span>
                                </div>

                                {task.started_at && (
                                  <div className="text-xs text-gray-500 mt-1">
                                    {task.completed_at ? (
                                      <>
                                        Completed in {((new Date(task.completed_at).getTime() - new Date(task.started_at).getTime()) / 1000).toFixed(2)}s
                                      </>
                                    ) : (
                                      <>Started {new Date(task.started_at).toLocaleTimeString()}</>
                                    )}
                                  </div>
                                )}

                                {task.error_message && (
                                  <div className="mt-2 p-2 bg-red-50 border border-red-200 rounded">
                                    <p className="text-xs text-red-700 font-medium">Error:</p>
                                    <p className="text-xs text-red-600 mt-1">{task.error_message}</p>
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        ) : null}

                        {/* Child Jobs (for parallel phases) */}
                        {jobStatus.is_parallel && jobStatus.child_jobs && phase.id.includes('parallel') && (
                          <div className="space-y-2">
                            <div className="flex items-center justify-between mb-2">
                              <p className="text-xs font-medium text-purple-700">
                                Parallel Batches: {jobStatus.child_jobs.filter(c => c.status === 'COMPLETED').length}/{jobStatus.child_jobs.length} complete
                              </p>
                              {jobStatus.parallel_progress?.running ? (
                                <span className="text-xs text-blue-600">{jobStatus.parallel_progress.running} running</span>
                              ) : null}
                            </div>
                            {/* Batch grid */}
                            <div className="flex flex-wrap gap-1">
                              {jobStatus.child_jobs.map((child) => (
                                <div
                                  key={child.job_id}
                                  title={`Batch ${child.item_id}: ${child.status}`}
                                  className={`w-8 h-6 rounded text-xs flex items-center justify-center font-medium cursor-pointer transition-transform hover:scale-110 ${
                                    child.status === 'COMPLETED' ? 'bg-green-500 text-white' :
                                    child.status === 'RUNNING' ? 'bg-blue-500 text-white animate-pulse' :
                                    child.status === 'FAILED' ? 'bg-red-500 text-white' :
                                    'bg-gray-200 text-gray-600'
                                  }`}
                                  onClick={(e) => { e.stopPropagation(); toggleChildJob(child.job_id); }}
                                >
                                  {child.item_id}
                                </div>
                              ))}
                            </div>
                            {/* Expanded child details */}
                            {jobStatus.child_jobs.filter(c => expandedChildJobs.has(c.job_id)).map((child) => (
                              <div key={child.job_id} className="mt-2 p-3 bg-purple-50 rounded border border-purple-200">
                                <div className="flex items-center justify-between mb-2">
                                  <span className="text-sm font-medium text-purple-900">Batch {child.item_id}</span>
                                  <span className={`text-xs px-2 py-0.5 rounded ${getStatusColor(child.status)}`}>
                                    {child.status}
                                  </span>
                                </div>
                                {child.errors && child.errors.length > 0 && (
                                  <div className="text-xs text-red-600 mt-1">
                                    {child.errors.map((err, i) => <p key={i}>{err}</p>)}
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        )}

                        {/* No details message - only show for pending/running phases without tasks and without per-unit stats */}
                        {(!phase.tasks || phase.tasks.length === 0) &&
                         !phase.result &&
                         !isPerUnit &&
                         !(jobStatus.is_parallel && phase.id.includes('parallel')) &&
                         phase.status !== 'COMPLETED' && phase.status !== 'SKIPPED' && (
                          <div className="p-3 bg-gray-50 rounded border border-gray-200 text-sm text-gray-500 italic">
                            {phase.status === 'RUNNING' ? 'Processing...' : 'Waiting to start...'}
                          </div>
                        )}
                        {/* Completed inline phase - show success (only for phases without per-unit stats) */}
                        {(!phase.tasks || phase.tasks.length === 0) &&
                         !phase.result &&
                         !isPerUnit &&
                         !(jobStatus.is_parallel && phase.id.includes('parallel')) &&
                         phase.status === 'COMPLETED' && (
                          <div className="p-3 bg-green-50 rounded border border-green-200 text-sm text-green-700">
                            Phase completed successfully
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* Live Events Panel */}
        <div className="lg:col-span-1">
          <div className="bg-white rounded-lg shadow h-fit sticky top-6">
            <div className="p-4 border-b border-gray-200">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-bold text-gray-900">Live Events</h2>
                <div className="flex items-center gap-2">
                  {/* SSE connection indicator */}
                  <span className={`flex items-center gap-1 text-xs px-2 py-0.5 rounded ${
                    sseStatus === 'connected' ? 'bg-green-100 text-green-700' :
                    sseStatus === 'connecting' ? 'bg-yellow-100 text-yellow-700' :
                    'bg-red-100 text-red-700'
                  }`}>
                    <span className={`w-2 h-2 rounded-full ${
                      sseStatus === 'connected' ? 'bg-green-500' :
                      sseStatus === 'connecting' ? 'bg-yellow-500 animate-pulse' :
                      'bg-red-500'
                    }`} />
                    {sseStatus === 'connected' ? 'Live' :
                     sseStatus === 'connecting' ? 'Connecting' : 'Disconnected'}
                  </span>
                  {/* Manual reconnect button */}
                  {sseStatus === 'disconnected' && jobStatus?.status !== 'COMPLETED' && jobStatus?.status !== 'FAILED' && jobStatus?.status !== 'CANCELLED' && (
                    <button
                      onClick={reconnectSSE}
                      className="text-xs px-2 py-0.5 bg-blue-100 text-blue-700 rounded hover:bg-blue-200 transition-colors"
                    >
                      Reconnect
                    </button>
                  )}
                </div>
              </div>
              {sseReconnects > 0 && sseStatus === 'connected' && (
                <p className="text-xs text-gray-400 mt-1">Reconnected {sseReconnects}x (polling as fallback)</p>
              )}
            </div>
            <div className="p-4">
              <div className="max-h-[400px] overflow-y-auto space-y-1">
                {liveEvents.length === 0 ? (
                  <p className="text-sm text-gray-500">Waiting for events...</p>
                ) : (
                  liveEvents.map((event, idx) => (
                    <div key={idx} className="text-xs font-mono text-gray-700 py-1">
                      {event}
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>

          {/* Errors */}
          {jobStatus.errors && jobStatus.errors.length > 0 && (
            <div className="mt-4 bg-red-50 border border-red-200 rounded-lg p-4">
              <h3 className="text-sm font-bold text-red-800 mb-2">Errors</h3>
              <div className="space-y-1">
                {jobStatus.errors.map((error, idx) => (
                  <p key={idx} className="text-xs text-red-600">{error}</p>
                ))}
              </div>
            </div>
          )}

          {/* Created Resources */}
          {Object.keys(jobStatus.created_resources).length > 0 && (
            <div className="mt-4 bg-green-50 border border-green-200 rounded-lg p-4">
              <h3 className="text-sm font-bold text-green-800 mb-2">Created Resources</h3>
              <div className="space-y-1">
                {Object.entries(jobStatus.created_resources).map(([type, resources]) => (
                  <p key={type} className="text-xs text-green-700">
                    {type}: {resources.length}
                  </p>
                ))}
              </div>
            </div>
          )}

          {/* Job Summary (shown for completed/failed jobs) */}
          {(jobStatus.status === 'COMPLETED' || jobStatus.status === 'FAILED' || jobStatus.status === 'PARTIAL' || jobStatus.status === 'CANCELLED') &&
           jobStatus.summary && Object.keys(jobStatus.summary).length > 0 && (
            <div className="mt-4 bg-blue-50 border border-blue-200 rounded-lg p-4">
              <h3 className="text-sm font-bold text-blue-800 mb-2">Job Summary</h3>
              <div className="space-y-2 text-xs">
                {/* Parallel job summary */}
                {jobStatus.is_parallel && (
                  <>
                    <div className="grid grid-cols-2 gap-2">
                      <div className="flex justify-between">
                        <span className="text-gray-600">Total Units:</span>
                        <span className="font-medium">{jobStatus.summary.total_items ?? '-'}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-600">Completed:</span>
                        <span className="font-medium text-green-600">{jobStatus.summary.completed ?? '-'}</span>
                      </div>
                      {(jobStatus.summary.failed ?? 0) > 0 && (
                        <div className="flex justify-between">
                          <span className="text-gray-600">Failed:</span>
                          <span className="font-medium text-red-600">{jobStatus.summary.failed}</span>
                        </div>
                      )}
                      {(jobStatus.summary.partial ?? 0) > 0 && (
                        <div className="flex justify-between">
                          <span className="text-gray-600">Partial:</span>
                          <span className="font-medium text-yellow-600">{jobStatus.summary.partial}</span>
                        </div>
                      )}
                    </div>
                    {/* Resources summary */}
                    {jobStatus.summary.resources && Object.keys(jobStatus.summary.resources).length > 0 && (
                      <div className="mt-2 pt-2 border-t border-blue-200">
                        <p className="text-blue-700 font-medium mb-1">Resources Created:</p>
                        <div className="grid grid-cols-2 gap-1">
                          {Object.entries(jobStatus.summary.resources).map(([type, count]) => (
                            <div key={type} className="flex justify-between text-blue-600">
                              <span>{type.replace(/_/g, ' ')}:</span>
                              <span className="font-medium">{String(count)}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                )}
                {/* Non-parallel job summary - show all summary fields */}
                {!jobStatus.is_parallel && (
                  <div className="space-y-1">
                    {Object.entries(jobStatus.summary).map(([key, value]) => {
                      // Skip complex objects
                      if (typeof value === 'object') return null;
                      return (
                        <div key={key} className="flex justify-between">
                          <span className="text-gray-600">{key.replace(/_/g, ' ')}:</span>
                          <span className="font-medium">{String(value)}</span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default JobMonitorView;
