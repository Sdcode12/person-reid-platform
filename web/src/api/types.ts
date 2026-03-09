export type Role = 'admin' | 'operator' | 'auditor';

export interface LoginResponse {
  access_token: string;
  token_type: string;
  role: Role;
}

export interface AuthMeResponse {
  user_id: number;
  username: string;
  role: Role;
  is_active: boolean;
  managed_by_db: boolean;
  created_at?: string | null;
  last_login_at?: string | null;
  password_updated_at?: string | null;
  must_change_password: boolean;
  permissions: string[];
}

export interface AuthSecurityInfo {
  token_expire_minutes: number;
  jwt_algorithm: string;
  password_min_length: number;
  auth_mode: 'db_only';
  max_failed_login_attempts: number;
  account_lock_minutes: number;
  roles: Role[];
  permission_matrix: Record<string, string[]>;
}

export interface UserItem {
  user_id: number;
  username: string;
  role: Role;
  is_active: boolean;
  created_at?: string | null;
  last_login_at?: string | null;
  password_updated_at?: string | null;
  failed_login_count: number;
  locked_until?: string | null;
  must_change_password: boolean;
  permissions: string[];
}

export interface UserListResponse {
  items: UserItem[];
}

export interface StatusResponse {
  service: string;
  db: string;
  ingestion: string;
  time: string;
}

export interface SetupDatabaseSummary {
  host: string;
  port: number;
  dbname: string;
  user: string;
  has_password: boolean;
}

export interface SetupStatusResponse {
  setup_required: boolean;
  setup_completed: boolean;
  config_exists: boolean;
  db_configured: boolean;
  db_reachable: boolean;
  schema_ready: boolean;
  admin_exists: boolean;
  config_path: string;
  database: SetupDatabaseSummary;
  next_step: string;
  detail?: string | null;
}

export interface SetupDbTestResponse {
  ok: boolean;
  detail: string;
  db_version?: string | null;
  pgvector_installed: boolean;
}

export interface SetupInitializeResponse {
  status: string;
  config_path: string;
  setup_completed: boolean;
  admin_username: string;
  applied_migrations: number;
  skipped_migrations: number;
  detail?: string | null;
}

export interface SearchEvidenceItem {
  track_id: number;
  target_key?: string | null;
  similarity: number;
  body_sim: number;
  upper_sim?: number | null;
  lower_sim?: number | null;
  face_sim?: number | null;
  attr_score: number;
  spacetime_score: number;
  camera_id: string;
  start_time: string;
  end_time: string;
  upper_color: string;
  lower_color: string;
  image_path?: string | null;
  person_bbox?: number[] | null;
  has_hat?: boolean | null;
  image_mode?: string | null;
  is_night?: boolean | null;
  quality_score?: number | null;
  pose_hint?: string | null;
  face_used?: boolean | null;
  face_available?: boolean | null;
}

export interface SearchItem {
  track_id: number;
  target_key?: string | null;
  similarity: number;
  body_sim: number;
  upper_sim?: number | null;
  lower_sim?: number | null;
  face_sim?: number | null;
  attr_score: number;
  spacetime_score: number;
  camera_id: string;
  start_time: string;
  end_time: string;
  upper_color: string;
  lower_color: string;
  image_path?: string | null;
  person_bbox?: number[] | null;
  has_hat?: boolean | null;
  image_mode?: string | null;
  is_night?: boolean | null;
  quality_score?: number | null;
  pose_hint?: string | null;
  face_used?: boolean | null;
  face_available?: boolean | null;
  evidence_count: number;
  evidence: SearchEvidenceItem[];
}

export interface SearchResponse {
  query_id: string;
  count: number;
  elapsed_ms: number;
  funnel: {
    layer1_count: number;
    layer2_count: number;
    layer3_count: number;
  };
  metrics?: {
    candidate_reduction_rate?: number;
    recall_at_10?: number;
    fpr?: number;
    p95_latency_ms?: number;
    query_has_face?: number;
    face_assist_used?: number;
    reranked_count?: number;
  };
  timeline: Record<string, number>;
  results: SearchItem[];
}

export interface SearchFeedbackResponse {
  query_id: string;
  track_id: number;
  verdict: string;
  note?: string | null;
  status: string;
  feedback_id?: number | null;
  created_at?: string | null;
}

export interface SearchHistoryItem {
  query_id: string;
  created_by: string;
  created_at: string;
  upper_color?: string | null;
  lower_color?: string | null;
  time_start?: string | null;
  time_end?: string | null;
  camera_id?: string | null;
  image_mode?: string | null;
  has_hat?: boolean | null;
  pose_hint?: string | null;
  min_quality_score?: number | null;
  face_mode: string;
  group_by_target: boolean;
  diverse_camera: boolean;
  top_k: number;
  result_count: number;
  elapsed_ms: number;
  hit_count: number;
  miss_count: number;
  latest_feedback_at?: string | null;
  funnel?: Record<string, number>;
  metrics?: Record<string, number>;
}

export interface SearchHistoryResponse {
  items: SearchHistoryItem[];
}

export interface CameraStatusItem {
  camera_id: string;
  camera_name: string;
  online: boolean;
  last_frame_time: string | null;
  frames_read: number;
  failures: number;
}

export interface CameraListResponse {
  items: CameraStatusItem[];
}

export interface CameraSourceConfigItem {
  id: string;
  name: string;
  vendor: string;
  rtsp_url: string;
  event_api_url: string;
  snapshot_api_url: string;
  host: string;
  port: number;
  scheme: string;
  username: string;
  password: string;
  channel_id: number;
  enabled: boolean;
}

export interface CameraSourceConfigListResponse {
  source_path: string;
  items: CameraSourceConfigItem[];
}

export interface RoiPoint {
  x: number;
  y: number;
}

export interface RoiPolygon {
  points: RoiPoint[];
}

export interface CameraRoiConfig {
  camera_id: string;
  include: RoiPolygon[];
  exclude: RoiPolygon[];
  updated_by: string;
  updated_at: string | null;
}

export interface CameraRoiTestResult {
  camera_id: string;
  timestamp: string;
  raw_people_count: number;
  filtered_people_count: number;
  dropped_count: number;
  include_polygon_count: number;
  exclude_polygon_count: number;
}

export interface CaptureRuntimeStatus {
  running: boolean;
  desired_running?: boolean;
  pid: number | null;
  started_at: string | null;
  last_exit_code: number | null;
  auto_restart_enabled?: boolean;
  restart_pending?: boolean;
  restart_count?: number;
  script_path: string;
  config_path: string;
  runtime_config_path?: string | null;
  active_camera_id?: string | null;
  active_camera_ids?: string[];
  desired_camera_ids?: string[];
  pending_camera_ids?: string[];
  worker_count?: number;
  workers?: Array<Record<string, unknown>>;
  start_errors?: string[];
  command: string[];
}

export interface CaptureActionResponse {
  status: CaptureRuntimeStatus;
}

export interface CaptureConfigResponse {
  config_path: string;
  config: Record<string, unknown>;
}

export interface CaptureLogItem {
  timestamp: string;
  source: string;
  line: string;
}

export interface CaptureLogsResponse {
  items: CaptureLogItem[];
}

export interface CaptureConfigAuditItem {
  timestamp: string;
  actor: string;
  changed_count: number;
  changed_paths: string[];
}

export interface CaptureConfigAuditResponse {
  items: CaptureConfigAuditItem[];
}

export interface CaptureRecentResponse {
  items: Record<string, unknown>[];
}

export interface CaptureModelStatus {
  checked_at: string;
  yolo_model_path: string;
  yolo_model_exists: boolean;
  reid_capture_mode: string;
  reid_capture_model_path: string;
  reid_capture_model_exists: boolean;
  reid_capture_backend: string;
  reid_capture_ready: boolean;
  reid_search_mode: string;
  reid_search_model_path: string;
  reid_search_model_exists: boolean;
  reid_search_backend: string;
  reid_search_ready: boolean;
}

export interface CaptureQueryParams {
  limit?: number;
  scan_limit?: number;
  camera_id?: string;
  upper_color?: string;
  lower_color?: string;
  has_hat?: boolean;
  image_mode?: string;
  is_night?: boolean;
  pose_hint?: string;
  min_quality_score?: number;
  time_start?: string;
  time_end?: string;
}

export interface CaptureDeleteParams {
  track_ids?: number[];
  image_paths?: string[];
  camera_id?: string;
  upper_color?: string;
  lower_color?: string;
  has_hat?: boolean;
  image_mode?: string;
  is_night?: boolean;
  pose_hint?: string;
  min_quality_score?: number;
  time_start?: string;
  time_end?: string;
  delete_local_files?: boolean;
  dry_run?: boolean;
}

export interface CaptureDeleteResponse {
  mode: 'selection' | 'filters' | string;
  dry_run: boolean;
  matched: number;
  deleted: number;
  deleted_local_images?: number;
  deleted_local_sidecars?: number;
}

export interface CaptureSyncResponse {
  scanned: number;
  inserted: number;
  updated?: number;
  skipped: number;
  errors: number;
  purged_local_images?: number;
  purged_local_sidecars?: number;
  total_records?: number;
}

export interface AlertItem {
  id: string;
  level: string;
  source: string;
  message: string;
  created_at: string;
}

export interface AlertsResponse {
  items: AlertItem[];
}

export interface AdminOverviewResponse {
  today_tracks: number;
  total_tracks: number;
  alerts_open: number;
  disk_used_gb: number;
  note: string;
}

export interface AdminOpsCheckItem {
  key: string;
  label: string;
  level: string;
  ok: boolean;
  value: string;
  detail?: string | null;
}

export interface AdminOpsResponse {
  generated_at: string;
  checks: AdminOpsCheckItem[];
}

export interface AnalyticsTrendPoint {
  label: string;
  bucket_start: string;
  bucket_end: string;
  value: number;
}

export interface AnalyticsDistributionItem {
  key: string;
  label: string;
  value: number;
  ratio: number;
}

export interface AnalyticsTopCameraItem {
  camera_id: string;
  label: string;
  value: number;
  ratio: number;
}

export interface AnalyticsDashboardResponse {
  generated_at: string;
  source: string;
  range_start: string;
  range_end: string;
  granularity: string;
  camera_id?: string | null;
  total_count: number;
  today_count: number;
  range_count: number;
  previous_range_count: number;
  range_change_ratio?: number | null;
  active_camera_count: number;
  trend: AnalyticsTrendPoint[];
  camera_distribution: AnalyticsDistributionItem[];
  mode_distribution: AnalyticsDistributionItem[];
  top_cameras: AnalyticsTopCameraItem[];
  note?: string | null;
}
