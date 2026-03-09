import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import {
  AUTH_EXPIRED_EVENT,
  changeMyPassword,
  createUser,
  fetchCameraConfigs,
  fetchAuthMe,
  fetchAuthSecurity,
  fetchCaptureConfig,
  fetchCaptureConfigAudit,
  fetchCaptureModelStatus,
  fetchCaptureStatus,
  fetchUsers,
  resetUserPassword,
  saveCaptureConfig,
  updateUser,
} from '../api/client';
import type {
  AuthMeResponse,
  AuthSecurityInfo,
  CameraSourceConfigItem,
  CaptureConfigAuditItem,
  CaptureModelStatus,
  CaptureRuntimeStatus,
  Role,
  UserItem,
} from '../api/types';

interface Props {
  token: string;
  role: string;
}

type FieldType = 'int' | 'float' | 'bool' | 'text';

interface FieldDef {
  path: string[];
  label: string;
  type: FieldType;
  step?: string;
}

interface FieldSection {
  title: string;
  fields: FieldDef[];
}

interface PresetChange {
  path: string[];
  value: number | boolean | string;
}

interface CapturePreset {
  key: string;
  label: string;
  description: string;
  changes: PresetChange[];
}

interface PermissionGroup {
  key: string;
  label: string;
  items: string[];
}

interface JwtPayload {
  exp?: number;
}

const FIELD_HINTS: Record<string, string> = {
  'camera.source_camera_id': '绑定“摄像头配置”中的ID，启动采集时从数据库读取该摄像头连接信息。',
  'camera.burst_count': '单次事件期望保存目标图数量。',
  'camera.max_capture_attempts': '每次事件最多尝试抓拍次数，过小会漏检。',
  'camera.burst_interval_ms': '抓拍两帧之间的等待时间。',
  'camera.cooldown_seconds': '事件结束后冷却时间，避免重复触发。',
  'detector.min_person_confidence': '越低召回越高，误检也会增多。',
  'detector.min_person_area_ratio': '过滤太小的人体框，抑制远处噪点。',
  'quality.min_laplacian_var': '清晰度阈值，过高会丢夜间图。',
  'quality.min_brightness': '过滤过暗图像。',
  'quality.max_brightness': '过滤过曝图像。',
  'quality.min_contrast_std': '过滤低对比度图像。',
  'reliability.same_target_suppress_seconds': '同目标抑制窗口，避免重复入库。',
  'reliability.same_target_embedding_similarity': '同目标 embedding 相似度阈值。',
  'output.save_to_db': '开启后抓拍直接写数据库图片字节。',
  'output.save_local_image': '是否保留本地图片文件。',
};

const CAPTURE_PRESETS: CapturePreset[] = [
  {
    key: 'balanced',
    label: '推荐默认',
    description: '兼顾召回和存储（推荐）。',
    changes: [
      { path: ['camera', 'burst_count'], value: 4 },
      { path: ['camera', 'max_capture_attempts'], value: 36 },
      { path: ['camera', 'cooldown_seconds'], value: 2 },
      { path: ['detector', 'min_person_confidence'], value: 0.2 },
      { path: ['detector', 'min_person_area_ratio'], value: 0.01 },
      { path: ['reliability', 'same_target_suppress_seconds'], value: 120 },
    ],
  },
  {
    key: 'recall',
    label: '召回优先',
    description: '尽量不漏人，存储量会增加。',
    changes: [
      { path: ['camera', 'burst_count'], value: 6 },
      { path: ['camera', 'max_capture_attempts'], value: 56 },
      { path: ['camera', 'cooldown_seconds'], value: 1 },
      { path: ['detector', 'min_person_confidence'], value: 0.15 },
      { path: ['detector', 'min_person_area_ratio'], value: 0.008 },
      { path: ['quality', 'min_laplacian_var'], value: 40 },
      { path: ['reliability', 'same_target_suppress_seconds'], value: 80 },
    ],
  },
  {
    key: 'storage',
    label: '存储优先',
    description: '减少重复抓拍，控制容量增长。',
    changes: [
      { path: ['camera', 'burst_count'], value: 3 },
      { path: ['camera', 'max_capture_attempts'], value: 24 },
      { path: ['camera', 'cooldown_seconds'], value: 3 },
      { path: ['detector', 'min_person_confidence'], value: 0.28 },
      { path: ['detector', 'min_person_area_ratio'], value: 0.015 },
      { path: ['quality', 'min_laplacian_var'], value: 60 },
      { path: ['reliability', 'same_target_suppress_seconds'], value: 240 },
    ],
  },
];

const FIELD_SECTIONS: FieldSection[] = [
  {
    title: '抓拍策略',
    fields: [
      { path: ['camera', 'source_camera_id'], label: '抓拍摄像头ID(来自摄像头配置)', type: 'text' },
      { path: ['camera', 'burst_count'], label: '每事件目标保存张数', type: 'int' },
      { path: ['camera', 'max_capture_attempts'], label: '每事件最大抓拍尝试', type: 'int' },
      { path: ['camera', 'burst_interval_ms'], label: '连拍间隔(ms)', type: 'int' },
      { path: ['camera', 'cooldown_seconds'], label: '事件冷却(s)', type: 'float', step: '0.1' },
    ],
  },
  {
    title: '检测与质量',
    fields: [
      { path: ['detector', 'min_person_confidence'], label: '最小人体置信度', type: 'float', step: '0.01' },
      { path: ['detector', 'min_person_area_ratio'], label: '最小人体面积占比', type: 'float', step: '0.001' },
      { path: ['detector', 'reid_mode'], label: 'ReID模式(auto/onnx/hist)', type: 'text' },
      { path: ['detector', 'reid_model_path'], label: 'ReID模型路径', type: 'text' },
      { path: ['detector', 'reid_input_width'], label: 'ReID输入宽', type: 'int' },
      { path: ['detector', 'reid_input_height'], label: 'ReID输入高', type: 'int' },
      { path: ['quality', 'min_laplacian_var'], label: '最小清晰度(Laplacian)', type: 'float', step: '1' },
      { path: ['quality', 'min_brightness'], label: '最小亮度', type: 'float', step: '1' },
      { path: ['quality', 'max_brightness'], label: '最大亮度', type: 'float', step: '1' },
      { path: ['quality', 'min_contrast_std'], label: '最小对比度', type: 'float', step: '1' },
    ],
  },
  {
    title: '去重与同目标抑制',
    fields: [
      { path: ['dedup', 'hamming_threshold'], label: '事件内去重哈明阈值', type: 'int' },
      { path: ['reliability', 'min_consecutive_vmd_active'], label: '最小连续VMD命中', type: 'int' },
      { path: ['reliability', 'active_window_seconds'], label: 'VMD命中窗口(s)', type: 'float', step: '0.1' },
      { path: ['reliability', 'same_target_suppress_seconds'], label: '同目标跨事件抑制(s)', type: 'float', step: '1' },
      { path: ['reliability', 'same_target_embedding_similarity'], label: '同目标embedding相似度', type: 'float', step: '0.01' },
      { path: ['reliability', 'same_target_hash_hamming_threshold'], label: '同目标哈明阈值', type: 'int' },
      { path: ['reliability', 'same_target_area_ratio_delta'], label: '同目标面积占比差', type: 'float', step: '0.01' },
    ],
  },
  {
    title: '颜色与输出',
    fields: [
      { path: ['color', 'enable_normalization'], label: '启用颜色归一化', type: 'bool' },
      { path: ['color', 'target_brightness'], label: '颜色归一目标亮度', type: 'float', step: '1' },
      { path: ['color', 'night_brightness_threshold'], label: '夜间亮度阈值', type: 'float', step: '1' },
      { path: ['output', 'save_to_db'], label: '直接写入数据库', type: 'bool' },
      { path: ['output', 'save_local_image'], label: '保留本地图片', type: 'bool' },
      { path: ['output', 'local_fallback_on_db_error'], label: 'DB失败回退本地', type: 'bool' },
      { path: ['output', 'save_sidecar_json'], label: '保存 sidecar JSON', type: 'bool' },
      { path: ['output', 'save_metadata_jsonl'], label: '保存 metadata JSONL', type: 'bool' },
      { path: ['logging', 'verbose_events'], label: '输出详细日志', type: 'bool' },
    ],
  },
];

function getPathValue(
  root: Record<string, unknown> | null,
  path: string[],
  fallback: number | boolean | string,
): number | boolean | string {
  if (!root) return fallback;
  let cursor: unknown = root;
  for (const key of path) {
    if (typeof cursor !== 'object' || cursor === null || Array.isArray(cursor)) return fallback;
    cursor = (cursor as Record<string, unknown>)[key];
  }
  if (typeof fallback === 'boolean') return Boolean(cursor);
  if (typeof fallback === 'string') return typeof cursor === 'string' ? cursor : fallback;
  const num = Number(cursor);
  return Number.isFinite(num) ? num : fallback;
}

function setPathValue(
  root: Record<string, unknown>,
  path: string[],
  value: number | boolean | string,
): Record<string, unknown> {
  const next = JSON.parse(JSON.stringify(root)) as Record<string, unknown>;
  let cursor: Record<string, unknown> = next;
  for (let i = 0; i < path.length - 1; i += 1) {
    const key = path[i];
    const current = cursor[key];
    if (typeof current !== 'object' || current === null || Array.isArray(current)) {
      cursor[key] = {};
    }
    cursor = cursor[key] as Record<string, unknown>;
  }
  cursor[path[path.length - 1]] = value;
  return next;
}

function toDateText(value: unknown): string {
  if (typeof value !== 'string' || !value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString();
}

function isLockedUntil(value: string | null | undefined): boolean {
  if (!value) return false;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return false;
  return d.getTime() > Date.now();
}

function getUserLockLabel(user: Pick<UserItem, 'locked_until'>): string {
  if (!user.locked_until) return '未锁定';
  return isLockedUntil(user.locked_until) ? `锁定至 ${toDateText(user.locked_until)}` : `最近锁定 ${toDateText(user.locked_until)}`;
}

const PERMISSION_GROUP_LABELS: Record<string, string> = {
  auth: '登录认证',
  system: '系统状态',
  ingestion: '采集状态',
  search: '检索能力',
  alert: '告警处理',
  audit: '审计查看',
  camera: '摄像头配置',
  capture: '抓拍控制',
  cleanup: '清理维护',
  user: '用户管理',
  config: '系统配置',
};

const PERMISSION_DOMAIN_LABELS: Record<string, string> = {
  camera: 'Camera',
  capture: 'Capture',
  search: 'Search',
  alert: 'Alert',
  system: 'System',
  auth: 'Auth',
  audit: 'Audit',
  user: 'User',
  config: 'Config',
  ingestion: 'Ingestion',
  cleanup: 'Cleanup',
};

const PERMISSION_DOMAIN_ORDER = [
  'camera',
  'capture',
  'search',
  'alert',
  'system',
  'auth',
  'audit',
  'user',
  'config',
  'ingestion',
  'cleanup',
];

function groupPermissions(permissionMatrix: Record<string, string[]>): PermissionGroup[] {
  const unique = new Set<string>();
  Object.values(permissionMatrix).forEach((items) => items.forEach((item) => unique.add(item)));
  const grouped = new Map<string, string[]>();
  [...unique]
    .sort((a, b) => a.localeCompare(b))
    .forEach((permission) => {
      const [prefix] = permission.split(':');
      const key = prefix || 'other';
      const current = grouped.get(key) ?? [];
      current.push(permission);
      grouped.set(key, current);
    });
  return [...grouped.entries()].map(([key, items]) => ({
    key,
    label: PERMISSION_GROUP_LABELS[key] ?? key.toUpperCase(),
    items,
  }));
}

function groupVisiblePermissions(items: string[]): PermissionGroup[] {
  const grouped = new Map<string, string[]>();
  normalizePermissions(items).forEach((permission) => {
    const [prefix] = permission.split(':');
    const key = prefix || 'other';
    const current = grouped.get(key) ?? [];
    current.push(permission);
    grouped.set(key, current);
  });
  return [...grouped.entries()]
    .sort((left, right) => {
      const leftIndex = PERMISSION_DOMAIN_ORDER.indexOf(left[0]);
      const rightIndex = PERMISSION_DOMAIN_ORDER.indexOf(right[0]);
      const safeLeft = leftIndex === -1 ? Number.MAX_SAFE_INTEGER : leftIndex;
      const safeRight = rightIndex === -1 ? Number.MAX_SAFE_INTEGER : rightIndex;
      if (safeLeft !== safeRight) return safeLeft - safeRight;
      return left[0].localeCompare(right[0]);
    })
    .map(([key, values]) => ({
      key,
      label: PERMISSION_DOMAIN_LABELS[key] ?? key.charAt(0).toUpperCase() + key.slice(1),
      items: values,
    }));
}

function decodeJwtPayload(token: string): JwtPayload | null {
  const raw = (token || '').trim();
  if (!raw) return null;
  const parts = raw.split('.');
  if (parts.length < 2) return null;
  try {
    const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, '=');
    const decoded = JSON.parse(atob(padded)) as JwtPayload;
    return decoded;
  } catch {
    return null;
  }
}

function formatDurationLabel(totalMinutes: number | null | undefined): string {
  if (!totalMinutes || totalMinutes <= 0) return '-';
  if (totalMinutes % 60 === 0) return `${totalMinutes / 60}小时`;
  if (totalMinutes >= 60) {
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    return `${hours}小时${minutes}分`;
  }
  return `${totalMinutes}分钟`;
}

function formatRemainingSessionLabel(token: string, fallbackMinutes: number | null | undefined, nowMs: number): string {
  const payload = decodeJwtPayload(token);
  const exp = payload?.exp;
  if (typeof exp === 'number' && Number.isFinite(exp)) {
    const remainMs = exp * 1000 - nowMs;
    if (remainMs <= 0) return '已过期';
    const remainMinutes = Math.max(1, Math.ceil(remainMs / 60000));
    return formatDurationLabel(remainMinutes);
  }
  return formatDurationLabel(fallbackMinutes);
}

function sortRoleNames(roles: string[]): Role[] {
  const order: Role[] = ['admin', 'operator', 'auditor'];
  return order.filter((item) => roles.includes(item));
}

function normalizePermissions(items: string[]): string[] {
  return [...new Set(items)].sort((a, b) => a.localeCompare(b));
}

function arraysEqual(left: string[], right: string[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((item, index) => item === right[index]);
}

function resolveRoleFromPermissions(permissionMatrix: Record<string, string[]>, permissions: string[]): Role | null {
  const normalized = normalizePermissions(permissions);
  const roles = sortRoleNames(Object.keys(permissionMatrix));
  for (const role of roles) {
    const matrixPermissions = normalizePermissions(permissionMatrix[role] ?? []);
    if (arraysEqual(matrixPermissions, normalized)) return role;
  }
  return null;
}

export default function OverviewPage({ token, role }: Props) {
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [loadedConfigSnapshot, setLoadedConfigSnapshot] = useState<Record<string, unknown> | null>(null);
  const [auditItems, setAuditItems] = useState<CaptureConfigAuditItem[]>([]);
  const [modelStatus, setModelStatus] = useState<CaptureModelStatus | null>(null);
  const [captureStatus, setCaptureStatus] = useState<CaptureRuntimeStatus | null>(null);
  const [cameraOptions, setCameraOptions] = useState<CameraSourceConfigItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const canEdit = role === 'admin' || role === 'operator';

  const runtimeAppliedLabel = useMemo(() => {
    if (captureStatus?.worker_count && captureStatus.worker_count > 0) {
      return `已下发到 ${captureStatus.worker_count} 路抓拍进程`;
    }
    if (captureStatus?.desired_running) return '等待抓拍进程启动后下发';
    return '抓拍未启动，保存后下次启动生效';
  }, [captureStatus?.desired_running, captureStatus?.worker_count]);

  const load = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const [cfg, auditResp, modelResp, statusResp] = await Promise.all([
        fetchCaptureConfig(token),
        fetchCaptureConfigAudit(token, 80),
        fetchCaptureModelStatus(token),
        fetchCaptureStatus(token),
      ]);
      setConfig(cfg.config);
      setLoadedConfigSnapshot(cfg.config);
      setAuditItems(auditResp.items);
      setModelStatus(modelResp);
      setCaptureStatus(statusResp);
      try {
        const cams = await fetchCameraConfigs(token);
        setCameraOptions(cams.items);
      } catch {
        // keep previous camera options
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  function handleFieldChange(field: FieldDef, rawValue: string | boolean) {
    if (!config) return;
    if (field.type === 'bool') {
      setConfig((prev) => (prev ? setPathValue(prev, field.path, Boolean(rawValue)) : prev));
      return;
    }
    if (field.type === 'text') {
      setConfig((prev) => (prev ? setPathValue(prev, field.path, String(rawValue)) : prev));
      return;
    }
    const parsed = field.type === 'int' ? Number.parseInt(String(rawValue), 10) : Number.parseFloat(String(rawValue));
    const safe = Number.isFinite(parsed) ? parsed : 0;
    setConfig((prev) => (prev ? setPathValue(prev, field.path, safe) : prev));
  }

  function applyPreset(preset: CapturePreset) {
    if (!config) return;
    let next = JSON.parse(JSON.stringify(config)) as Record<string, unknown>;
    for (const change of preset.changes) {
      next = setPathValue(next, change.path, change.value);
    }
    setConfig(next);
    setMessage(`已应用预设: ${preset.label}`);
  }

  function restoreLoadedSnapshot() {
    if (!loadedConfigSnapshot) return;
    const restored = JSON.parse(JSON.stringify(loadedConfigSnapshot)) as Record<string, unknown>;
    setConfig(restored);
    setMessage('已恢复到最近一次加载/保存的参数');
  }

  async function handleSave() {
    if (!config) return;
    setError(null);
    setMessage(null);
    setSaving(true);
    try {
      const resp = await saveCaptureConfig(token, config);
      setConfig(resp.config);
      setLoadedConfigSnapshot(resp.config);
      const [auditResp, statusResp] = await Promise.all([
        fetchCaptureConfigAudit(token, 80),
        fetchCaptureStatus(token),
      ]);
      setAuditItems(auditResp.items);
      setCaptureStatus(statusResp);
      setMessage('抓拍参数已保存');
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="capture-page">
      <section className="card">
        <div className="capture-runtime-head">
          <h2>抓拍参数</h2>
          <button type="button" onClick={() => void load()} disabled={loading}>
            {loading ? '刷新中...' : '刷新'}
          </button>
        </div>
        <p className="muted">参数通过网页保存到数据库。保存后，重启采集或新启动的抓拍进程会使用新参数。</p>
        <p className="muted">
          当前参数状态: {runtimeAppliedLabel} | 当前运行摄像头:{' '}
          {Array.isArray(captureStatus?.active_camera_ids) && captureStatus.active_camera_ids.length > 0
            ? captureStatus.active_camera_ids.join(', ')
            : captureStatus?.active_camera_id ?? '-'}
        </p>
        <p className="muted">
          模型状态: YOLO={modelStatus?.yolo_model_exists ? 'ok' : 'missing'} | ReID抓拍=
          {modelStatus?.reid_capture_ready ? modelStatus.reid_capture_backend : 'fallback'} | ReID检索=
          {modelStatus?.reid_search_ready ? modelStatus.reid_search_backend : 'fallback'}
        </p>
        <div className="capture-actions">
          <button type="button" onClick={() => void handleSave()} disabled={!canEdit || saving || !config}>
            {saving ? '保存中...' : '保存参数'}
          </button>
          <button type="button" className="ghost-btn" onClick={restoreLoadedSnapshot} disabled={!loadedConfigSnapshot}>
            撤销未保存
          </button>
        </div>
        {!canEdit ? <p className="muted">当前角色为只读，无法保存参数。</p> : null}
        {message ? <p>{message}</p> : null}
        {error ? <p className="error">{error}</p> : null}
      </section>

      <section className="card capture-config-card">
        <div className="capture-runtime-head">
          <h3>参数面板</h3>
          <span className="muted">建议先用预设，再微调</span>
        </div>
        <div className="capture-preset-row">
          {CAPTURE_PRESETS.map((preset) => (
            <button
              key={preset.key}
              type="button"
              className="ghost-btn"
              onClick={() => applyPreset(preset)}
              disabled={!canEdit || !config}
              title={preset.description}
            >
              {preset.label}
            </button>
          ))}
        </div>
        <p className="muted">预设说明: {CAPTURE_PRESETS.map((item) => `${item.label}=${item.description}`).join(' ')}</p>
        {!config ? (
          <p className="muted">配置加载中...</p>
        ) : (
          <div className="capture-config-grid">
            {FIELD_SECTIONS.map((section) => (
              <fieldset className="capture-fieldset" key={section.title}>
                <legend>{section.title}</legend>
                {section.fields.map((field) => {
                  const fallback = field.type === 'bool' ? false : field.type === 'text' ? '' : 0;
                  const value = getPathValue(config, field.path, fallback);
                  const key = field.path.join('.');
                  const hint = FIELD_HINTS[key];

                  if (key === 'camera.source_camera_id') {
                    return (
                      <label key={key}>
                        {field.label}
                        {hint ? <p className="capture-field-hint">{hint}</p> : null}
                        <select
                          value={String(value)}
                          onChange={(e) => handleFieldChange(field, e.target.value)}
                          disabled={!canEdit}
                        >
                          <option value="">按采集控制台启动方式决定</option>
                          {cameraOptions.map((cam) => (
                            <option key={cam.id} value={cam.id}>
                              {cam.name} ({cam.id})
                            </option>
                          ))}
                        </select>
                      </label>
                    );
                  }

                  if (field.type === 'bool') {
                    return (
                      <label key={key} className="capture-checkbox">
                        <input
                          type="checkbox"
                          checked={Boolean(value)}
                          onChange={(e) => handleFieldChange(field, e.target.checked)}
                          disabled={!canEdit}
                        />
                        <div>
                          <span>{field.label}</span>
                          {hint ? <p className="capture-field-hint">{hint}</p> : null}
                        </div>
                      </label>
                    );
                  }

                  if (field.type === 'text') {
                    return (
                      <label key={key}>
                        {field.label}
                        {hint ? <p className="capture-field-hint">{hint}</p> : null}
                        <input
                          type="text"
                          value={String(value)}
                          onChange={(e) => handleFieldChange(field, e.target.value)}
                          disabled={!canEdit}
                        />
                      </label>
                    );
                  }

                  return (
                    <label key={key}>
                      {field.label}
                      {hint ? <p className="capture-field-hint">{hint}</p> : null}
                      <input
                        type="number"
                        value={String(value)}
                        step={field.step ?? '1'}
                        onChange={(e) => handleFieldChange(field, e.target.value)}
                        disabled={!canEdit}
                      />
                    </label>
                  );
                })}
              </fieldset>
            ))}
          </div>
        )}
      </section>

      <section className="card capture-config-audit-card">
        <h3>参数变更审计</h3>
        {auditItems.length === 0 ? (
          <p className="muted">暂无配置变更记录</p>
        ) : (
          <div className="capture-audit-list">
            {auditItems.slice(0, 12).map((item, idx) => (
              <article key={`${item.timestamp}-${idx}`} className="capture-audit-item">
                <p>
                  <strong>{toDateText(item.timestamp)}</strong> · {item.actor} · 变更 {item.changed_count} 项
                </p>
                <p className="muted">{(item.changed_paths || []).slice(0, 8).join(', ') || '-'}</p>
              </article>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}

export function SettingsPage({ token, role }: Props) {
  const [me, setMe] = useState<AuthMeResponse | null>(null);
  const [security, setSecurity] = useState<AuthSecurityInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [passwordLoading, setPasswordLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [showSecurityPolicy, setShowSecurityPolicy] = useState(false);
  const [permissionFilter, setPermissionFilter] = useState('all');
  const [nowMs, setNowMs] = useState(() => Date.now());
  const isAdmin = role === 'admin';

  const sessionRemainingLabel = useMemo(
    () => formatRemainingSessionLabel(token, security?.token_expire_minutes, nowMs),
    [nowMs, security?.token_expire_minutes, token],
  );
  const securityDisabled = passwordLoading || !me?.is_active;
  const visiblePermissionGroups = useMemo(() => groupVisiblePermissions(me?.permissions ?? []), [me?.permissions]);
  const filteredPermissionGroups = useMemo(() => {
    if (permissionFilter === 'all') return visiblePermissionGroups;
    return visiblePermissionGroups.filter((group) => group.key === permissionFilter);
  }, [permissionFilter, visiblePermissionGroups]);
  const accountIdentityMeta = useMemo(() => {
    const items: string[] = [];
    if (me?.user_id) items.push(`账号ID ${me.user_id}`);
    if (me?.created_at) items.push(`创建于 ${toDateText(me.created_at)}`);
    return items.join(' · ') || '数据库账号';
  }, [me?.created_at, me?.user_id]);
  const accountActivityMeta = useMemo(() => {
    const items: string[] = [];
    if (me?.last_login_at) items.push(`上次登录 ${toDateText(me.last_login_at)}`);
    if (me?.password_updated_at) items.push(`密码更新 ${toDateText(me.password_updated_at)}`);
    return items.join(' · ') || '当前账号已接入数据库认证';
  }, [me?.last_login_at, me?.password_updated_at]);

  const load = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const [meResp, securityResp] = await Promise.all([fetchAuthMe(token), fetchAuthSecurity(token)]);
      setMe(meResp);
      setSecurity(securityResp);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 60000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (permissionFilter === 'all') return;
    if (!visiblePermissionGroups.some((group) => group.key === permissionFilter)) {
      setPermissionFilter('all');
    }
  }, [permissionFilter, visiblePermissionGroups]);

  async function handleChangePassword() {
    setError(null);
    setMessage(null);
    setPasswordLoading(true);
    try {
      await changeMyPassword(token, currentPassword, newPassword);
      setCurrentPassword('');
      setNewPassword('');
      setMessage('密码已更新，请重新登录。');
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPasswordLoading(false);
    }
  }

  function handleContactAdmin() {
    setError(null);
    setMessage('如需重置密码、调整角色或解锁账号，请联系管理员处理。');
  }

  return (
    <main className="settings-page">
      <section className="card settings-account-card">
        <div className="settings-block-head">
          <div>
            <h3>① 账户概览</h3>
            <p className="muted">头像、用户名、角色和当前账号状态。</p>
          </div>
          <div className="settings-hero-actions">
            {isAdmin ? (
              <Link to="/user-admin" className="ghost-btn settings-link-btn">
                用户管理
              </Link>
            ) : null}
            <button type="button" onClick={() => void load()} disabled={loading}>
              {loading ? '刷新中...' : '刷新'}
            </button>
          </div>
        </div>
        <div className="settings-account-shell">
          <div className="settings-account-identity">
            <div className="settings-avatar">{(me?.username?.[0] ?? role?.[0] ?? 'U').toUpperCase()}</div>
            <div className="settings-account-copy">
              <strong>{me?.username ?? '-'}</strong>
              <p className="muted">{accountIdentityMeta}</p>
              <p className="muted">{accountActivityMeta}</p>
              <div className="settings-badges">
                <span className="settings-badge">{me?.role ?? role}</span>
                <span className={me?.is_active ? 'settings-badge success' : 'settings-badge warning'}>
                  {me?.is_active ? '已启用' : '已停用'}
                </span>
                <span className="settings-badge success">数据库托管</span>
              </div>
            </div>
          </div>
          <div className="settings-account-metrics">
            <article className="settings-overview-chip">
              <span>权限项</span>
              <strong>{me?.permissions.length ?? 0}</strong>
            </article>
            <article className="settings-overview-chip">
              <span>会话剩余</span>
              <strong>{sessionRemainingLabel}</strong>
            </article>
            <article className="settings-overview-chip">
              <span>认证方式</span>
              <strong>{security?.auth_mode === 'db_only' ? '数据库账号' : '数据库优先'}</strong>
            </article>
            <article className="settings-overview-chip">
              <span>托管状态</span>
              <strong>{me?.managed_by_db ? '已托管' : '未接入'}</strong>
            </article>
          </div>
        </div>
        {error ? <p className="error">{error}</p> : null}
        {message ? <p className="settings-help-text">{message}</p> : null}
      </section>

      <section className="card settings-security-card">
        <div className="settings-block-head">
          <div>
            <h3>② 账号安全</h3>
            <p className="muted">密码修改入口和当前安全策略。</p>
          </div>
        </div>
        {me?.must_change_password ? <div className="settings-inline-alert">当前密码由管理员重置，请尽快修改为个人密码。</div> : null}
        <div className="settings-security-metrics">
          <div className="settings-security-metric">
            <span className="muted">会话有效期</span>
            <strong>{formatDurationLabel(security?.token_expire_minutes)}</strong>
          </div>
          <div className="settings-security-metric">
            <span className="muted">密码最短长度</span>
            <strong>{security?.password_min_length ? `${security.password_min_length} 位` : '-'}</strong>
          </div>
          <div className="settings-security-metric">
            <span className="muted">上次登录</span>
            <strong>{toDateText(me?.last_login_at)}</strong>
          </div>
          <div className="settings-security-metric">
            <span className="muted">失败锁定</span>
            <strong>
              {security?.max_failed_login_attempts
                ? `${security.max_failed_login_attempts} 次 / ${security.account_lock_minutes} 分钟`
                : '-'}
            </strong>
          </div>
        </div>
        <div className="settings-password-panel">
          <div className="settings-password-grid">
            <label>
              当前密码
              <input
                type="password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                disabled={securityDisabled}
                placeholder="输入当前密码"
              />
            </label>
            <label>
              新密码
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                disabled={securityDisabled}
                placeholder="输入新密码"
              />
            </label>
          </div>
          <div className="settings-inline-actions settings-security-actions">
            <button
              type="button"
              onClick={() => void handleChangePassword()}
              disabled={passwordLoading || securityDisabled || !currentPassword || !newPassword}
            >
              {passwordLoading ? '提交中...' : '修改密码'}
            </button>
            <button type="button" className="ghost-btn" onClick={handleContactAdmin}>
              联系管理员
            </button>
            <button type="button" className="ghost-btn" onClick={() => setShowSecurityPolicy((prev) => !prev)}>
              {showSecurityPolicy ? '收起安全策略' : '查看安全策略'}
            </button>
          </div>
          {showSecurityPolicy ? (
            <div className="settings-policy-panel">
              <div className="settings-policy-item">
                <span>会话签名算法</span>
                <strong>{security?.jwt_algorithm ?? '-'}</strong>
              </div>
              <div className="settings-policy-item">
                <span>角色模板</span>
                <strong>{(security?.roles ?? []).join(' / ') || '-'}</strong>
              </div>
              <div className="settings-policy-item">
                <span>认证模式</span>
                <strong>{security?.auth_mode === 'db_only' ? '仅数据库账号' : security?.auth_mode ?? '-'}</strong>
              </div>
              <div className="settings-policy-item">
                <span>在线改密条件</span>
                <strong>数据库托管且账号启用</strong>
              </div>
              <div className="settings-policy-item">
                <span>账号锁定策略</span>
                <strong>
                  {security?.max_failed_login_attempts
                    ? `连续失败 ${security.max_failed_login_attempts} 次锁定 ${security.account_lock_minutes} 分钟`
                    : '-'}
                </strong>
              </div>
            </div>
          ) : null}
        </div>
      </section>

      <section id="settings-permissions" className="card settings-permission-card">
        <div className="settings-block-head">
          <div>
            <h3>③ 权限范围</h3>
            <p className="muted">按模块查看当前角色可操作的系统权限。</p>
          </div>
        </div>
        <div className="settings-permission-filters">
          <button
            type="button"
            className={permissionFilter === 'all' ? 'settings-filter-pill active' : 'settings-filter-pill'}
            onClick={() => setPermissionFilter('all')}
          >
            全部
          </button>
          {visiblePermissionGroups.map((group) => (
            <button
              key={group.key}
              type="button"
              className={permissionFilter === group.key ? 'settings-filter-pill active' : 'settings-filter-pill'}
              onClick={() => setPermissionFilter(group.key)}
            >
              {group.label}
            </button>
          ))}
        </div>
        {filteredPermissionGroups.length === 0 ? (
          <p className="muted">当前筛选条件下暂无可见权限。</p>
        ) : (
          <div className="settings-permission-groups-view">
            {filteredPermissionGroups.map((group) => (
              <article key={group.key} className="settings-permission-domain">
                <h4>{group.label}</h4>
                <div className="settings-permission-token-grid">
                  {group.items.map((permission) => (
                    <span key={permission} className="settings-permission-token">
                      {permission}
                    </span>
                  ))}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}

export function UserAdminPage({ token, role }: Props) {
  const [security, setSecurity] = useState<AuthSecurityInfo | null>(null);
  const [users, setUsers] = useState<UserItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [userLoading, setUserLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [createUsername, setCreateUsername] = useState('');
  const [createPassword, setCreatePassword] = useState('');
  const [createRole, setCreateRole] = useState<Role>('operator');
  const [createActive, setCreateActive] = useState(true);
  const [roleDrafts, setRoleDrafts] = useState<Record<string, Role>>({});
  const [activeDrafts, setActiveDrafts] = useState<Record<string, boolean>>({});
  const [permissionDrafts, setPermissionDrafts] = useState<Record<string, string[]>>({});
  const [resetPasswordDrafts, setResetPasswordDrafts] = useState<Record<string, string>>({});
  const [selectedUsername, setSelectedUsername] = useState<string>('');
  const [searchTerm, setSearchTerm] = useState('');
  const [roleFilter, setRoleFilter] = useState<'all' | Role>('all');
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'inactive'>('all');
  const [createPanelOpen, setCreatePanelOpen] = useState(false);
  const isAdmin = role === 'admin';

  const syncUserDrafts = useCallback((items: UserItem[]) => {
    const nextRoles: Record<string, Role> = {};
    const nextActive: Record<string, boolean> = {};
    const nextPermissions: Record<string, string[]> = {};
    for (const item of items) {
      nextRoles[item.username] = item.role;
      nextActive[item.username] = item.is_active;
      nextPermissions[item.username] = normalizePermissions(item.permissions);
    }
    setRoleDrafts(nextRoles);
    setActiveDrafts(nextActive);
    setPermissionDrafts(nextPermissions);
    setSelectedUsername((prev) => (prev && items.some((item) => item.username === prev) ? prev : items[0]?.username ?? ''));
  }, []);

  const load = useCallback(async () => {
    if (!isAdmin) return;
    setError(null);
    setLoading(true);
    try {
      const [securityResp, userResp] = await Promise.all([fetchAuthSecurity(token), fetchUsers(token)]);
      setSecurity(securityResp);
      setUsers(userResp.items);
      syncUserDrafts(userResp.items);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [isAdmin, syncUserDrafts, token]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleCreateUser() {
    setError(null);
    setMessage(null);
    setUserLoading(true);
    try {
      await createUser(token, {
        username: createUsername,
        password: createPassword,
        role: createRole,
        is_active: createActive,
      });
      setCreateUsername('');
      setCreatePassword('');
      setCreateRole('operator');
      setCreateActive(true);
      const userResp = await fetchUsers(token);
      setUsers(userResp.items);
      syncUserDrafts(userResp.items);
      setSelectedUsername(createUsername.trim().toLowerCase());
      setMessage('用户已创建');
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setUserLoading(false);
    }
  }

  async function handleSaveUser(username: string) {
    setError(null);
    setMessage(null);
    setUserLoading(true);
    try {
      const nextRole = resolveRoleFromPermissions(security?.permission_matrix ?? {}, permissionDrafts[username] ?? []);
      if (!nextRole) {
        setError('当前勾选的权限组合不对应现有角色模板，无法保存');
        return;
      }
      const nextActive = activeDrafts[username];
      const updated = await updateUser(token, username, {
        role: nextRole,
        is_active: nextActive,
      });
      const nextItems = users.map((item) => (item.username === username ? updated : item));
      setUsers(nextItems);
      syncUserDrafts(nextItems);
      setMessage(`用户 ${username} 已更新`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setUserLoading(false);
    }
  }

  function applyRoleTemplate(username: string, nextRole: Role) {
    const nextPermissions = normalizePermissions(security?.permission_matrix?.[nextRole] ?? []);
    setRoleDrafts((prev) => ({ ...prev, [username]: nextRole }));
    setPermissionDrafts((prev) => ({ ...prev, [username]: nextPermissions }));
  }

  function togglePermission(username: string, permission: string, checked: boolean) {
    setPermissionDrafts((prev) => {
      const current = new Set(prev[username] ?? []);
      if (checked) current.add(permission);
      else current.delete(permission);
      const nextPermissions = normalizePermissions([...current]);
      const matchedRole = resolveRoleFromPermissions(security?.permission_matrix ?? {}, nextPermissions);
      if (matchedRole) {
        setRoleDrafts((prevRoles) => ({ ...prevRoles, [username]: matchedRole }));
      }
      return { ...prev, [username]: nextPermissions };
    });
  }

  async function handleResetUserPassword(username: string) {
    const nextPassword = (resetPasswordDrafts[username] || '').trim();
    if (!nextPassword) {
      setError('请输入新密码');
      return;
    }
    setError(null);
    setMessage(null);
    setUserLoading(true);
    try {
      await resetUserPassword(token, username, nextPassword);
      setResetPasswordDrafts((prev) => ({ ...prev, [username]: '' }));
      const userResp = await fetchUsers(token);
      setUsers(userResp.items);
      syncUserDrafts(userResp.items);
      setMessage(`用户 ${username} 的密码已重置`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setUserLoading(false);
    }
  }

  if (!isAdmin) {
    return (
      <main className="settings-page">
        <section className="card">
          <div className="overview-head">
            <h2>用户管理</h2>
          </div>
          <p className="muted">当前角色无权访问管理员页面。</p>
        </section>
      </main>
    );
  }

  const activeUserCount = users.filter((item) => item.is_active).length;
  const adminUserCount = users.filter((item) => item.role === 'admin').length;
  const lockedUserCount = users.filter((item) => isLockedUntil(item.locked_until)).length;
  const mustChangePasswordCount = users.filter((item) => item.must_change_password).length;
  const filteredUsers = useMemo(() => {
    const keyword = searchTerm.trim().toLowerCase();
    return users.filter((item) => {
      if (roleFilter !== 'all' && item.role !== roleFilter) return false;
      if (statusFilter === 'active' && !(activeDrafts[item.username] ?? item.is_active)) return false;
      if (statusFilter === 'inactive' && (activeDrafts[item.username] ?? item.is_active)) return false;
      if (!keyword) return true;
      return item.username.toLowerCase().includes(keyword);
    });
  }, [activeDrafts, roleFilter, searchTerm, statusFilter, users]);
  const selectedUser = filteredUsers.find((item) => item.username === selectedUsername) ?? filteredUsers[0] ?? null;
  const selectedPermissions = selectedUser ? permissionDrafts[selectedUser.username] ?? [] : [];
  const resolvedRole = selectedUser
    ? resolveRoleFromPermissions(security?.permission_matrix ?? {}, selectedPermissions)
    : null;
  const permissionGroups = groupPermissions(security?.permission_matrix ?? {});
  const availableRoles = sortRoleNames(security?.roles ?? []);

  return (
    <main className="settings-page">
      <section className="card settings-admin-header-card">
        <div className="settings-admin-toolbar">
          <div>
            <h2>全部用户 {users.length}</h2>
            <p className="muted">按账号筛选后点击用户，再在右侧调整角色和权限。</p>
          </div>
          <div className="settings-admin-toolbar-actions">
            <label className="settings-admin-search">
              <input placeholder="搜索用户" value={searchTerm} onChange={(e) => setSearchTerm(e.target.value)} />
            </label>
            <select value={roleFilter} onChange={(e) => setRoleFilter(e.target.value as 'all' | Role)}>
              <option value="all">全部角色</option>
              {availableRoles.map((itemRole) => (
                <option key={itemRole} value={itemRole}>
                  {itemRole}
                </option>
              ))}
            </select>
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as 'all' | 'active' | 'inactive')}>
              <option value="all">全部状态</option>
              <option value="active">已启用</option>
              <option value="inactive">已停用</option>
            </select>
            <button type="button" className="ghost-btn" onClick={() => void load()} disabled={loading}>
              {loading ? '刷新中...' : '刷新'}
            </button>
            <button type="button" onClick={() => setCreatePanelOpen((prev) => !prev)}>
              {createPanelOpen ? '收起新增' : '新增用户'}
            </button>
          </div>
        </div>
        <div className="settings-summary-grid">
          <div className="capture-stat">
            <p className="muted">用户总数</p>
            <p>{users.length}</p>
          </div>
          <div className="capture-stat">
            <p className="muted">启用用户</p>
            <p>{activeUserCount}</p>
          </div>
          <div className="capture-stat">
            <p className="muted">管理员</p>
            <p>{adminUserCount}</p>
          </div>
          <div className="capture-stat">
            <p className="muted">锁定中账号</p>
            <p>{lockedUserCount}</p>
          </div>
          <div className="capture-stat">
            <p className="muted">待改密账号</p>
            <p>{mustChangePasswordCount}</p>
          </div>
          <div className="capture-stat">
            <p className="muted">密码最短长度</p>
            <p>{security?.password_min_length ?? '-'}</p>
          </div>
        </div>
        {createPanelOpen ? (
          <div className="settings-admin-create-panel">
            <div className="settings-section-head">
              <h3>新增用户</h3>
              <span className="muted">创建后可在右侧继续提权或降权</span>
            </div>
            <div className="settings-user-create-grid">
              <label>
                用户名
                <input value={createUsername} onChange={(e) => setCreateUsername(e.target.value)} />
              </label>
              <label>
                初始密码
                <input type="password" value={createPassword} onChange={(e) => setCreatePassword(e.target.value)} />
              </label>
              <label>
                角色模板
                <select value={createRole} onChange={(e) => setCreateRole(e.target.value as Role)}>
                  {availableRoles.map((itemRole) => (
                    <option key={itemRole} value={itemRole}>
                      {itemRole}
                    </option>
                  ))}
                </select>
              </label>
              <label className="capture-checkbox">
                <input type="checkbox" checked={createActive} onChange={(e) => setCreateActive(e.target.checked)} />
                <span>创建后立即启用</span>
              </label>
            </div>
            <div className="settings-inline-actions">
              <button type="button" onClick={() => void handleCreateUser()} disabled={userLoading}>
                {userLoading ? '处理中...' : '确认新增'}
              </button>
              <button type="button" className="ghost-btn" onClick={() => setCreatePanelOpen(false)}>
                取消
              </button>
            </div>
          </div>
        ) : null}
        {error ? <p className="error">{error}</p> : null}
        {message ? <p>{message}</p> : null}
      </section>

      <section className="settings-admin-console">
        <div className="card settings-admin-list-card">
          <div className="settings-admin-list-head">
            <span>显示 {filteredUsers.length} / {users.length} 个用户</span>
            <span className="muted">点击行查看权限与密码操作</span>
          </div>
          <div className="settings-admin-table">
            <div className="settings-admin-table-head">
              <span>用户</span>
              <span>访问级别</span>
              <span>状态</span>
              <span>最近活动</span>
            </div>
            {filteredUsers.length === 0 ? (
              <div className="settings-admin-empty">没有匹配到用户</div>
            ) : (
              filteredUsers.map((item) => {
                const itemPermissions = permissionDrafts[item.username] ?? item.permissions ?? [];
                const itemRole = resolveRoleFromPermissions(security?.permission_matrix ?? {}, itemPermissions) ?? roleDrafts[item.username] ?? item.role;
                const enabled = activeDrafts[item.username] ?? item.is_active;
                return (
                  <button
                    key={item.user_id}
                    type="button"
                    className={selectedUser?.username === item.username ? 'settings-admin-row active' : 'settings-admin-row'}
                    onClick={() => setSelectedUsername(item.username)}
                  >
                    <span className="settings-admin-usercell">
                      <span className="settings-admin-avatar">{item.username.slice(0, 1).toUpperCase()}</span>
                      <span>
                        <strong>{item.username}</strong>
                        <small>
                          {itemPermissions.length} 项权限
                          {item.must_change_password ? ' · 待改密' : ''}
                          {isLockedUntil(item.locked_until) ? ' · 已锁定' : ''}
                        </small>
                      </span>
                    </span>
                    <span className="settings-admin-access">
                      <span className="settings-admin-pill role">{itemRole}</span>
                      {itemRole === 'admin' ? <span className="settings-admin-pill accent">全量</span> : null}
                      {item.must_change_password ? <span className="settings-admin-pill warning">待改密</span> : null}
                    </span>
                    <span className={enabled ? 'settings-admin-status on' : 'settings-admin-status off'}>
                      {enabled ? '已启用' : '已停用'}
                      {isLockedUntil(item.locked_until) ? ' / 已锁定' : ''}
                    </span>
                    <span className="settings-admin-date">
                      {toDateText(item.last_login_at) !== '-' ? toDateText(item.last_login_at) : toDateText(item.created_at)}
                    </span>
                  </button>
                );
              })
            )}
          </div>
        </div>

        <aside className="card settings-admin-detail-card">
          {!selectedUser ? (
            <p className="muted">请选择左侧用户。</p>
          ) : (
            <>
              <div className="settings-admin-detail-head">
                <div>
                  <h3>{selectedUser.username}</h3>
                  <p className="muted">
                    创建时间 {toDateText(selectedUser.created_at)} · 当前模板 {resolvedRole ?? '未匹配角色'}
                  </p>
                  <div className="settings-admin-detail-badges">
                    <span className="settings-admin-pill role">{roleDrafts[selectedUser.username] ?? selectedUser.role}</span>
                    <span className={(activeDrafts[selectedUser.username] ?? selectedUser.is_active) ? 'settings-admin-pill success' : 'settings-admin-pill warning'}>
                      {(activeDrafts[selectedUser.username] ?? selectedUser.is_active) ? '已启用' : '已停用'}
                    </span>
                    {selectedUser.must_change_password ? <span className="settings-admin-pill warning">下次登录需改密</span> : null}
                    {isLockedUntil(selectedUser.locked_until) ? <span className="settings-admin-pill warning">账号锁定中</span> : null}
                  </div>
                </div>
                <label className="capture-checkbox">
                  <input
                    type="checkbox"
                    checked={activeDrafts[selectedUser.username] ?? selectedUser.is_active}
                    onChange={(e) =>
                      setActiveDrafts((prev) => ({
                        ...prev,
                        [selectedUser.username]: e.target.checked,
                      }))
                    }
                  />
                  <span>{(activeDrafts[selectedUser.username] ?? selectedUser.is_active) ? '启用' : '停用'}</span>
                </label>
              </div>

              <div className="settings-admin-meta-grid">
                <article className="settings-admin-meta-card">
                  <span>账号ID</span>
                  <strong>{selectedUser.user_id}</strong>
                </article>
                <article className="settings-admin-meta-card">
                  <span>创建时间</span>
                  <strong>{toDateText(selectedUser.created_at)}</strong>
                </article>
                <article className="settings-admin-meta-card">
                  <span>上次登录</span>
                  <strong>{toDateText(selectedUser.last_login_at)}</strong>
                </article>
                <article className="settings-admin-meta-card">
                  <span>密码更新</span>
                  <strong>{toDateText(selectedUser.password_updated_at)}</strong>
                </article>
                <article className="settings-admin-meta-card">
                  <span>失败次数</span>
                  <strong>{selectedUser.failed_login_count}</strong>
                </article>
                <article className="settings-admin-meta-card">
                  <span>锁定状态</span>
                  <strong>{getUserLockLabel(selectedUser)}</strong>
                </article>
              </div>

              <div className="settings-role-template-row">
                {availableRoles.map((itemRole) => (
                  <button
                    key={itemRole}
                    type="button"
                    className={(roleDrafts[selectedUser.username] ?? selectedUser.role) === itemRole ? 'settings-role-card active' : 'settings-role-card'}
                    onClick={() => applyRoleTemplate(selectedUser.username, itemRole)}
                  >
                    <strong>{itemRole}</strong>
                    <span>{(security?.permission_matrix?.[itemRole] ?? []).length} 项权限</span>
                  </button>
                ))}
              </div>

              <div className="settings-permission-groups">
                {permissionGroups.map((group) => (
                  <article key={group.key} className="settings-permission-group">
                    <h4>{group.label}</h4>
                    <div className="settings-permission-checklist">
                      {group.items.map((permission) => {
                        const checked = selectedPermissions.includes(permission);
                        return (
                          <label key={permission} className="settings-permission-option">
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(e) => togglePermission(selectedUser.username, permission, e.target.checked)}
                            />
                            <span>{permission}</span>
                          </label>
                        );
                      })}
                    </div>
                  </article>
                ))}
              </div>

              {!resolvedRole ? (
                <p className="settings-warning">
                  当前勾选的权限组合不对应现有角色模板，无法保存。请调整为 `admin` / `operator` / `auditor` 标准组合。
                </p>
              ) : null}

              <div className="settings-detail-actions">
                <label>
                  重置密码
                  <input
                    type="password"
                    placeholder="输入新密码"
                    value={resetPasswordDrafts[selectedUser.username] ?? ''}
                    onChange={(e) =>
                      setResetPasswordDrafts((prev) => ({
                        ...prev,
                        [selectedUser.username]: e.target.value,
                      }))
                    }
                  />
                  <small className="muted">重置后该账号下次登录必须先修改密码。</small>
                </label>
                <div className="settings-inline-actions">
                  <button
                    type="button"
                    className="ghost-btn"
                    onClick={() => void handleSaveUser(selectedUser.username)}
                    disabled={userLoading || !resolvedRole}
                  >
                    保存权限
                  </button>
                  <button type="button" onClick={() => void handleResetUserPassword(selectedUser.username)} disabled={userLoading}>
                    重置密码
                  </button>
                </div>
              </div>
            </>
          )}
        </aside>
      </section>
    </main>
  );
}
