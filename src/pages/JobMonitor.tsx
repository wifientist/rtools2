import { useEffect, useState, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
//import { API_URL } from '@/config';

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
  // Parallel job progress
  total_items?: number;
  running?: number;
}

interface ChildJob {
  job_id: string;
  item_id: string;
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

const JobMonitor = () => {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();

  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedPhases, setExpandedPhases] = useState<Set<string>>(new Set());
  const [expandedChildJobs, setExpandedChildJobs] = useState<Set<string>>(new Set());
  const [liveEvents, setLiveEvents] = useState<string[]>([]);

  const eventSourceRef = useRef<EventSource | null>(null);

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

      // Refresh job status
      refreshJobStatus();
    });

    eventSource.addEventListener('phase_completed', (e) => {
      const data = JSON.parse(e.data);
      console.log('Phase completed:', data);
      addLiveEvent(`✅ Phase completed: ${data.phase_name}`);

      // Refresh job status
      refreshJobStatus();
    });

    eventSource.addEventListener('task_completed', (e) => {
      const data = JSON.parse(e.data);
      console.log('Task completed:', data);
      addLiveEvent(`✓ Task: ${data.task_name || data.task_id}`);

      // Refresh job status periodically
      refreshJobStatus();
    });

    eventSource.addEventListener('progress', (e) => {
      const data = JSON.parse(e.data);
      console.log('Progress:', data);
      addLiveEvent(`📈 Progress: ${data.percent}% (${data.completed}/${data.total})`);

      // Refresh job status
      refreshJobStatus();
    });

    eventSource.addEventListener('message', (e) => {
      const data = JSON.parse(e.data);
      console.log('Message:', data);
      const icon = data.level === 'error' ? '❌' :
                   data.level === 'warning' ? '⚠️' :
                   data.level === 'success' ? '✅' : 'ℹ️';
      addLiveEvent(`${icon} ${data.message}`);
    });

    eventSource.addEventListener('job_started', (e) => {
      const data = JSON.parse(e.data);
      console.log('Job started:', data);
      addLiveEvent(`🚀 Job started: ${data.workflow_name}`);
      refreshJobStatus();
    });

    eventSource.addEventListener('job_completed', (e) => {
      const data = JSON.parse(e.data);
      console.log('Job completed:', data);
      addLiveEvent(`🎉 Job completed!`);

      // Final refresh and close connection
      refreshJobStatus();
      eventSource.close();
    });

    eventSource.addEventListener('job_failed', (e) => {
      const data = JSON.parse(e.data);
      console.log('Job failed:', data);
      addLiveEvent(`❌ Job failed: ${data.error || 'Unknown error'}`);

      // Final refresh and close connection
      refreshJobStatus();
      eventSource.close();
    });

    eventSource.onerror = (err) => {
      console.error('SSE error:', err);

      // Check if job is in terminal state before reconnecting
      // EventSource automatically reconnects, so close it if job is done
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
    setLiveEvents(prev => [`[${timestamp}] ${message}`, ...prev].slice(0, 50)); // Keep last 50 events
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
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto mb-4"></div>
          <p className="text-gray-600">Loading job status...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-4xl mx-auto p-6">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6">
          <h2 className="text-xl font-bold text-red-800 mb-2">Error</h2>
          <p className="text-red-600">{error}</p>
          <button
            onClick={() => navigate(-1)}
            className="mt-4 px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700"
          >
            Go Back
          </button>
        </div>
      </div>
    );
  }

  if (!jobStatus) {
    return (
      <div className="max-w-4xl mx-auto p-6">
        <p className="text-gray-600">No job data available</p>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto p-6">
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-4">
          <h1 className="text-3xl font-bold text-gray-900">Job Monitor</h1>
          <div className="flex gap-2">
            <button
              onClick={() => navigate('/jobs')}
              className="px-4 py-2 text-blue-600 hover:text-blue-800 hover:bg-blue-50 rounded transition-colors"
            >
              ← All Jobs
            </button>
            <button
              onClick={() => navigate(-1)}
              className="px-4 py-2 text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded transition-colors"
            >
              Back
            </button>
          </div>
        </div>

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
                  </p>
                </div>
              </>
            ) : (
              <>
                <div>
                  <p className="text-sm text-gray-500">Progress</p>
                  <p className="text-lg font-bold">{jobStatus.progress.percent}%</p>
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
                style={{ width: `${jobStatus.parallel_progress?.percent ?? jobStatus.progress.percent}%` }}
              />
            </div>
          </div>

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

          {/* Parallel Job Live Summary */}
          {jobStatus.is_parallel && jobStatus.child_jobs && jobStatus.status === 'RUNNING' && (
            <div className="mt-4 p-4 bg-purple-50 border border-purple-200 rounded">
              <div className="flex items-center justify-between mb-2">
                <p className="text-sm text-purple-800 font-medium">Parallel Execution Progress</p>
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
              {/* Mini grid showing unit status tiles */}
              <div className="flex flex-wrap gap-1 mt-2">
                {jobStatus.child_jobs.map((child) => (
                  <div
                    key={child.job_id}
                    title={`Unit ${child.item_id}: ${child.status}${child.current_phase ? ` (${child.current_phase})` : ''}`}
                    className={`w-12 h-7 rounded text-xs flex items-center justify-center font-medium cursor-pointer transition-transform hover:scale-105 ${
                      child.status === 'COMPLETED' ? 'bg-green-500 text-white' :
                      child.status === 'RUNNING' ? 'bg-blue-500 text-white animate-pulse' :
                      child.status === 'FAILED' ? 'bg-red-500 text-white' :
                      'bg-gray-200 text-gray-600'
                    }`}
                    onClick={() => toggleChildJob(child.job_id)}
                  >
                    {child.item_id?.slice(-4) || '?'}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Main Panel - Child Jobs for parallel, Phases for sequential */}
        <div className="lg:col-span-2">
          {jobStatus.is_parallel && jobStatus.child_jobs ? (
            /* Child Jobs Panel for Parallel Execution */
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
                        {/* Progress indicator for child job */}
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

                    {/* Expanded Child Job Details */}
                    {expandedChildJobs.has(child.job_id) && (
                      <div className="mt-3 ml-8 space-y-3">
                        {/* Phase progress for this child */}
                        {child.progress && (child.progress.total_phases ?? 0) > 0 && (
                          <div className="p-3 bg-gray-50 rounded border border-gray-200">
                            <p className="text-xs font-medium text-gray-700 mb-2">Phase Progress:</p>
                            <div className="text-xs text-gray-600 space-y-1">
                              <div className="flex justify-between">
                                <span>Completed:</span>
                                <span className="font-medium text-green-600">{child.progress.completed_phases ?? 0}</span>
                              </div>
                              {(child.progress.running_phases ?? 0) > 0 && (
                                <div className="flex justify-between">
                                  <span>Running:</span>
                                  <span className="font-medium text-blue-600">{child.progress.running_phases}</span>
                                </div>
                              )}
                              {(child.progress.failed_phases ?? 0) > 0 && (
                                <div className="flex justify-between">
                                  <span>Failed:</span>
                                  <span className="font-medium text-red-600">{child.progress.failed_phases}</span>
                                </div>
                              )}
                              <div className="flex justify-between">
                                <span>Total:</span>
                                <span className="font-medium">{child.progress.total_phases ?? 0}</span>
                              </div>
                            </div>
                          </div>
                        )}

                        {/* Errors for this child */}
                        {child.errors && child.errors.length > 0 && (
                          <div className="p-3 bg-red-50 rounded border border-red-200">
                            <p className="text-xs font-medium text-red-800 mb-2">Errors:</p>
                            <div className="space-y-1">
                              {child.errors.map((error, idx) => (
                                <p key={idx} className="text-xs text-red-600">{error}</p>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Current phase info */}
                        {child.current_phase && child.status === 'RUNNING' && (
                          <div className="p-3 bg-blue-50 rounded border border-blue-200">
                            <p className="text-xs font-medium text-blue-800">Currently Running:</p>
                            <p className="text-sm text-blue-900 mt-1">{child.current_phase}</p>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ) : (
            /* Phases Panel for Sequential Execution */
            <div className="bg-white rounded-lg shadow">
              <div className="p-4 border-b border-gray-200">
                <h2 className="text-xl font-bold text-gray-900">Phases</h2>
              </div>
              <div className="divide-y divide-gray-200">
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
                    <div className="flex items-center gap-2">
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

                              {/* Task timing */}
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
          )}
        </div>

        {/* Live Events Panel */}
        <div className="lg:col-span-1">
          <div className="bg-white rounded-lg shadow h-fit sticky top-6">
            <div className="p-4 border-b border-gray-200">
              <h2 className="text-lg font-bold text-gray-900">Live Events</h2>
            </div>
            <div className="p-4">
              <div className="max-h-[600px] overflow-y-auto space-y-1">
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

export default JobMonitor;
