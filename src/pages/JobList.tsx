import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';

const API_URL = import.meta.env.VITE_API_BASE_URL || "/api";

interface JobListItem {
  job_id: string;
  workflow_name: string;
  status: string;
  created_at: string;
  completed_at: string | null;
  venue_id: string;
  controller_id: number;
  progress_percent: number;
}

interface JobListResponse {
  jobs: JobListItem[];
  total: number;
}

const JobList = () => {
  const navigate = useNavigate();
  const { userRole } = useAuth();

  const [jobs, setJobs] = useState<JobListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [selectedJobs, setSelectedJobs] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState(false);

  const isAdmin = userRole === 'admin' || userRole === 'super';

  useEffect(() => {
    fetchJobs();
  }, [statusFilter]);

  const fetchJobs = async () => {
    try {
      setLoading(true);
      setError(null);

      const params = new URLSearchParams();
      if (statusFilter) {
        params.append('status', statusFilter);
      }

      const url = `${API_URL}/jobs${params.toString() ? '?' + params.toString() : ''}`;
      const response = await fetch(url, {
        credentials: 'include',
      });

      if (!response.ok) {
        throw new Error('Failed to fetch jobs');
      }

      const data: JobListResponse = await response.json();
      setJobs(data.jobs);
    } catch (err: any) {
      console.error('Error fetching jobs:', err);
      setError(err.message || 'Failed to load jobs');
    } finally {
      setLoading(false);
    }
  };

  const getStatusColor = (status: string): string => {
    switch (status.toUpperCase()) {
      case 'COMPLETED':
        return 'bg-green-100 text-green-800 border-green-200';
      case 'RUNNING':
        return 'bg-blue-100 text-blue-800 border-blue-200';
      case 'FAILED':
        return 'bg-red-100 text-red-800 border-red-200';
      case 'PENDING':
        return 'bg-gray-100 text-gray-800 border-gray-200';
      case 'PARTIAL':
        return 'bg-yellow-100 text-yellow-800 border-yellow-200';
      default:
        return 'bg-gray-100 text-gray-800 border-gray-200';
    }
  };

  const getStatusIcon = (status: string): string => {
    switch (status.toUpperCase()) {
      case 'COMPLETED':
        return '✅';
      case 'RUNNING':
        return '▶️';
      case 'FAILED':
        return '❌';
      case 'PENDING':
        return '⏸️';
      case 'PARTIAL':
        return '⚠️';
      default:
        return '⚪';
    }
  };

  const formatDate = (dateStr: string | null): string => {
    if (!dateStr) return '-';

    try {
      const date = new Date(dateStr);
      const now = new Date();
      const diffMs = now.getTime() - date.getTime();
      const diffMins = Math.floor(diffMs / 60000);
      const diffHours = Math.floor(diffMs / 3600000);
      const diffDays = Math.floor(diffMs / 86400000);

      if (diffMins < 1) return 'Just now';
      if (diffMins < 60) return `${diffMins}m ago`;
      if (diffHours < 24) return `${diffHours}h ago`;
      if (diffDays < 7) return `${diffDays}d ago`;

      return date.toLocaleDateString();
    } catch {
      return dateStr;
    }
  };

  const formatDuration = (startStr: string, endStr: string | null): string => {
    if (!endStr) return '-';

    try {
      const start = new Date(startStr);
      const end = new Date(endStr);
      const diffMs = end.getTime() - start.getTime();
      const diffSecs = Math.floor(diffMs / 1000);
      const diffMins = Math.floor(diffSecs / 60);

      if (diffSecs < 60) return `${diffSecs}s`;
      if (diffMins < 60) return `${diffMins}m ${diffSecs % 60}s`;

      const hours = Math.floor(diffMins / 60);
      const mins = diffMins % 60;
      return `${hours}h ${mins}m`;
    } catch {
      return '-';
    }
  };

  const handleDeleteJobs = async () => {
    if (selectedJobs.size === 0) return;

    if (!confirm(`Delete ${selectedJobs.size} job${selectedJobs.size > 1 ? 's' : ''}? This cannot be undone.`)) {
      return;
    }

    try {
      setDeleting(true);
      const response = await fetch(`${API_URL}/jobs`, {
        method: 'DELETE',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          job_ids: Array.from(selectedJobs),
        }),
      });

      if (!response.ok) {
        throw new Error('Failed to delete jobs');
      }

      const result = await response.json();

      if (result.deleted.length > 0) {
        // Refresh the job list
        await fetchJobs();
        setSelectedJobs(new Set());
      }

      if (result.failed.length > 0) {
        console.error('Some jobs failed to delete:', result.failed);
        alert(`${result.deleted.length} job(s) deleted, ${result.failed.length} failed`);
      }
    } catch (err: any) {
      console.error('Error deleting jobs:', err);
      setError(err.message || 'Failed to delete jobs');
    } finally {
      setDeleting(false);
    }
  };

  const toggleJobSelection = (jobId: string) => {
    setSelectedJobs(prev => {
      const next = new Set(prev);
      if (next.has(jobId)) {
        next.delete(jobId);
      } else {
        next.add(jobId);
      }
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedJobs.size === jobs.length) {
      setSelectedJobs(new Set());
    } else {
      setSelectedJobs(new Set(jobs.map(j => j.job_id)));
    }
  };

  const getWorkflowDisplayName = (workflowName: string): string => {
    const nameMap: Record<string, string> = {
      'cloudpath_dpsk_migration': 'Cloudpath DPSK Migration',
    };
    return nameMap[workflowName] || workflowName;
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto mb-4"></div>
          <p className="text-gray-600">Loading jobs...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto p-6">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Workflow Jobs</h1>
        <p className="text-gray-600">Monitor and manage your workflow executions</p>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-lg shadow p-4 mb-6">
        <div className="flex items-center gap-4">
          <label className="text-sm font-medium text-gray-700">Filter by status:</label>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="">All Statuses</option>
            <option value="RUNNING">Running</option>
            <option value="COMPLETED">Completed</option>
            <option value="FAILED">Failed</option>
            <option value="PARTIAL">Partial</option>
            <option value="PENDING">Pending</option>
          </select>

          <div className="ml-auto flex gap-2">
            {isAdmin && selectedJobs.size > 0 && (
              <button
                onClick={handleDeleteJobs}
                disabled={deleting}
                className="px-4 py-2 bg-red-600 text-white rounded-md hover:bg-red-700 transition-colors disabled:bg-gray-400"
              >
                {deleting ? 'Deleting...' : `🗑️ Delete ${selectedJobs.size} job${selectedJobs.size > 1 ? 's' : ''}`}
              </button>
            )}
            <button
              onClick={fetchJobs}
              className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition-colors"
            >
              🔄 Refresh
            </button>
          </div>
        </div>
      </div>

      {/* Error State */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6">
          <p className="text-red-800">{error}</p>
        </div>
      )}

      {/* Jobs Table */}
      {jobs.length === 0 ? (
        <div className="bg-white rounded-lg shadow p-12 text-center">
          <p className="text-gray-500 text-lg mb-2">No jobs found</p>
          <p className="text-gray-400 text-sm">
            {statusFilter
              ? `No jobs with status "${statusFilter}"`
              : 'Start a workflow to see it here'}
          </p>
        </div>
      ) : (
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                {isAdmin && (
                  <th className="px-4 py-3 text-left">
                    <input
                      type="checkbox"
                      checked={jobs.length > 0 && selectedJobs.size === jobs.length}
                      onChange={toggleSelectAll}
                      className="w-4 h-4 text-blue-600 rounded focus:ring-blue-500"
                    />
                  </th>
                )}
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Workflow
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Status
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Progress
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Created
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Duration
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Venue ID
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {jobs.map((job) => (
                <tr
                  key={job.job_id}
                  className="hover:bg-gray-50 transition-colors"
                >
                  {isAdmin && (
                    <td className="px-4 py-4 whitespace-nowrap">
                      <input
                        type="checkbox"
                        checked={selectedJobs.has(job.job_id)}
                        onChange={(e) => {
                          e.stopPropagation();
                          toggleJobSelection(job.job_id);
                        }}
                        className="w-4 h-4 text-blue-600 rounded focus:ring-blue-500"
                      />
                    </td>
                  )}
                  <td
                    className="px-6 py-4 whitespace-nowrap cursor-pointer"
                    onClick={() => navigate(`/jobs/${job.job_id}`)}
                  >
                    <div className="text-sm font-medium text-gray-900">
                      {getWorkflowDisplayName(job.workflow_name)}
                    </div>
                    <div className="text-xs text-gray-500 font-mono">
                      {job.job_id.substring(0, 8)}...
                    </div>
                  </td>
                  <td
                    className="px-6 py-4 whitespace-nowrap cursor-pointer"
                    onClick={() => navigate(`/jobs/${job.job_id}`)}
                  >
                    <span
                      className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium border ${getStatusColor(
                        job.status
                      )}`}
                    >
                      {getStatusIcon(job.status)} {job.status}
                    </span>
                  </td>
                  <td
                    className="px-6 py-4 whitespace-nowrap cursor-pointer"
                    onClick={() => navigate(`/jobs/${job.job_id}`)}
                  >
                    <div className="flex items-center gap-2">
                      <div className="flex-1 bg-gray-200 rounded-full h-2 w-24">
                        <div
                          className={`h-2 rounded-full transition-all ${
                            job.status === 'COMPLETED'
                              ? 'bg-green-600'
                              : job.status === 'FAILED'
                              ? 'bg-red-600'
                              : 'bg-blue-600'
                          }`}
                          style={{ width: `${job.progress_percent}%` }}
                        />
                      </div>
                      <span className="text-xs text-gray-600 font-medium min-w-[3rem] text-right">
                        {job.progress_percent}%
                      </span>
                    </div>
                  </td>
                  <td
                    className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 cursor-pointer"
                    onClick={() => navigate(`/jobs/${job.job_id}`)}
                  >
                    {formatDate(job.created_at)}
                  </td>
                  <td
                    className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 cursor-pointer"
                    onClick={() => navigate(`/jobs/${job.job_id}`)}
                  >
                    {formatDuration(job.created_at, job.completed_at)}
                  </td>
                  <td
                    className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 font-mono cursor-pointer"
                    onClick={() => navigate(`/jobs/${job.job_id}`)}
                  >
                    {job.venue_id.substring(0, 12)}...
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        navigate(`/jobs/${job.job_id}`);
                      }}
                      className="text-blue-600 hover:text-blue-900"
                    >
                      View Details →
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Summary */}
      {jobs.length > 0 && (
        <div className="mt-4 text-sm text-gray-500 text-center">
          Showing {jobs.length} job{jobs.length !== 1 ? 's' : ''}
        </div>
      )}
    </div>
  );
};

export default JobList;
