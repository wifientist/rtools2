import { useState, useEffect, useRef, useMemo } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import { apiGet, apiPost, apiDelete, apiFetch } from '@/utils/api';
import type {
  FileFolder,
  FileSubfolder,
  SharedFile,
  UploadInitiateResponse
} from '@/types/fileshare';
import { formatBytes, formatDate, getFileIcon } from '@/types/fileshare';
import {
  ArrowLeft,
  Folder,
  FolderOpen,
  Upload,
  Download,
  Trash2,
  RefreshCw,
  File,
  Clock,
  User,
  ChevronRight,
  ChevronDown,
  Flag,
  AlertTriangle,
  X,
  FileWarning
} from 'lucide-react';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

const PART_SIZE = 50 * 1024 * 1024; // 50MB - must match backend

const FolderView = () => {
  const { folderSlug, subfolderSlug } = useParams<{ folderSlug: string; subfolderSlug?: string }>();
  const { userRole } = useAuth();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [folder, setFolder] = useState<FileFolder | null>(null);
  const [subfolders, setSubfolders] = useState<FileSubfolder[]>([]);
  const [files, setFiles] = useState<SharedFile[]>([]);
  const [currentSubfolder, setCurrentSubfolder] = useState<FileSubfolder | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Expanded subfolders state (all expanded by default)
  const [expandedSubfolders, setExpandedSubfolders] = useState<Set<number>>(new Set());

  // Upload state
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadFileName, setUploadFileName] = useState('');

  // Terms acceptance state
  const [showTermsModal, setShowTermsModal] = useState(false);
  const [termsText, setTermsText] = useState('');
  const [termsAccepted, setTermsAccepted] = useState(() => {
    return localStorage.getItem('fileshare_terms_accepted') === 'true';
  });
  const [pendingFile, setPendingFile] = useState<File | null>(null);

  // Report modal state
  const [showReportModal, setShowReportModal] = useState<SharedFile | null>(null);
  const [reportReason, setReportReason] = useState('');
  const [reportSubmitting, setReportSubmitting] = useState(false);

  const isSuper = userRole === 'super';

  // Toggle subfolder expansion
  const toggleSubfolder = (subfolderId: number) => {
    setExpandedSubfolders(prev => {
      const next = new Set(prev);
      if (next.has(subfolderId)) {
        next.delete(subfolderId);
      } else {
        next.add(subfolderId);
      }
      return next;
    });
  };

  // Group files by subfolder for tree view
  const { rootFiles, filesBySubfolder } = useMemo(() => {
    const rootFiles: SharedFile[] = [];
    const filesBySubfolder: Map<number, SharedFile[]> = new Map();

    for (const file of files) {
      if (file.subfolder_id === null) {
        rootFiles.push(file);
      } else {
        const existing = filesBySubfolder.get(file.subfolder_id) || [];
        existing.push(file);
        filesBySubfolder.set(file.subfolder_id, existing);
      }
    }

    return { rootFiles, filesBySubfolder };
  }, [files]);

  // Fetch folder data
  const fetchData = async () => {
    if (!folderSlug) return;
    setLoading(true);
    setError('');

    try {
      // Get all folders to find this one
      const folders = await apiGet<FileFolder[]>(`${API_BASE_URL}/fileshare/folders`);
      const currentFolder = folders.find(f => f.slug === folderSlug);
      if (!currentFolder) {
        setError('Folder not found');
        setLoading(false);
        return;
      }
      setFolder(currentFolder);

      // Get subfolders
      const subs = await apiGet<FileSubfolder[]>(`${API_BASE_URL}/fileshare/folders/${currentFolder.id}/subfolders`);
      setSubfolders(subs);
      // Expand all subfolders by default
      setExpandedSubfolders(new Set(subs.map(s => s.id)));

      // Find current subfolder if in subfolder view
      let subfolderId: number | undefined;
      if (subfolderSlug) {
        const sub = subs.find(s => s.slug === subfolderSlug);
        if (sub) {
          setCurrentSubfolder(sub);
          subfolderId = sub.id;
        }
      } else {
        setCurrentSubfolder(null);
      }

      // Get files
      const filesUrl = subfolderId
        ? `${API_BASE_URL}/fileshare/folders/${currentFolder.id}/files?subfolder_id=${subfolderId}`
        : `${API_BASE_URL}/fileshare/folders/${currentFolder.id}/files`;
      const fileList = await apiGet<SharedFile[]>(filesUrl);
      setFiles(fileList);
    } catch (err: any) {
      setError(err.message || 'Failed to load folder');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, [folderSlug, subfolderSlug]);

  // Fetch terms of service
  const fetchTerms = async () => {
    try {
      const data = await apiGet<{ terms: string }>(`${API_BASE_URL}/fileshare/terms`);
      setTermsText(data.terms);
    } catch (err) {
      setTermsText('Unable to load terms. Please try again.');
    }
  };

  // Accept terms
  const handleAcceptTerms = async () => {
    try {
      await apiPost(`${API_BASE_URL}/fileshare/terms/accept`, { accepted: true });
      localStorage.setItem('fileshare_terms_accepted', 'true');
      setTermsAccepted(true);
      setShowTermsModal(false);
      // Process pending file upload
      if (pendingFile) {
        processUpload(pendingFile);
        setPendingFile(null);
      }
    } catch (err: any) {
      setError(err.message || 'Failed to accept terms');
    }
  };

  // Report a file
  const handleReportFile = async () => {
    if (!showReportModal || reportReason.length < 10) return;
    setReportSubmitting(true);
    try {
      await apiPost(`${API_BASE_URL}/fileshare/report`, {
        file_id: showReportModal.id,
        reason: reportReason
      });
      setShowReportModal(null);
      setReportReason('');
      alert('File reported successfully. Administrators have been notified.');
    } catch (err: any) {
      setError(err.message || 'Failed to report file');
    } finally {
      setReportSubmitting(false);
    }
  };

  // Handle file upload - check terms first
  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !folder) return;

    // Check if terms accepted
    if (!termsAccepted) {
      setPendingFile(file);
      fetchTerms();
      setShowTermsModal(true);
      // Reset the file input
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
      return;
    }

    processUpload(file);
  };

  // Process the actual upload
  const processUpload = async (file: File) => {
    if (!folder) return;

    setUploading(true);
    setUploadProgress(0);
    setUploadFileName(file.name);
    setError('');

    try {
      // Initiate upload
      const initResponse = await apiPost<UploadInitiateResponse>(`${API_BASE_URL}/fileshare/upload/initiate`, {
        folder_id: folder.id,
        subfolder_id: currentSubfolder?.id,
        filename: file.name,
        size_bytes: file.size,
        content_type: file.type || 'application/octet-stream'
      });

      if (initResponse.method === 'PUT') {
        // Single-part upload
        const uploadUrl = initResponse.upload_url!;

        await fetch(uploadUrl, {
          method: 'PUT',
          body: file,
          headers: { 'Content-Type': file.type || 'application/octet-stream' }
        });

        setUploadProgress(90);

        // Confirm upload
        await apiPost(`${API_BASE_URL}/fileshare/upload/${initResponse.file_id}/confirm`, {});
        setUploadProgress(100);
      } else {
        // Multipart upload
        const parts: { part_number: number; etag: string }[] = [];
        const totalParts = initResponse.parts!.length;

        for (const part of initResponse.parts!) {
          const start = (part.part_number - 1) * PART_SIZE;
          const end = Math.min(start + PART_SIZE, file.size);
          const chunk = file.slice(start, end);

          const uploadResponse = await fetch(part.upload_url, {
            method: 'PUT',
            body: chunk
          });

          const etag = uploadResponse.headers.get('ETag');
          if (!etag) throw new Error('Missing ETag in upload response');

          parts.push({
            part_number: part.part_number,
            etag: etag.replace(/"/g, '') // Remove quotes from ETag
          });

          setUploadProgress(Math.round((part.part_number / totalParts) * 90));
        }

        // Complete multipart upload
        await apiPost(`${API_BASE_URL}/fileshare/upload/complete`, {
          file_id: initResponse.file_id,
          upload_id: initResponse.upload_id,
          parts
        });

        setUploadProgress(100);
      }

      // Refresh file list
      await fetchData();
    } catch (err: any) {
      setError(err.message || 'Upload failed');
    } finally {
      setUploading(false);
      setUploadFileName('');
      setUploadProgress(0);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  // Handle download
  const handleDownload = async (file: SharedFile) => {
    try {
      const response = await apiPost<{ download_url: string }>(`${API_BASE_URL}/fileshare/download/${file.id}`, {});
      window.open(response.download_url, '_blank');
    } catch (err: any) {
      setError(err.message || 'Download failed');
    }
  };

  // Handle delete
  const handleDelete = async (file: SharedFile) => {
    if (!confirm(`Delete "${file.filename}"? This cannot be undone.`)) return;

    try {
      await apiDelete(`${API_BASE_URL}/fileshare/files/${file.id}`);
      await fetchData();
    } catch (err: any) {
      setError(err.message || 'Delete failed');
    }
  };

  if (loading) {
    return (
      <div className="p-4 max-w-6xl mx-auto">
        <div className="text-center py-12 text-gray-500">
          <RefreshCw className="w-8 h-8 animate-spin mx-auto mb-2" />
          Loading...
        </div>
      </div>
    );
  }

  if (!folder) {
    return (
      <div className="p-4 max-w-6xl mx-auto">
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700">
          Folder not found
        </div>
        <Link to="/fileshare" className="mt-4 inline-flex items-center text-blue-600 hover:underline">
          <ArrowLeft className="w-4 h-4 mr-1" /> Back to Fileshare
        </Link>
      </div>
    );
  }

  return (
    <div className="p-4 max-w-6xl mx-auto">
      {/* Breadcrumb */}
      <nav className="flex items-center gap-2 text-sm text-gray-500 mb-4">
        <Link to="/fileshare" className="hover:text-blue-600">Fileshare</Link>
        <ChevronRight className="w-4 h-4" />
        <Link to={`/fileshare/${folder.slug}`} className="hover:text-blue-600">{folder.name}</Link>
        {currentSubfolder && (
          <>
            <ChevronRight className="w-4 h-4" />
            <span className="text-gray-900">{currentSubfolder.name}</span>
          </>
        )}
      </nav>

      {/* Header */}
      <div className="flex justify-between items-start mb-6">
        <div>
          <h1 className="text-3xl font-bold text-gray-900 flex items-center gap-3">
            <FolderOpen className="w-8 h-8 text-blue-600" />
            {currentSubfolder ? currentSubfolder.name : folder.name}
          </h1>
          {folder.description && !currentSubfolder && (
            <p className="text-gray-600 mt-1">{folder.description}</p>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={fetchData}
            className="px-4 py-2 text-gray-600 hover:text-gray-800 hover:bg-gray-100 rounded-lg flex items-center gap-2"
          >
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
          {error}
        </div>
      )}

      {/* Upload Section */}
      {folder.can_upload && (
        <div className="bg-white rounded-lg shadow p-4 mb-6">
          <div className="flex items-center gap-4">
            <input
              ref={fileInputRef}
              type="file"
              onChange={handleFileSelect}
              disabled={uploading}
              className="block w-full text-sm text-gray-500
                file:mr-4 file:py-2 file:px-4
                file:rounded-lg file:border-0
                file:text-sm file:font-semibold
                file:bg-blue-50 file:text-blue-700
                hover:file:bg-blue-100
                disabled:opacity-50"
            />
          </div>

          {/* Upload Progress */}
          {uploading && (
            <div className="mt-4">
              <div className="flex justify-between text-sm text-gray-600 mb-1">
                <span>Uploading: {uploadFileName}</span>
                <span>{uploadProgress}%</span>
              </div>
              <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                <div
                  className="h-full bg-blue-500 transition-all duration-300"
                  style={{ width: `${uploadProgress}%` }}
                />
              </div>
            </div>
          )}
        </div>
      )}

      {/* Tree View - Subfolders with files, then root files */}
      {!currentSubfolder ? (
        <div className="space-y-4">
          {/* Subfolders with their files */}
          {subfolders.map((sub) => {
            const subFiles = filesBySubfolder.get(sub.id) || [];
            const isExpanded = expandedSubfolders.has(sub.id);

            return (
              <div key={sub.id} className="bg-white rounded-lg shadow overflow-hidden">
                {/* Subfolder header - clickable to expand/collapse */}
                <button
                  onClick={() => toggleSubfolder(sub.id)}
                  className="w-full px-4 py-3 flex items-center justify-between bg-gray-50 hover:bg-gray-100 transition-colors"
                >
                  <div className="flex items-center gap-3">
                    <ChevronDown className={`w-5 h-5 text-gray-400 transition-transform ${isExpanded ? '' : '-rotate-90'}`} />
                    <Folder className="w-5 h-5 text-yellow-500" />
                    <span className="font-medium text-gray-900">{sub.name}</span>
                    <span className="text-sm text-gray-500">({subFiles.length} files)</span>
                  </div>
                  <Link
                    to={`/fileshare/${folder.slug}/${sub.slug}`}
                    onClick={(e) => e.stopPropagation()}
                    className="text-sm text-blue-600 hover:underline"
                  >
                    Open folder →
                  </Link>
                </button>

                {/* Subfolder files */}
                {isExpanded && subFiles.length > 0 && (
                  <div className="divide-y">
                    {subFiles.map((file) => (
                      <div key={file.id} className="px-4 py-3 pl-12 flex items-center justify-between hover:bg-gray-50">
                        <div className="flex items-center gap-3">
                          <span className="text-xl">{getFileIcon(file.content_type)}</span>
                          <div>
                            <div className="font-medium text-gray-900">{file.filename}</div>
                            <div className="text-xs text-gray-500 flex items-center gap-1">
                              {formatBytes(file.size_bytes)} · {file.uploaded_by_email} · {formatDate(file.uploaded_at)} · <Clock className="w-3 h-3" /> Expires {formatDate(file.expires_at)} · {file.download_count} downloads
                            </div>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          {folder.can_download && (
                            <button
                              onClick={() => handleDownload(file)}
                              className="p-2 text-blue-600 hover:bg-blue-50 rounded-lg"
                              title="Download"
                            >
                              <Download className="w-4 h-4" />
                            </button>
                          )}
                          <button
                            onClick={() => setShowReportModal(file)}
                            className="p-2 text-orange-600 hover:bg-orange-50 rounded-lg"
                            title="Report file"
                          >
                            <Flag className="w-4 h-4" />
                          </button>
                          {(isSuper || file.uploaded_by_email === folder.created_by_email) && (
                            <button
                              onClick={() => handleDelete(file)}
                              className="p-2 text-red-600 hover:bg-red-50 rounded-lg"
                              title="Delete"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {/* Empty subfolder message */}
                {isExpanded && subFiles.length === 0 && (
                  <div className="px-4 py-3 pl-12 text-sm text-gray-500 italic">
                    No files in this subfolder
                  </div>
                )}
              </div>
            );
          })}

          {/* Divider if both subfolders and root files exist */}
          {subfolders.length > 0 && rootFiles.length > 0 && (
            <div className="border-t border-gray-200 my-4" />
          )}

          {/* Root files */}
          {rootFiles.length > 0 && (
            <div className="bg-white rounded-lg shadow overflow-hidden">
              <div className="px-4 py-3 bg-gray-50 border-b">
                <span className="font-medium text-gray-700">Root Files ({rootFiles.length})</span>
              </div>
              <div className="divide-y">
                {rootFiles.map((file) => (
                  <div key={file.id} className="px-4 py-3 flex items-center justify-between hover:bg-gray-50">
                    <div className="flex items-center gap-3">
                      <span className="text-xl">{getFileIcon(file.content_type)}</span>
                      <div>
                        <div className="font-medium text-gray-900">{file.filename}</div>
                        <div className="text-xs text-gray-500">
                          {formatBytes(file.size_bytes)} · {file.uploaded_by_email} · {formatDate(file.uploaded_at)} · <Clock className="w-3 h-3 inline" /> Expires {formatDate(file.expires_at)} · {file.download_count} downloads
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      {folder.can_download && (
                        <button
                          onClick={() => handleDownload(file)}
                          className="p-2 text-blue-600 hover:bg-blue-50 rounded-lg"
                          title="Download"
                        >
                          <Download className="w-4 h-4" />
                        </button>
                      )}
                      <button
                        onClick={() => setShowReportModal(file)}
                        className="p-2 text-orange-600 hover:bg-orange-50 rounded-lg"
                        title="Report file"
                      >
                        <Flag className="w-4 h-4" />
                      </button>
                      {(isSuper || file.uploaded_by_email === folder.created_by_email) && (
                        <button
                          onClick={() => handleDelete(file)}
                          className="p-2 text-red-600 hover:bg-red-50 rounded-lg"
                          title="Delete"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Empty state */}
          {subfolders.length === 0 && rootFiles.length === 0 && (
            <div className="bg-white rounded-lg shadow p-8 text-center text-gray-500">
              <File className="w-12 h-12 mx-auto mb-3 text-gray-300" />
              <p>No files or subfolders yet</p>
              {folder.can_upload && (
                <p className="text-sm mt-1">Upload a file to get started</p>
              )}
            </div>
          )}
        </div>
      ) : (
        /* Subfolder view - show files in table format */
        <div>
          <h2 className="text-lg font-semibold text-gray-800 mb-3">
            Files {files.length > 0 && `(${files.length})`}
          </h2>

          {files.length === 0 ? (
            <div className="bg-white rounded-lg shadow p-8 text-center text-gray-500">
              <File className="w-12 h-12 mx-auto mb-3 text-gray-300" />
              <p>No files in this subfolder</p>
              {folder.can_upload && (
                <p className="text-sm mt-1">Upload a file to get started</p>
              )}
            </div>
          ) : (
            <div className="bg-white rounded-lg shadow overflow-hidden">
              <table className="w-full">
                <thead className="bg-gray-50 border-b">
                  <tr>
                    <th className="text-left px-4 py-3 text-sm font-medium text-gray-600">File</th>
                    <th className="text-left px-4 py-3 text-sm font-medium text-gray-600">Size</th>
                    <th className="text-left px-4 py-3 text-sm font-medium text-gray-600">Uploaded</th>
                    <th className="text-left px-4 py-3 text-sm font-medium text-gray-600">Expires</th>
                    <th className="text-right px-4 py-3 text-sm font-medium text-gray-600">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {files.map((file) => (
                    <tr key={file.id} className="hover:bg-gray-50">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-3">
                          <span className="text-2xl">{getFileIcon(file.content_type)}</span>
                          <div>
                            <div className="font-medium text-gray-900">{file.filename}</div>
                            <div className="text-xs text-gray-500 flex items-center gap-2">
                              <User className="w-3 h-3" />
                              {file.uploaded_by_email}
                              <span className="text-gray-300">|</span>
                              <Download className="w-3 h-3" />
                              {file.download_count} downloads
                            </div>
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-600">
                        {formatBytes(file.size_bytes)}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-600">
                        {formatDate(file.uploaded_at)}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-600">
                        <div className="flex items-center gap-1">
                          <Clock className="w-3 h-3" />
                          {formatDate(file.expires_at)}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <div className="flex justify-end gap-2">
                          {folder.can_download && (
                            <button
                              onClick={() => handleDownload(file)}
                              className="p-2 text-blue-600 hover:bg-blue-50 rounded-lg"
                              title="Download"
                            >
                              <Download className="w-4 h-4" />
                            </button>
                          )}
                          <button
                            onClick={() => setShowReportModal(file)}
                            className="p-2 text-orange-600 hover:bg-orange-50 rounded-lg"
                            title="Report file"
                          >
                            <Flag className="w-4 h-4" />
                          </button>
                          {(isSuper || file.uploaded_by_email === folder.created_by_email) && (
                            <button
                              onClick={() => handleDelete(file)}
                              className="p-2 text-red-600 hover:bg-red-50 rounded-lg"
                              title="Delete"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Terms of Service Modal */}
      {showTermsModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full max-h-[80vh] flex flex-col">
            <div className="px-6 py-4 border-b flex justify-between items-center">
              <h3 className="text-xl font-semibold flex items-center gap-2">
                <FileWarning className="w-6 h-6 text-amber-500" />
                Fileshare Terms of Service
              </h3>
              <button onClick={() => { setShowTermsModal(false); setPendingFile(null); }}>
                <X className="w-5 h-5 text-gray-500" />
              </button>
            </div>
            <div className="p-6 overflow-y-auto flex-1">
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 mb-4">
                <p className="text-amber-800 text-sm">
                  <AlertTriangle className="w-4 h-4 inline mr-2" />
                  You must accept these terms before uploading files.
                </p>
              </div>
              <pre className="whitespace-pre-wrap text-sm text-gray-700 font-sans leading-relaxed">
                {termsText}
              </pre>
            </div>
            <div className="px-6 py-4 border-t flex justify-end gap-3">
              <button
                onClick={() => { setShowTermsModal(false); setPendingFile(null); }}
                className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg"
              >
                Decline
              </button>
              <button
                onClick={handleAcceptTerms}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
              >
                I Accept These Terms
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Report File Modal */}
      {showReportModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full">
            <div className="px-6 py-4 border-b flex justify-between items-center">
              <h3 className="text-xl font-semibold flex items-center gap-2">
                <Flag className="w-5 h-5 text-orange-500" />
                Report File
              </h3>
              <button onClick={() => { setShowReportModal(null); setReportReason(''); }}>
                <X className="w-5 h-5 text-gray-500" />
              </button>
            </div>
            <div className="p-6">
              <div className="mb-4">
                <p className="text-sm text-gray-600 mb-2">You are reporting:</p>
                <p className="font-medium text-gray-900">{showReportModal.filename}</p>
              </div>
              <div className="mb-4">
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Reason for report <span className="text-red-500">*</span>
                </label>
                <textarea
                  value={reportReason}
                  onChange={(e) => setReportReason(e.target.value)}
                  placeholder="Please describe why you are reporting this file (minimum 10 characters)..."
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg resize-none"
                  rows={4}
                />
                <p className="text-xs text-gray-500 mt-1">
                  {reportReason.length}/10 minimum characters
                </p>
              </div>
              <div className="bg-gray-50 rounded-lg p-3 text-sm text-gray-600">
                <p>Administrators will be notified and will review this file. False reports may result in loss of access.</p>
              </div>
            </div>
            <div className="px-6 py-4 border-t flex justify-end gap-3">
              <button
                onClick={() => { setShowReportModal(null); setReportReason(''); }}
                className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg"
              >
                Cancel
              </button>
              <button
                onClick={handleReportFile}
                disabled={reportReason.length < 10 || reportSubmitting}
                className="px-4 py-2 bg-orange-600 text-white rounded-lg hover:bg-orange-700 disabled:opacity-50"
              >
                {reportSubmitting ? 'Submitting...' : 'Submit Report'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default FolderView;
