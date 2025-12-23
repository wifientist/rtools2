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
  const [liveEvents, setLiveEvents] = useState<string[]>([]);

  const eventSourceRef = useRef<EventSource | null>(null);

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

    eventSource.addEventListener('connected', (e) => {
      console.log('SSE connected:', e.data);
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

    eventSource.addEventListener('task_started', (e) => {
      const data = JSON.parse(e.data);
      console.log('Task started:', data);
      addLiveEvent(`⏳ Starting: ${data.task_name || data.task_id}`);
    });

    eventSource.addEventListener('task_completed', (e) => {
      const data = JSON.parse(e.data);
      console.log('Task completed:', data);
      const status = data.status === 'FAILED' ? '❌' : '✓';
      addLiveEvent(`${status} Completed: ${data.task_name || data.task_id}`);
      refreshJobStatus();
    });

    eventSource.addEventListener('progress', (e) => {
      const data = JSON.parse(e.data);
      console.log('Progress:', data);
      // Show phase progress if available
      if (data.total_phases) {
        addLiveEvent(`📈 Progress: ${data.completed_phases}/${data.total_phases} phases complete`);
      } else {
        addLiveEvent(`📈 Progress: ${data.percent}% (${data.completed}/${data.total_tasks || data.total})`);
      }
      refreshJobStatus();
    });

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

    eventSource.addEventListener('job_completed', async (e) => {
      const data = JSON.parse(e.data);
      console.log('Job completed:', data);
      addLiveEvent(`🎉 Job completed!`);
      await refreshJobStatus();
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
          errors: data.errors,
        });
      }
      eventSource.close();
    });

    eventSource.addEventListener('job_failed', async (e) => {
      const data = JSON.parse(e.data);
      console.log('Job failed:', data);
      addLiveEvent(`❌ Job failed: ${data.error || 'Unknown error'}`);
      await refreshJobStatus();
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

    eventSource.onerror = (err) => {
      console.error('SSE error:', err);

      if (jobStatus?.status === 'COMPLETED' || jobStatus?.status === 'FAILED' || jobStatus?.status === 'PARTIAL') {
        console.log('Job in terminal state, closing SSE connection');
        addLiveEvent('✓ Job finished, closing live stream');
        eventSource.close();
      } else {
        addLiveEvent('⚠️ Stream connection lost, reconnecting...');
      }
    };

    return () => {
      eventSource.close();
    };
  }, [jobId]);

  const refreshJobStatus = async () => {
    if (!jobId) return;

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

  const addLiveEvent = (message: string) => {
    const timestamp = new Date().toLocaleTimeString();
    setLiveEvents(prev => [`[${timestamp}] ${message}`, ...prev].slice(0, 50));
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
      case 'FAILED':
        return 'text-red-600 bg-red-50 border-red-200';
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
          </div>
          <div>
            <p className="text-sm text-gray-500">Phases</p>
            <p className="text-lg font-bold">
              {jobStatus.progress.completed_phases ?? 0}/{jobStatus.progress.total_phases ?? jobStatus.phases.length}
              {jobStatus.progress.running_phases ? (
                <span className="text-blue-600 text-sm font-normal ml-1">(1 running)</span>
              ) : null}
            </p>
          </div>
          <div>
            <p className="text-sm text-gray-500">Tasks</p>
            <p className="text-sm">
              {jobStatus.progress.completed}/{jobStatus.progress.total_tasks} completed
              {jobStatus.progress.failed > 0 && (
                <span className="text-red-600 ml-2">({jobStatus.progress.failed} failed)</span>
              )}
            </p>
          </div>
        </div>

        {/* Progress Bar - uses phase percent for stable visual feedback */}
        <div className="mt-4">
          <div className="w-full bg-gray-200 rounded-full h-3 overflow-hidden">
            <div
              className="bg-blue-600 h-full transition-all duration-500"
              style={{ width: `${jobStatus.progress.phase_percent ?? jobStatus.progress.percent}%` }}
            />
          </div>
        </div>

        {/* Current Phase */}
        {jobStatus.current_phase && (
          <div className="mt-4 p-4 bg-blue-50 border border-blue-200 rounded">
            <p className="text-sm text-blue-800 font-medium">Currently Running:</p>
            <p className="text-blue-900">{jobStatus.current_phase.name}</p>
            <p className="text-sm text-blue-600 mt-1">
              {jobStatus.current_phase.tasks_completed}/{jobStatus.current_phase.tasks_total} tasks completed
            </p>
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
        {/* Phases Panel */}
        <div className="lg:col-span-2">
          <div className="bg-white rounded-lg shadow">
            <div className="p-4 border-b border-gray-200">
              <h2 className="text-xl font-bold text-gray-900">Phases</h2>
            </div>
            <div className="divide-y divide-gray-200 max-h-[600px] overflow-y-auto">
              {jobStatus.phases.map((phase) => (
                <div key={phase.id} className="p-4">
                  <div
                    className="flex items-center justify-between cursor-pointer"
                    onClick={() => togglePhase(phase.id)}
                  >
                    <div className="flex items-center gap-3">
                      <span className="text-xl">{getStatusIcon(phase.status)}</span>
                      <div>
                        <h3 className="font-medium text-gray-900">{phase.name}</h3>
                        <span className={`inline-block px-2 py-0.5 text-xs rounded ${getStatusColor(phase.status)}`}>
                          {phase.status}
                        </span>
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      {/* Per-phase progress indicator */}
                      {phase.tasks && phase.tasks.length > 0 && (
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

                      {/* Tasks List */}
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
                      ) : !phase.result ? (
                        <div className="p-3 bg-gray-50 rounded border border-gray-200 text-sm text-gray-500 italic">
                          No task details available for this phase
                        </div>
                      ) : null}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Live Events Panel */}
        <div className="lg:col-span-1">
          <div className="bg-white rounded-lg shadow h-fit sticky top-6">
            <div className="p-4 border-b border-gray-200">
              <h2 className="text-lg font-bold text-gray-900">Live Events</h2>
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
        </div>
      </div>
    </div>
  );
};

export default JobMonitorView;
