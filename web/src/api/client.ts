import type {
  AnalyticsDashboardResponse,
  AdminOpsResponse,
  AdminOverviewResponse,
  AlertsResponse,
  AuthMeResponse,
  AuthSecurityInfo,
  CaptureActionResponse,
  CaptureConfigAuditResponse,
  CaptureConfigResponse,
  CaptureDeleteParams,
  CaptureDeleteResponse,
  CaptureQueryParams,
  CaptureLogsResponse,
  CaptureModelStatus,
  CaptureRecentResponse,
  CaptureRuntimeStatus,
  CaptureSyncResponse,
  CameraListResponse,
  CameraRoiConfig,
  CameraRoiTestResult,
  CameraSourceConfigListResponse,
  LoginResponse,
  SearchFeedbackResponse,
  SearchHistoryResponse,
  SearchResponse,
  SetupDbTestResponse,
  SetupInitializeResponse,
  SetupStatusResponse,
  StatusResponse,
  UserItem,
  UserListResponse,
} from './types';

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8002/api/v1';
export const AUTH_EXPIRED_EVENT = 'reid:auth-expired';

function withAuthHeaders(token?: string): HeadersInit {
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}

async function ensureOk(res: Response, label: string): Promise<void> {
  if (res.ok) return;
  const text = await res.text();
  if (res.status === 401) {
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
    }
    throw new Error('登录已失效，请重新登录');
  }
  throw new Error(`${label} failed: ${res.status}${text ? ` ${text}` : ''}`);
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Login failed: ${res.status}${text ? ` ${text}` : ''}`);
  }
  return (await res.json()) as LoginResponse;
}

export async function fetchAuthMe(token: string): Promise<AuthMeResponse> {
  const res = await fetch(`${API_BASE}/auth/me`, {
    headers: { ...withAuthHeaders(token) },
  });
  await ensureOk(res, 'Fetch auth me');
  return (await res.json()) as AuthMeResponse;
}

export async function changeMyPassword(
  token: string,
  currentPassword: string,
  newPassword: string,
): Promise<{ status: string }> {
  const res = await fetch(`${API_BASE}/auth/change-password`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...withAuthHeaders(token),
    },
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  });
  await ensureOk(res, 'Change password');
  return (await res.json()) as { status: string };
}

export async function fetchAuthSecurity(token: string): Promise<AuthSecurityInfo> {
  const res = await fetch(`${API_BASE}/auth/security`, {
    headers: { ...withAuthHeaders(token) },
  });
  await ensureOk(res, 'Fetch auth security');
  return (await res.json()) as AuthSecurityInfo;
}

export async function fetchStatus(token: string): Promise<StatusResponse> {
  const res = await fetch(`${API_BASE}/status`, {
    headers: { ...withAuthHeaders(token) },
  });
  await ensureOk(res, 'Status');
  return (await res.json()) as StatusResponse;
}

export async function fetchSetupStatus(): Promise<SetupStatusResponse> {
  const res = await fetch(`${API_BASE}/setup/status`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Fetch setup status failed: ${res.status}${text ? ` ${text}` : ''}`);
  }
  return (await res.json()) as SetupStatusResponse;
}

export async function testSetupDatabase(payload: {
  host: string;
  port: number;
  dbname: string;
  user: string;
  password: string;
}): Promise<SetupDbTestResponse> {
  const res = await fetch(`${API_BASE}/setup/test-db`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Test setup database failed: ${res.status}${text ? ` ${text}` : ''}`);
  }
  return (await res.json()) as SetupDbTestResponse;
}

export async function initializeSetup(payload: {
  host: string;
  port: number;
  dbname: string;
  user: string;
  password: string;
  admin_username: string;
  admin_password: string;
}): Promise<SetupInitializeResponse> {
  const res = await fetch(`${API_BASE}/setup/initialize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Initialize setup failed: ${res.status}${text ? ` ${text}` : ''}`);
  }
  return (await res.json()) as SetupInitializeResponse;
}

export async function search(token: string, formData: FormData): Promise<SearchResponse> {
  const res = await fetch(`${API_BASE}/search`, {
    method: 'POST',
    headers: { ...withAuthHeaders(token) },
    body: formData,
  });
  await ensureOk(res, 'Search');
  return (await res.json()) as SearchResponse;
}

export async function submitSearchFeedback(
  token: string,
  queryId: string,
  trackId: number,
  verdict: 'hit' | 'miss',
  note?: string,
): Promise<SearchFeedbackResponse> {
  const res = await fetch(`${API_BASE}/search/${encodeURIComponent(queryId)}/feedback`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...withAuthHeaders(token),
    },
    body: JSON.stringify({
      track_id: trackId,
      verdict,
      note: note?.trim() ? note.trim() : null,
    }),
  });
  await ensureOk(res, 'Submit search feedback');
  return (await res.json()) as SearchFeedbackResponse;
}

export async function fetchSearchHistory(
  token: string,
  limit = 12,
  allUsers = false,
): Promise<SearchHistoryResponse> {
  const query = new URLSearchParams();
  query.set('limit', String(limit));
  if (allUsers) query.set('all_users', 'true');
  const res = await fetch(`${API_BASE}/search/history?${query.toString()}`, {
    headers: { ...withAuthHeaders(token) },
  });
  await ensureOk(res, 'Fetch search history');
  return (await res.json()) as SearchHistoryResponse;
}

export async function fetchCameras(token: string): Promise<CameraListResponse> {
  const res = await fetch(`${API_BASE}/cameras`, {
    headers: { ...withAuthHeaders(token) },
  });
  await ensureOk(res, 'Fetch cameras');
  return (await res.json()) as CameraListResponse;
}

export async function testCamera(token: string, cameraId: string): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}/cameras/${encodeURIComponent(cameraId)}/test`, {
    method: 'POST',
    headers: { ...withAuthHeaders(token) },
  });
  await ensureOk(res, 'Test camera');
  return (await res.json()) as Record<string, unknown>;
}

export async function fetchCameraSnapshot(
  token: string,
  cameraId: string,
  drawBoxes = false,
): Promise<Blob> {
  const res = await fetch(
    `${API_BASE}/cameras/${encodeURIComponent(cameraId)}/snapshot?draw_boxes=${drawBoxes ? 'true' : 'false'}&apply_roi=false`,
    {
      headers: { ...withAuthHeaders(token) },
    },
  );
  if (!res.ok) throw new Error(`Fetch camera snapshot failed: ${res.status}`);
  return await res.blob();
}

export async function fetchCameraRoi(token: string, cameraId: string): Promise<CameraRoiConfig> {
  const res = await fetch(`${API_BASE}/cameras/${encodeURIComponent(cameraId)}/roi`, {
    headers: { ...withAuthHeaders(token) },
  });
  if (!res.ok) throw new Error(`Fetch camera ROI failed: ${res.status}`);
  return (await res.json()) as CameraRoiConfig;
}

export async function saveCameraRoi(
  token: string,
  cameraId: string,
  payload: Pick<CameraRoiConfig, 'include' | 'exclude'>,
): Promise<CameraRoiConfig> {
  const res = await fetch(`${API_BASE}/cameras/${encodeURIComponent(cameraId)}/roi`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      ...withAuthHeaders(token),
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Save camera ROI failed: ${res.status} ${text}`);
  }
  return (await res.json()) as CameraRoiConfig;
}

export async function testCameraRoi(token: string, cameraId: string): Promise<CameraRoiTestResult> {
  const res = await fetch(`${API_BASE}/cameras/${encodeURIComponent(cameraId)}/roi/test`, {
    headers: { ...withAuthHeaders(token) },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Test camera ROI failed: ${res.status} ${text}`);
  }
  return (await res.json()) as CameraRoiTestResult;
}

export async function fetchCaptureStatus(token: string): Promise<CaptureRuntimeStatus> {
  const res = await fetch(`${API_BASE}/capture/status`, {
    headers: { ...withAuthHeaders(token) },
  });
  await ensureOk(res, 'Fetch capture status');
  return (await res.json()) as CaptureRuntimeStatus;
}

export async function startCapture(token: string, cameraId?: string): Promise<CaptureActionResponse> {
  const q = new URLSearchParams();
  if (cameraId && cameraId.trim()) q.set('camera_id', cameraId.trim());
  const suffix = q.toString() ? `?${q.toString()}` : '';
  const res = await fetch(`${API_BASE}/capture/start${suffix}`, {
    method: 'POST',
    headers: { ...withAuthHeaders(token) },
  });
  await ensureOk(res, 'Start capture');
  return (await res.json()) as CaptureActionResponse;
}

export async function stopCapture(token: string): Promise<CaptureActionResponse> {
  const res = await fetch(`${API_BASE}/capture/stop`, {
    method: 'POST',
    headers: { ...withAuthHeaders(token) },
  });
  await ensureOk(res, 'Stop capture');
  return (await res.json()) as CaptureActionResponse;
}

export async function restartCapture(token: string, cameraId?: string): Promise<CaptureActionResponse> {
  const q = new URLSearchParams();
  if (cameraId && cameraId.trim()) q.set('camera_id', cameraId.trim());
  const suffix = q.toString() ? `?${q.toString()}` : '';
  const res = await fetch(`${API_BASE}/capture/restart${suffix}`, {
    method: 'POST',
    headers: { ...withAuthHeaders(token) },
  });
  await ensureOk(res, 'Restart capture');
  return (await res.json()) as CaptureActionResponse;
}

export async function fetchCaptureConfig(token: string): Promise<CaptureConfigResponse> {
  const res = await fetch(`${API_BASE}/capture/config`, {
    headers: { ...withAuthHeaders(token) },
  });
  await ensureOk(res, 'Fetch capture config');
  return (await res.json()) as CaptureConfigResponse;
}

export async function saveCaptureConfig(
  token: string,
  config: Record<string, unknown>,
): Promise<CaptureConfigResponse> {
  const res = await fetch(`${API_BASE}/capture/config`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      ...withAuthHeaders(token),
    },
    body: JSON.stringify({ config }),
  });
  await ensureOk(res, 'Save capture config');
  return (await res.json()) as CaptureConfigResponse;
}

export async function fetchCaptureLogs(token: string, limit = 200): Promise<CaptureLogsResponse> {
  const res = await fetch(`${API_BASE}/capture/logs?limit=${encodeURIComponent(String(limit))}`, {
    headers: { ...withAuthHeaders(token) },
  });
  await ensureOk(res, 'Fetch capture logs');
  return (await res.json()) as CaptureLogsResponse;
}

export async function fetchCaptureConfigAudit(token: string, limit = 80): Promise<CaptureConfigAuditResponse> {
  const res = await fetch(`${API_BASE}/capture/config-audit?limit=${encodeURIComponent(String(limit))}`, {
    headers: { ...withAuthHeaders(token) },
  });
  await ensureOk(res, 'Fetch capture config audit');
  return (await res.json()) as CaptureConfigAuditResponse;
}

export async function fetchCaptureRecent(token: string, limit = 60): Promise<CaptureRecentResponse> {
  const res = await fetch(`${API_BASE}/capture/recent?limit=${encodeURIComponent(String(limit))}`, {
    headers: { ...withAuthHeaders(token) },
  });
  await ensureOk(res, 'Fetch capture recent');
  return (await res.json()) as CaptureRecentResponse;
}

export async function fetchCaptureModelStatus(token: string): Promise<CaptureModelStatus> {
  const res = await fetch(`${API_BASE}/capture/model-status`, {
    headers: { ...withAuthHeaders(token) },
  });
  await ensureOk(res, 'Fetch capture model status');
  return (await res.json()) as CaptureModelStatus;
}

export async function queryCaptureItems(
  token: string,
  params: CaptureQueryParams,
): Promise<CaptureRecentResponse> {
  const q = new URLSearchParams();
  const append = (key: string, value: unknown) => {
    if (value === null || value === undefined) return;
    const text = String(value).trim();
    if (!text) return;
    q.set(key, text);
  };
  append('limit', params.limit ?? 100);
  append('scan_limit', params.scan_limit ?? 5000);
  append('camera_id', params.camera_id);
  append('upper_color', params.upper_color);
  append('lower_color', params.lower_color);
  if (typeof params.has_hat === 'boolean') append('has_hat', params.has_hat ? 'true' : 'false');
  append('image_mode', params.image_mode);
  if (typeof params.is_night === 'boolean') append('is_night', params.is_night ? 'true' : 'false');
  append('pose_hint', params.pose_hint);
  if (typeof params.min_quality_score === 'number') append('min_quality_score', params.min_quality_score);
  append('time_start', params.time_start);
  append('time_end', params.time_end);

  const res = await fetch(`${API_BASE}/capture/query?${q.toString()}`, {
    headers: { ...withAuthHeaders(token) },
  });
  await ensureOk(res, 'Query capture items');
  return (await res.json()) as CaptureRecentResponse;
}

export async function fetchCapturePhoto(token: string, imagePath: string, trackId?: number): Promise<Blob> {
  const q = new URLSearchParams();
  if (imagePath.trim()) q.set('image_path', imagePath.trim());
  if (typeof trackId === 'number' && Number.isFinite(trackId) && trackId > 0) {
    q.set('track_id', String(Math.trunc(trackId)));
  }
  const res = await fetch(`${API_BASE}/capture/photo?${q.toString()}`, {
    headers: { ...withAuthHeaders(token) },
  });
  await ensureOk(res, 'Fetch capture photo');
  return await res.blob();
}

export async function deleteCaptureItems(
  token: string,
  params: CaptureDeleteParams,
): Promise<CaptureDeleteResponse> {
  const res = await fetch(`${API_BASE}/capture/delete`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...withAuthHeaders(token),
    },
    body: JSON.stringify({
      track_ids: params.track_ids ?? [],
      image_paths: params.image_paths ?? [],
      camera_id: params.camera_id,
      upper_color: params.upper_color,
      lower_color: params.lower_color,
      has_hat: params.has_hat,
      image_mode: params.image_mode,
      is_night: params.is_night,
      pose_hint: params.pose_hint,
      min_quality_score: params.min_quality_score,
      time_start: params.time_start,
      time_end: params.time_end,
      delete_local_files: params.delete_local_files ?? true,
      dry_run: params.dry_run ?? false,
    }),
  });
  await ensureOk(res, 'Delete capture items');
  return (await res.json()) as CaptureDeleteResponse;
}

export async function syncCaptureToDb(
  token: string,
  scanLimit = 5000,
  purgeLocalImages = true,
): Promise<CaptureSyncResponse> {
  const res = await fetch(
    `${API_BASE}/capture/sync-db?scan_limit=${encodeURIComponent(String(scanLimit))}&purge_local_images=${purgeLocalImages ? 'true' : 'false'}`,
    {
    method: 'POST',
    headers: { ...withAuthHeaders(token) },
  },
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Sync capture metadata to DB failed: ${res.status} ${text}`);
  }
  return (await res.json()) as CaptureSyncResponse;
}

export async function fetchAlerts(token: string): Promise<AlertsResponse> {
  const res = await fetch(`${API_BASE}/alerts`, {
    headers: { ...withAuthHeaders(token) },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Fetch alerts failed: ${res.status} ${text}`);
  }
  return (await res.json()) as AlertsResponse;
}

export async function fetchAdminOverview(token: string): Promise<AdminOverviewResponse> {
  const res = await fetch(`${API_BASE}/admin/overview`, {
    headers: { ...withAuthHeaders(token) },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Fetch admin overview failed: ${res.status} ${text}`);
  }
  return (await res.json()) as AdminOverviewResponse;
}

export async function fetchAdminOps(token: string): Promise<AdminOpsResponse> {
  const res = await fetch(`${API_BASE}/admin/ops`, {
    headers: { ...withAuthHeaders(token) },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Fetch admin ops failed: ${res.status} ${text}`);
  }
  return (await res.json()) as AdminOpsResponse;
}

export async function fetchAnalyticsDashboard(
  token: string,
  params: {
    rangeStart?: string;
    rangeEnd?: string;
    granularity?: string;
    cameraId?: string;
  },
): Promise<AnalyticsDashboardResponse> {
  const q = new URLSearchParams();
  if (params.rangeStart) q.set('range_start', params.rangeStart);
  if (params.rangeEnd) q.set('range_end', params.rangeEnd);
  if (params.granularity) q.set('granularity', params.granularity);
  if (params.cameraId) q.set('camera_id', params.cameraId);
  const suffix = q.toString() ? `?${q.toString()}` : '';
  const res = await fetch(`${API_BASE}/analytics/dashboard${suffix}`, {
    headers: { ...withAuthHeaders(token) },
  });
  await ensureOk(res, 'Fetch analytics dashboard');
  return (await res.json()) as AnalyticsDashboardResponse;
}

export async function fetchUsers(token: string): Promise<UserListResponse> {
  const res = await fetch(`${API_BASE}/admin/users`, {
    headers: { ...withAuthHeaders(token) },
  });
  await ensureOk(res, 'Fetch users');
  return (await res.json()) as UserListResponse;
}

export async function createUser(
  token: string,
  payload: { username: string; password: string; role: string; is_active: boolean },
): Promise<UserItem> {
  const res = await fetch(`${API_BASE}/admin/users`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...withAuthHeaders(token),
    },
    body: JSON.stringify(payload),
  });
  await ensureOk(res, 'Create user');
  return (await res.json()) as UserItem;
}

export async function updateUser(
  token: string,
  username: string,
  payload: { role?: string; is_active?: boolean },
): Promise<UserItem> {
  const res = await fetch(`${API_BASE}/admin/users/${encodeURIComponent(username)}`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      ...withAuthHeaders(token),
    },
    body: JSON.stringify(payload),
  });
  await ensureOk(res, 'Update user');
  return (await res.json()) as UserItem;
}

export async function resetUserPassword(
  token: string,
  username: string,
  newPassword: string,
): Promise<{ status: string }> {
  const res = await fetch(`${API_BASE}/admin/users/${encodeURIComponent(username)}/password`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      ...withAuthHeaders(token),
    },
    body: JSON.stringify({ new_password: newPassword }),
  });
  await ensureOk(res, 'Reset user password');
  return (await res.json()) as { status: string };
}

export async function fetchCameraConfigs(token: string): Promise<CameraSourceConfigListResponse> {
  const res = await fetch(`${API_BASE}/cameras/configs`, {
    headers: { ...withAuthHeaders(token) },
  });
  await ensureOk(res, 'Fetch camera configs');
  return (await res.json()) as CameraSourceConfigListResponse;
}

export async function saveCameraConfigs(
  token: string,
  items: CameraSourceConfigListResponse['items'],
): Promise<CameraSourceConfigListResponse> {
  const res = await fetch(`${API_BASE}/cameras/configs`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      ...withAuthHeaders(token),
    },
    body: JSON.stringify({ items }),
  });
  await ensureOk(res, 'Save camera configs');
  return (await res.json()) as CameraSourceConfigListResponse;
}
