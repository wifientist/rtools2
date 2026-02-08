// Fileshare API types

export interface FileFolder {
  id: number;
  name: string;
  slug: string;
  description: string | null;
  company_id: number | null;
  company_name: string | null;
  quota_bytes: number;
  used_bytes: number;
  created_by_email: string;
  created_at: string;
  subfolder_count: number;
  file_count: number;
  can_download: boolean;
  can_upload: boolean;
}

export interface FileSubfolder {
  id: number;
  folder_id: number;
  name: string;
  slug: string;
  created_at: string;
  file_count: number;
}

export interface SharedFile {
  id: number;
  folder_id: number;
  folder_slug: string;
  subfolder_id: number | null;
  subfolder_slug: string | null;
  filename: string;
  size_bytes: number;
  content_type: string;
  uploaded_by_email: string;
  uploaded_at: string;
  expires_at: string;
  download_count: number;
}

export interface FolderPermission {
  id: number;
  folder_id: number;
  user_id: number;
  user_email: string;
  permission_type: 'download' | 'upload';
  granted_by_email: string;
  granted_at: string;
}

export interface UploadInitiateRequest {
  folder_id: number;
  subfolder_id?: number;
  filename: string;
  size_bytes: number;
  content_type: string;
}

export interface UploadInitiateResponse {
  file_id: number;
  method: 'PUT' | 'MULTIPART';
  upload_url?: string;
  upload_id?: string;
  parts?: { part_number: number; upload_url: string }[];
  part_size: number;
}

export interface DownloadResponse {
  download_url: string;
  expires_in: number;
}

export interface AuditLogEntry {
  id: number;
  user_email: string;
  action: string;
  filename: string;
  folder_slug: string;
  subfolder_slug: string | null;
  file_size_bytes: number | null;
  ip_address: string | null;
  created_at: string;
}

// Helper functions
export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

export function formatDate(dateString: string): string {
  return new Date(dateString).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  });
}

export function getFileIcon(contentType: string): string {
  if (contentType.startsWith('image/')) return '🖼️';
  if (contentType.includes('pdf')) return '📄';
  if (contentType.includes('zip') || contentType.includes('tar') || contentType.includes('gzip')) return '📦';
  if (contentType.includes('csv') || contentType.includes('spreadsheet')) return '📊';
  if (contentType.includes('xml') || contentType.includes('json')) return '📋';
  return '📁';
}
