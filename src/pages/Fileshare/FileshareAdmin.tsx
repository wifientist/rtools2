import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { apiGet, apiPost, apiPatch, apiDelete } from '@/utils/api';
import type {
  FileFolder,
  FolderPermission,
  AuditLogEntry
} from '@/types/fileshare';
import { formatBytes, formatDate } from '@/types/fileshare';
import {
  ArrowLeft,
  Folder,
  Plus,
  Trash2,
  RefreshCw,
  Users,
  Shield,
  FileText,
  Upload,
  Download,
  X,
  Edit2
} from 'lucide-react';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

interface Company {
  id: number;
  name: string;
}

interface User {
  id: number;
  email: string;
}

const FileshareAdmin = () => {
  // Tab state
  const [activeTab, setActiveTab] = useState<'folders' | 'audit'>('folders');

  // Folders state
  const [folders, setFolders] = useState<FileFolder[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Create folder modal
  const [showCreateFolder, setShowCreateFolder] = useState(false);
  const [newFolder, setNewFolder] = useState({ name: '', slug: '', description: '', company_id: '' });
  const [creating, setCreating] = useState(false);

  // Edit folder modal
  const [editingFolder, setEditingFolder] = useState<FileFolder | null>(null);
  const [editFolder, setEditFolder] = useState({ name: '', description: '', company_id: '' });
  const [saving, setSaving] = useState(false);

  // Create subfolder modal
  const [showCreateSubfolder, setShowCreateSubfolder] = useState<number | null>(null);
  const [newSubfolder, setNewSubfolder] = useState({ name: '', slug: '' });

  // Permissions modal
  const [showPermissions, setShowPermissions] = useState<FileFolder | null>(null);
  const [permissions, setPermissions] = useState<FolderPermission[]>([]);
  const [newPerm, setNewPerm] = useState({ user_id: '', permission_type: 'upload' });

  // Audit logs
  const [auditLogs, setAuditLogs] = useState<AuditLogEntry[]>([]);
  const [auditLoading, setAuditLoading] = useState(false);

  // Fetch data
  const fetchData = async () => {
    setLoading(true);
    setError('');
    try {
      const [foldersData, companiesData, usersData] = await Promise.all([
        apiGet<FileFolder[]>(`${API_BASE_URL}/fileshare/folders`),
        apiGet<Company[]>(`${API_BASE_URL}/admin/companies`),
        apiGet<User[]>(`${API_BASE_URL}/users/`)
      ]);
      setFolders(foldersData);
      setCompanies(companiesData);
      setUsers(usersData);
    } catch (err: any) {
      setError(err.message || 'Failed to load data');
    } finally {
      setLoading(false);
    }
  };

  const fetchAuditLogs = async () => {
    setAuditLoading(true);
    try {
      const logs = await apiGet<AuditLogEntry[]>(`${API_BASE_URL}/fileshare/audit-logs?limit=100`);
      setAuditLogs(logs);
    } catch (err: any) {
      setError(err.message || 'Failed to load audit logs');
    } finally {
      setAuditLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  useEffect(() => {
    if (activeTab === 'audit') {
      fetchAuditLogs();
    }
  }, [activeTab]);

  // Create folder
  const handleCreateFolder = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    try {
      await apiPost(`${API_BASE_URL}/fileshare/folders`, {
        name: newFolder.name,
        slug: newFolder.slug,
        description: newFolder.description || null,
        company_id: newFolder.company_id ? parseInt(newFolder.company_id) : null
      });
      setShowCreateFolder(false);
      setNewFolder({ name: '', slug: '', description: '', company_id: '' });
      await fetchData();
    } catch (err: any) {
      setError(err.message || 'Failed to create folder');
    } finally {
      setCreating(false);
    }
  };

  // Delete folder
  const handleDeleteFolder = async (folder: FileFolder) => {
    if (!confirm(`Delete folder "${folder.name}"? This cannot be undone.`)) return;
    try {
      await apiDelete(`${API_BASE_URL}/fileshare/folders/${folder.id}`);
      await fetchData();
    } catch (err: any) {
      setError(err.message || 'Failed to delete folder');
    }
  };

  // Open edit folder modal
  const openEditFolder = (folder: FileFolder) => {
    setEditingFolder(folder);
    setEditFolder({
      name: folder.name,
      description: folder.description || '',
      company_id: folder.company_id?.toString() || ''
    });
  };

  // Update folder
  const handleUpdateFolder = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingFolder) return;
    setSaving(true);
    try {
      await apiPatch(`${API_BASE_URL}/fileshare/folders/${editingFolder.id}`, {
        name: editFolder.name,
        description: editFolder.description || null,
        company_id: editFolder.company_id ? parseInt(editFolder.company_id) : null
      });
      setEditingFolder(null);
      await fetchData();
    } catch (err: any) {
      setError(err.message || 'Failed to update folder');
    } finally {
      setSaving(false);
    }
  };

  // Create subfolder
  const handleCreateSubfolder = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!showCreateSubfolder) return;
    setCreating(true);
    try {
      await apiPost(`${API_BASE_URL}/fileshare/folders/${showCreateSubfolder}/subfolders`, {
        name: newSubfolder.name,
        slug: newSubfolder.slug
      });
      setShowCreateSubfolder(null);
      setNewSubfolder({ name: '', slug: '' });
      await fetchData();
    } catch (err: any) {
      setError(err.message || 'Failed to create subfolder');
    } finally {
      setCreating(false);
    }
  };

  // Fetch permissions
  const openPermissions = async (folder: FileFolder) => {
    setShowPermissions(folder);
    try {
      const perms = await apiGet<FolderPermission[]>(`${API_BASE_URL}/fileshare/folders/${folder.id}/permissions`);
      setPermissions(perms);
    } catch (err: any) {
      setError(err.message || 'Failed to load permissions');
    }
  };

  // Grant permission
  const handleGrantPermission = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!showPermissions) return;
    try {
      await apiPost(`${API_BASE_URL}/fileshare/folders/${showPermissions.id}/permissions`, {
        user_id: parseInt(newPerm.user_id),
        permission_type: newPerm.permission_type
      });
      const perms = await apiGet<FolderPermission[]>(`${API_BASE_URL}/fileshare/folders/${showPermissions.id}/permissions`);
      setPermissions(perms);
      setNewPerm({ user_id: '', permission_type: 'upload' });
    } catch (err: any) {
      setError(err.message || 'Failed to grant permission');
    }
  };

  // Revoke permission
  const handleRevokePermission = async (permId: number) => {
    if (!confirm('Revoke this permission?')) return;
    try {
      await apiDelete(`${API_BASE_URL}/fileshare/permissions/${permId}`);
      setPermissions(permissions.filter(p => p.id !== permId));
    } catch (err: any) {
      setError(err.message || 'Failed to revoke permission');
    }
  };

  // Auto-generate slug from name
  const generateSlug = (name: string) => {
    return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
  };

  return (
    <div className="p-4 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-4 mb-6">
        <Link to="/fileshare" className="p-2 hover:bg-gray-100 rounded-lg">
          <ArrowLeft className="w-5 h-5" />
        </Link>
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Fileshare Admin</h1>
          <p className="text-gray-600">Manage folders, permissions, and view audit logs</p>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700 flex justify-between">
          {error}
          <button onClick={() => setError('')}><X className="w-4 h-4" /></button>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-2 mb-6 border-b">
        <button
          onClick={() => setActiveTab('folders')}
          className={`px-4 py-2 -mb-px border-b-2 transition-colors ${
            activeTab === 'folders'
              ? 'border-blue-600 text-blue-600'
              : 'border-transparent text-gray-600 hover:text-gray-800'
          }`}
        >
          <Folder className="w-4 h-4 inline mr-2" />
          Folders & Permissions
        </button>
        <button
          onClick={() => setActiveTab('audit')}
          className={`px-4 py-2 -mb-px border-b-2 transition-colors ${
            activeTab === 'audit'
              ? 'border-blue-600 text-blue-600'
              : 'border-transparent text-gray-600 hover:text-gray-800'
          }`}
        >
          <FileText className="w-4 h-4 inline mr-2" />
          Audit Logs
        </button>
      </div>

      {/* Folders Tab */}
      {activeTab === 'folders' && (
        <div>
          {/* Actions */}
          <div className="flex justify-between mb-4">
            <button
              onClick={() => setShowCreateFolder(true)}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 flex items-center gap-2"
            >
              <Plus className="w-4 h-4" />
              Create Folder
            </button>
            <button
              onClick={fetchData}
              className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg flex items-center gap-2"
            >
              <RefreshCw className="w-4 h-4" />
              Refresh
            </button>
          </div>

          {/* Loading */}
          {loading && (
            <div className="text-center py-12 text-gray-500">
              <RefreshCw className="w-8 h-8 animate-spin mx-auto mb-2" />
              Loading...
            </div>
          )}

          {/* Folders List */}
          {!loading && (
            <div className="space-y-4">
              {folders.map((folder) => (
                <div key={folder.id} className="bg-white rounded-lg shadow p-4">
                  <div className="flex justify-between items-start">
                    <div className="flex items-center gap-3">
                      <div className="p-2 bg-blue-100 rounded-lg">
                        <Folder className="w-6 h-6 text-blue-600" />
                      </div>
                      <div>
                        <h3 className="font-semibold text-gray-900">{folder.name}</h3>
                        <div className="text-sm text-gray-500">
                          /{folder.slug}
                          <span className={`ml-2 px-2 py-0.5 rounded text-xs ${folder.company_name ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500'}`}>
                            {folder.company_name || 'No company'}
                          </span>
                        </div>
                      </div>
                    </div>
                    <div className="flex gap-2">
                      <button
                        onClick={() => setShowCreateSubfolder(folder.id)}
                        className="px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded flex items-center gap-1"
                      >
                        <Plus className="w-4 h-4" />
                        Subfolder
                      </button>
                      <button
                        onClick={() => openEditFolder(folder)}
                        className="px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded flex items-center gap-1"
                      >
                        <Edit2 className="w-4 h-4" />
                        Edit
                      </button>
                      <button
                        onClick={() => openPermissions(folder)}
                        className="px-3 py-1.5 text-sm text-blue-600 hover:bg-blue-50 rounded flex items-center gap-1"
                      >
                        <Shield className="w-4 h-4" />
                        Permissions
                      </button>
                      <button
                        onClick={() => handleDeleteFolder(folder)}
                        className="px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 rounded flex items-center gap-1"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>

                  {/* Stats */}
                  <div className="mt-3 flex items-center gap-6 text-sm text-gray-500">
                    <span>{folder.subfolder_count} subfolders</span>
                    <span>{folder.file_count} files</span>
                    <span>{formatBytes(folder.used_bytes)} / {formatBytes(folder.quota_bytes)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Audit Tab */}
      {activeTab === 'audit' && (
        <div>
          <div className="flex justify-end mb-4">
            <button
              onClick={fetchAuditLogs}
              disabled={auditLoading}
              className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg flex items-center gap-2"
            >
              <RefreshCw className={`w-4 h-4 ${auditLoading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </div>

          <div className="bg-white rounded-lg shadow overflow-hidden">
            <table className="w-full">
              <thead className="bg-gray-50 border-b">
                <tr>
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-600">Time</th>
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-600">User</th>
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-600">Action</th>
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-600">File</th>
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-600">Folder</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {auditLogs.map((log) => (
                  <tr key={log.id} className="hover:bg-gray-50">
                    <td className="px-4 py-3 text-sm text-gray-600">
                      {formatDate(log.created_at)}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-900">
                      {log.user_email}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-medium ${
                        log.action === 'upload' ? 'bg-green-100 text-green-700' :
                        log.action === 'download' ? 'bg-blue-100 text-blue-700' :
                        log.action === 'delete' ? 'bg-red-100 text-red-700' :
                        'bg-gray-100 text-gray-700'
                      }`}>
                        {log.action === 'upload' && <Upload className="w-3 h-3" />}
                        {log.action === 'download' && <Download className="w-3 h-3" />}
                        {log.action === 'delete' && <Trash2 className="w-3 h-3" />}
                        {log.action}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-900">
                      {log.filename}
                      {log.file_size_bytes && (
                        <span className="text-gray-500 ml-1">({formatBytes(log.file_size_bytes)})</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-600">
                      /{log.folder_slug}
                      {log.subfolder_slug && `/${log.subfolder_slug}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Create Folder Modal */}
      {showCreateFolder && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full">
            <div className="px-6 py-4 border-b flex justify-between items-center">
              <h3 className="text-xl font-semibold">Create Folder</h3>
              <button onClick={() => setShowCreateFolder(false)}>
                <X className="w-5 h-5 text-gray-500" />
              </button>
            </div>
            <form onSubmit={handleCreateFolder} className="p-6 space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
                <input
                  type="text"
                  value={newFolder.name}
                  onChange={(e) => {
                    setNewFolder({ ...newFolder, name: e.target.value, slug: generateSlug(e.target.value) });
                  }}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                  required
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Slug</label>
                <input
                  type="text"
                  value={newFolder.slug}
                  onChange={(e) => setNewFolder({ ...newFolder, slug: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                  pattern="[a-z0-9-]+"
                  required
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
                <textarea
                  value={newFolder.description}
                  onChange={(e) => setNewFolder({ ...newFolder, description: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                  rows={2}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Company (auto-grants download access)
                </label>
                <select
                  value={newFolder.company_id}
                  onChange={(e) => setNewFolder({ ...newFolder, company_id: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                >
                  <option value="">None (explicit permissions only)</option>
                  {companies.map((c) => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
              </div>
              <div className="flex justify-end gap-2 pt-4">
                <button
                  type="button"
                  onClick={() => setShowCreateFolder(false)}
                  className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={creating}
                  className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
                >
                  {creating ? 'Creating...' : 'Create'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Edit Folder Modal */}
      {editingFolder && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full">
            <div className="px-6 py-4 border-b flex justify-between items-center">
              <h3 className="text-xl font-semibold">Edit Folder</h3>
              <button onClick={() => setEditingFolder(null)}>
                <X className="w-5 h-5 text-gray-500" />
              </button>
            </div>
            <form onSubmit={handleUpdateFolder} className="p-6 space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
                <input
                  type="text"
                  value={editFolder.name}
                  onChange={(e) => setEditFolder({ ...editFolder, name: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                  required
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Slug (read-only)</label>
                <input
                  type="text"
                  value={editingFolder.slug}
                  disabled
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg bg-gray-50 text-gray-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
                <textarea
                  value={editFolder.description}
                  onChange={(e) => setEditFolder({ ...editFolder, description: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                  rows={2}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Company (auto-grants download access)
                </label>
                <select
                  value={editFolder.company_id}
                  onChange={(e) => setEditFolder({ ...editFolder, company_id: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                >
                  <option value="">None (explicit permissions only)</option>
                  {companies.map((c) => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
              </div>
              <div className="flex justify-end gap-2 pt-4">
                <button
                  type="button"
                  onClick={() => setEditingFolder(null)}
                  className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={saving}
                  className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
                >
                  {saving ? 'Saving...' : 'Save Changes'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Create Subfolder Modal */}
      {showCreateSubfolder && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full">
            <div className="px-6 py-4 border-b flex justify-between items-center">
              <h3 className="text-xl font-semibold">Create Subfolder</h3>
              <button onClick={() => setShowCreateSubfolder(null)}>
                <X className="w-5 h-5 text-gray-500" />
              </button>
            </div>
            <form onSubmit={handleCreateSubfolder} className="p-6 space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
                <input
                  type="text"
                  value={newSubfolder.name}
                  onChange={(e) => {
                    setNewSubfolder({ ...newSubfolder, name: e.target.value, slug: generateSlug(e.target.value) });
                  }}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                  required
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Slug</label>
                <input
                  type="text"
                  value={newSubfolder.slug}
                  onChange={(e) => setNewSubfolder({ ...newSubfolder, slug: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                  pattern="[a-z0-9-]+"
                  required
                />
              </div>
              <div className="flex justify-end gap-2 pt-4">
                <button
                  type="button"
                  onClick={() => setShowCreateSubfolder(null)}
                  className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={creating}
                  className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
                >
                  {creating ? 'Creating...' : 'Create'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Permissions Modal */}
      {showPermissions && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full max-h-[80vh] flex flex-col">
            <div className="px-6 py-4 border-b flex justify-between items-center">
              <h3 className="text-xl font-semibold">Permissions: {showPermissions.name}</h3>
              <button onClick={() => setShowPermissions(null)}>
                <X className="w-5 h-5 text-gray-500" />
              </button>
            </div>

            <div className="p-6 overflow-y-auto flex-1">
              {/* Company notice */}
              {showPermissions.company_name && (
                <div className="mb-4 p-3 bg-blue-50 border border-blue-200 rounded-lg text-sm text-blue-700">
                  <Users className="w-4 h-4 inline mr-2" />
                  All users in <strong>{showPermissions.company_name}</strong> have automatic download access.
                </div>
              )}

              {/* Grant permission form */}
              <form onSubmit={handleGrantPermission} className="mb-6 p-4 bg-gray-50 rounded-lg">
                <h4 className="font-medium mb-3">Grant Permission</h4>
                <div className="flex gap-3">
                  <select
                    value={newPerm.user_id}
                    onChange={(e) => setNewPerm({ ...newPerm, user_id: e.target.value })}
                    className="flex-1 px-3 py-2 border border-gray-300 rounded-lg"
                    required
                  >
                    <option value="">Select user...</option>
                    {users.map((u) => (
                      <option key={u.id} value={u.id}>{u.email}</option>
                    ))}
                  </select>
                  <select
                    value={newPerm.permission_type}
                    onChange={(e) => setNewPerm({ ...newPerm, permission_type: e.target.value })}
                    className="px-3 py-2 border border-gray-300 rounded-lg"
                  >
                    <option value="upload">Upload</option>
                    <option value="download">Download</option>
                  </select>
                  <button
                    type="submit"
                    className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
                  >
                    Grant
                  </button>
                </div>
              </form>

              {/* Existing permissions */}
              <h4 className="font-medium mb-3">Existing Permissions</h4>
              {permissions.length === 0 ? (
                <p className="text-gray-500 text-sm">No explicit permissions granted yet.</p>
              ) : (
                <div className="space-y-2">
                  {permissions.map((perm) => (
                    <div
                      key={perm.id}
                      className="flex justify-between items-center p-3 bg-white border rounded-lg"
                    >
                      <div>
                        <div className="font-medium">{perm.user_email}</div>
                        <div className="text-xs text-gray-500">
                          Granted by {perm.granted_by_email} on {formatDate(perm.granted_at)}
                        </div>
                      </div>
                      <div className="flex items-center gap-3">
                        <span className={`px-2 py-1 rounded text-xs font-medium ${
                          perm.permission_type === 'upload'
                            ? 'bg-green-100 text-green-700'
                            : 'bg-blue-100 text-blue-700'
                        }`}>
                          {perm.permission_type === 'upload' ? (
                            <><Upload className="w-3 h-3 inline mr-1" />Upload</>
                          ) : (
                            <><Download className="w-3 h-3 inline mr-1" />Download</>
                          )}
                        </span>
                        <button
                          onClick={() => handleRevokePermission(perm.id)}
                          className="p-1 text-red-600 hover:bg-red-50 rounded"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="px-6 py-4 border-t bg-gray-50">
              <button
                onClick={() => setShowPermissions(null)}
                className="px-4 py-2 text-gray-600 hover:bg-gray-200 rounded-lg"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default FileshareAdmin;
