from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    role: str


class AuthMeResponse(BaseModel):
    user_id: int
    username: str
    role: str
    is_active: bool = True
    managed_by_db: bool = True
    created_at: datetime | None = None
    last_login_at: datetime | None = None
    password_updated_at: datetime | None = None
    must_change_password: bool = False
    permissions: list[str] = Field(default_factory=list)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class AuthSecurityInfoResponse(BaseModel):
    token_expire_minutes: int
    jwt_algorithm: str
    password_min_length: int
    auth_mode: str = "db_only"
    max_failed_login_attempts: int = 5
    account_lock_minutes: int = 15
    roles: list[str] = Field(default_factory=list)
    permission_matrix: dict[str, list[str]] = Field(default_factory=dict)


class UserItem(BaseModel):
    user_id: int
    username: str
    role: str
    is_active: bool
    created_at: datetime | None = None
    last_login_at: datetime | None = None
    password_updated_at: datetime | None = None
    failed_login_count: int = 0
    locked_until: datetime | None = None
    must_change_password: bool = False
    permissions: list[str] = Field(default_factory=list)


class UserListResponse(BaseModel):
    items: list[UserItem] = Field(default_factory=list)


class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: str
    is_active: bool = True


class UserUpdateRequest(BaseModel):
    role: str | None = None
    is_active: bool | None = None


class UserPasswordResetRequest(BaseModel):
    new_password: str


class StatusResponse(BaseModel):
    service: str
    db: str
    ingestion: str
    time: datetime


class SetupDatabaseSummary(BaseModel):
    host: str = ""
    port: int = 5432
    dbname: str = ""
    user: str = ""
    has_password: bool = False


class SetupStatusResponse(BaseModel):
    setup_required: bool
    setup_completed: bool
    config_exists: bool
    db_configured: bool
    db_reachable: bool
    schema_ready: bool
    admin_exists: bool
    config_path: str
    database: SetupDatabaseSummary
    next_step: str = "setup"
    detail: str | None = None


class SetupDbConfigRequest(BaseModel):
    host: str
    port: int = Field(default=5432, ge=1, le=65535)
    dbname: str
    user: str
    password: str = ""


class SetupDbTestResponse(BaseModel):
    ok: bool
    detail: str
    db_version: str | None = None
    pgvector_installed: bool = False


class SetupInitializeRequest(BaseModel):
    host: str
    port: int = Field(default=5432, ge=1, le=65535)
    dbname: str
    user: str
    password: str = ""
    admin_username: str
    admin_password: str


class SetupInitializeResponse(BaseModel):
    status: str
    config_path: str
    setup_completed: bool
    admin_username: str
    applied_migrations: int = 0
    skipped_migrations: int = 0
    detail: str | None = None


class SearchEvidenceItem(BaseModel):
    track_id: int
    target_key: str | None = None
    similarity: float
    body_sim: float
    upper_sim: float | None = None
    lower_sim: float | None = None
    face_sim: float | None = None
    attr_score: float
    spacetime_score: float
    camera_id: str
    start_time: datetime
    end_time: datetime
    upper_color: str
    lower_color: str
    image_path: str | None = None
    person_bbox: list[int] | None = None
    has_hat: bool | None = None
    image_mode: str | None = None
    is_night: bool | None = None
    quality_score: float | None = None
    pose_hint: str | None = None
    face_used: bool | None = None
    face_available: bool | None = None


class SearchResultItem(BaseModel):
    track_id: int
    target_key: str | None = None
    similarity: float
    body_sim: float
    upper_sim: float | None = None
    lower_sim: float | None = None
    face_sim: float | None = None
    attr_score: float
    spacetime_score: float
    camera_id: str
    start_time: datetime
    end_time: datetime
    upper_color: str
    lower_color: str
    image_path: str | None = None
    person_bbox: list[int] | None = None
    has_hat: bool | None = None
    image_mode: str | None = None
    is_night: bool | None = None
    quality_score: float | None = None
    pose_hint: str | None = None
    face_used: bool | None = None
    face_available: bool | None = None
    evidence_count: int = 0
    evidence: list[SearchEvidenceItem] = Field(default_factory=list)

class SearchResponse(BaseModel):
    query_id: str
    strategy: str | None = None
    count: int
    elapsed_ms: int
    funnel: dict[str, int]
    metrics: dict[str, float]
    timings_ms: dict[str, int] = Field(default_factory=dict)
    timeline: dict[str, int]
    results: list[SearchResultItem] = Field(default_factory=list)


class SearchFeedbackRequest(BaseModel):
    track_id: int
    verdict: str
    note: str | None = None


class SearchHistoryItem(BaseModel):
    query_id: str
    created_by: str
    created_at: datetime
    upper_color: str | None = None
    lower_color: str | None = None
    time_start: datetime | None = None
    time_end: datetime | None = None
    camera_id: str | None = None
    image_mode: str | None = None
    has_hat: bool | None = None
    pose_hint: str | None = None
    min_quality_score: float | None = None
    face_mode: str = "assist"
    group_by_target: bool = True
    diverse_camera: bool = True
    top_k: int = 10
    result_count: int = 0
    elapsed_ms: int = 0
    hit_count: int = 0
    miss_count: int = 0
    latest_feedback_at: datetime | None = None
    funnel: dict[str, int] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)


class SearchHistoryResponse(BaseModel):
    items: list[SearchHistoryItem] = Field(default_factory=list)


class RoiPoint(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class RoiPolygon(BaseModel):
    points: list[RoiPoint] = Field(min_length=3, max_length=64)


class CameraRoiConfigRequest(BaseModel):
    include: list[RoiPolygon] = Field(default_factory=list, max_length=32)
    exclude: list[RoiPolygon] = Field(default_factory=list, max_length=32)


class CameraRoiConfigResponse(BaseModel):
    camera_id: str
    include: list[RoiPolygon] = Field(default_factory=list)
    exclude: list[RoiPolygon] = Field(default_factory=list)
    updated_by: str
    updated_at: datetime | None = None


class CameraRoiTestResponse(BaseModel):
    camera_id: str
    timestamp: datetime
    raw_people_count: int
    filtered_people_count: int
    dropped_count: int
    include_polygon_count: int
    exclude_polygon_count: int


class CameraSourceConfigItem(BaseModel):
    id: str
    name: str
    vendor: str = "custom"
    rtsp_url: str = ""
    event_api_url: str = ""
    snapshot_api_url: str = ""
    host: str = ""
    port: int = 80
    scheme: str = "http"
    username: str = ""
    password: str = ""
    channel_id: int = 1
    enabled: bool = True


class CameraSourceConfigListResponse(BaseModel):
    source_path: str
    items: list[CameraSourceConfigItem] = Field(default_factory=list)


class CameraSourceConfigUpdateRequest(BaseModel):
    items: list[CameraSourceConfigItem] = Field(default_factory=list, max_length=128)


class CaptureRuntimeStatus(BaseModel):
    running: bool
    desired_running: bool = False
    pid: int | None = None
    started_at: datetime | None = None
    last_exit_code: int | None = None
    auto_restart_enabled: bool = False
    restart_pending: bool = False
    restart_count: int = 0
    script_path: str
    config_path: str
    runtime_config_path: str | None = None
    active_camera_id: str | None = None
    active_camera_ids: list[str] = Field(default_factory=list)
    desired_camera_ids: list[str] = Field(default_factory=list)
    pending_camera_ids: list[str] = Field(default_factory=list)
    worker_count: int = 0
    workers: list[dict[str, Any]] = Field(default_factory=list)
    start_errors: list[str] = Field(default_factory=list)
    command: list[str] = Field(default_factory=list)


class CaptureConfigResponse(BaseModel):
    config_path: str
    config: dict[str, Any]


class CaptureConfigUpdateRequest(BaseModel):
    config: dict[str, Any]


class CaptureActionResponse(BaseModel):
    status: CaptureRuntimeStatus


class CaptureLogItem(BaseModel):
    timestamp: datetime
    source: str
    line: str


class CaptureLogsResponse(BaseModel):
    items: list[CaptureLogItem] = Field(default_factory=list)


class CaptureConfigAuditItem(BaseModel):
    timestamp: datetime
    actor: str
    changed_count: int
    changed_paths: list[str] = Field(default_factory=list)


class CaptureConfigAuditResponse(BaseModel):
    items: list[CaptureConfigAuditItem] = Field(default_factory=list)


class CaptureRecentResponse(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


class CaptureDeleteRequest(BaseModel):
    track_ids: list[int] = Field(default_factory=list, max_length=5000)
    image_paths: list[str] = Field(default_factory=list, max_length=5000)
    camera_id: str | None = None
    upper_color: str | None = None
    lower_color: str | None = None
    has_hat: bool | None = None
    image_mode: str | None = None
    is_night: bool | None = None
    pose_hint: str | None = None
    min_quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    time_start: datetime | None = None
    time_end: datetime | None = None
    delete_local_files: bool = True
    dry_run: bool = False


class CaptureDeleteResponse(BaseModel):
    mode: str
    dry_run: bool = False
    matched: int = 0
    deleted: int = 0
    deleted_local_images: int = 0
    deleted_local_sidecars: int = 0


class CaptureModelStatusResponse(BaseModel):
    checked_at: datetime
    yolo_model_path: str
    yolo_model_exists: bool
    reid_capture_mode: str
    reid_capture_model_path: str
    reid_capture_model_exists: bool
    reid_capture_backend: str
    reid_capture_ready: bool
    reid_search_mode: str
    reid_search_model_path: str
    reid_search_model_exists: bool
    reid_search_backend: str
    reid_search_ready: bool


class AdminOpsCheckItem(BaseModel):
    key: str
    label: str
    level: str
    ok: bool
    value: str
    detail: str | None = None


class AdminOpsResponse(BaseModel):
    generated_at: datetime
    checks: list[AdminOpsCheckItem] = Field(default_factory=list)


class AnalyticsTrendPoint(BaseModel):
    label: str
    bucket_start: datetime
    bucket_end: datetime
    value: int


class AnalyticsDistributionItem(BaseModel):
    key: str
    label: str
    value: int
    ratio: float


class AnalyticsTopCameraItem(BaseModel):
    camera_id: str
    label: str
    value: int
    ratio: float


class AnalyticsDashboardResponse(BaseModel):
    generated_at: datetime
    source: str
    range_start: datetime
    range_end: datetime
    granularity: str
    camera_id: str | None = None
    total_count: int
    today_count: int
    range_count: int
    previous_range_count: int
    range_change_ratio: float | None = None
    active_camera_count: int
    trend: list[AnalyticsTrendPoint] = Field(default_factory=list)
    camera_distribution: list[AnalyticsDistributionItem] = Field(default_factory=list)
    mode_distribution: list[AnalyticsDistributionItem] = Field(default_factory=list)
    top_cameras: list[AnalyticsTopCameraItem] = Field(default_factory=list)
    note: str | None = None
