import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import { apiGet } from '@/utils/api';
import type { FileFolder } from '@/types/fileshare';
import { formatBytes } from '@/types/fileshare';
import { Folder, Upload, Download, Settings, RefreshCw } from 'lucide-react';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

const FilesharePage = () => {
  const { userRole } = useAuth();
  const [folders, setFolders] = useState<FileFolder[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const isSuper = userRole === 'super';

  const fetchFolders = async () => {
    setLoading(true);
    setError('');
    try {
      const data = await apiGet<FileFolder[]>(`${API_BASE_URL}/fileshare/folders`);
      setFolders(data);
    } catch (err: any) {
      setError(err.message || 'Failed to load folders');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchFolders();
  }, []);

  const getQuotaPercentage = (folder: FileFolder) => {
    if (folder.quota_bytes === 0) return 0;
    return Math.round((folder.used_bytes / folder.quota_bytes) * 100);
  };

  const getQuotaColor = (percentage: number) => {
    if (percentage >= 90) return 'bg-red-500';
    if (percentage >= 70) return 'bg-yellow-500';
    return 'bg-blue-500';
  };

  return (
    <div className="p-4 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Fileshare</h1>
          <p className="text-gray-600 mt-1">Share files securely with your team</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={fetchFolders}
            className="px-4 py-2 text-gray-600 hover:text-gray-800 hover:bg-gray-100 rounded-lg flex items-center gap-2"
          >
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
          {isSuper && (
            <Link
              to="/fileshare/admin"
              className="px-4 py-2 bg-gray-800 text-white rounded-lg hover:bg-gray-700 flex items-center gap-2"
            >
              <Settings className="w-4 h-4" />
              Manage
            </Link>
          )}
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
          {error}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="text-center py-12 text-gray-500">
          <RefreshCw className="w-8 h-8 animate-spin mx-auto mb-2" />
          Loading folders...
        </div>
      )}

      {/* Empty State */}
      {!loading && folders.length === 0 && (
        <div className="text-center py-12 bg-white rounded-lg shadow">
          <Folder className="w-16 h-16 text-gray-300 mx-auto mb-4" />
          <h3 className="text-xl font-medium text-gray-700 mb-2">No folders available</h3>
          <p className="text-gray-500">
            {isSuper
              ? 'Create a folder to get started.'
              : 'Contact your administrator to get access to shared folders.'}
          </p>
        </div>
      )}

      {/* Folder Grid */}
      {!loading && folders.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {folders.map((folder) => (
            <Link
              key={folder.id}
              to={`/fileshare/${folder.slug}`}
              className="bg-white rounded-lg shadow hover:shadow-md transition-shadow p-5 block"
            >
              {/* Folder Header */}
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-blue-100 rounded-lg">
                    <Folder className="w-6 h-6 text-blue-600" />
                  </div>
                  <div>
                    <h3 className="font-semibold text-gray-900">{folder.name}</h3>
                    {folder.company_name && (
                      <span className="text-xs text-gray-500">{folder.company_name}</span>
                    )}
                  </div>
                </div>
              </div>

              {/* Description */}
              {folder.description && (
                <p className="text-sm text-gray-600 mb-3 line-clamp-2">{folder.description}</p>
              )}

              {/* Stats */}
              <div className="flex items-center gap-4 text-sm text-gray-500 mb-3">
                <span>{folder.subfolder_count} subfolders</span>
                <span>{folder.file_count} files</span>
              </div>

              {/* Quota Bar */}
              <div className="mb-3">
                <div className="flex justify-between text-xs text-gray-500 mb-1">
                  <span>{formatBytes(folder.used_bytes)} used</span>
                  <span>{formatBytes(folder.quota_bytes)} quota</span>
                </div>
                <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                  <div
                    className={`h-full ${getQuotaColor(getQuotaPercentage(folder))} transition-all`}
                    style={{ width: `${getQuotaPercentage(folder)}%` }}
                  />
                </div>
              </div>

              {/* Permissions */}
              <div className="flex gap-2">
                {folder.can_download && (
                  <span className="inline-flex items-center gap-1 px-2 py-1 bg-green-100 text-green-700 text-xs rounded">
                    <Download className="w-3 h-3" />
                    Download
                  </span>
                )}
                {folder.can_upload && (
                  <span className="inline-flex items-center gap-1 px-2 py-1 bg-blue-100 text-blue-700 text-xs rounded">
                    <Upload className="w-3 h-3" />
                    Upload
                  </span>
                )}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
};

export default FilesharePage;
