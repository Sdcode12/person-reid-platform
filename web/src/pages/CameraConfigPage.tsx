import { useCallback, useEffect, useMemo, useState } from 'react';

import {
  fetchCameraConfigs,
  fetchCameras,
  fetchCaptureStatus,
  restartCapture,
  saveCameraConfigs,
  startCapture,
  stopCapture,
  testCamera,
} from '../api/client';
import type { CameraSourceConfigItem, CameraStatusItem, CaptureRuntimeStatus } from '../api/types';

interface Props {
  token: string;
  role: string;
}

interface VendorPreset {
  key: string;
  label: string;
  defaultScheme: 'http' | 'https';
  defaultPort: number;
  eventPathTemplate: string;
  snapshotPathTemplate: string;
}

const VENDOR_PRESETS: VendorPreset[] = [
  {
    key: 'hikvision_isapi',
    label: '海康 ISAPI',
    defaultScheme: 'http',
    defaultPort: 80,
    eventPathTemplate: '/ISAPI/Event/notification/alertStream',
    snapshotPathTemplate: '/ISAPI/Streaming/channels/{channel_no}/picture',
  },
  {
    key: 'dahua_cgi',
    label: '大华 CGI',
    defaultScheme: 'http',
    defaultPort: 80,
    eventPathTemplate: '/cgi-bin/eventManager.cgi?action=attach&codes=[VideoMotion]',
    snapshotPathTemplate: '/cgi-bin/snapshot.cgi?channel={channel_index}',
  },
  {
    key: 'custom',
    label: '自定义协议',
    defaultScheme: 'http',
    defaultPort: 80,
    eventPathTemplate: '',
    snapshotPathTemplate: '',
  },
];

const PRESET_BY_KEY = new Map<string, VendorPreset>(VENDOR_PRESETS.map((item) => [item.key, item]));

function getPreset(vendor: string): VendorPreset {
  return PRESET_BY_KEY.get(vendor) ?? PRESET_BY_KEY.get('custom')!;
}

function createEmptyCamera(index: number): CameraSourceConfigItem {
  return {
    id: `camera_${String(index).padStart(2, '0')}`,
    name: `摄像头${index}`,
    vendor: 'hikvision_isapi',
    event_api_url: '',
    snapshot_api_url: '',
    host: '',
    port: 80,
    scheme: 'http',
    username: '',
    password: '',
    channel_id: 1,
    rtsp_url: '',
    enabled: true,
  };
}

function buildRtspUrl(item: Pick<CameraSourceConfigItem, 'host' | 'username' | 'password' | 'channel_id'>): string {
  const host = item.host.trim();
  const username = item.username.trim();
  const password = item.password;
  const channel = Number.isFinite(item.channel_id) ? Math.max(1, Math.trunc(item.channel_id)) : 1;
  if (!host || !username || !password) return '';
  const channelNo = channel * 100 + 1;
  return `rtsp://${encodeURIComponent(username)}:${encodeURIComponent(password)}@${host}:554/Streaming/Channels/${channelNo}`;
}

function toDateTimeText(v: string | null): string {
  if (!v) return '-';
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v;
  return d.toLocaleString();
}

function buildTemplatePath(pathTemplate: string, channelId: number): string {
  const normalizedChannel = Math.max(1, Math.trunc(channelId || 1));
  const channelNo = normalizedChannel * 100 + 1;
  const channelIndex = Math.max(0, normalizedChannel - 1);
  return pathTemplate
    .replace(/\{channel_no\}/g, String(channelNo))
    .replace(/\{channel_id\}/g, String(normalizedChannel))
    .replace(/\{channel_index\}/g, String(channelIndex));
}

function buildApiUrl(
  scheme: string,
  host: string,
  port: number,
  pathTemplate: string,
  channelId: number,
): string {
  const hostText = host.trim();
  if (!hostText || !pathTemplate.trim()) return '';
  const protocol = scheme.trim() || 'http';
  const normalizedPort = Number.isFinite(port) ? Math.max(1, Math.trunc(port)) : 80;
  const path = buildTemplatePath(pathTemplate, channelId);
  return `${protocol}://${hostText}:${normalizedPort}${path}`;
}

function applyPresetUrls(item: CameraSourceConfigItem): Pick<CameraSourceConfigItem, 'event_api_url' | 'snapshot_api_url'> {
  const preset = getPreset(item.vendor);
  if (preset.key === 'custom') {
    return {
      event_api_url: item.event_api_url.trim(),
      snapshot_api_url: item.snapshot_api_url.trim(),
    };
  }
  return {
    event_api_url: buildApiUrl(item.scheme, item.host, item.port, preset.eventPathTemplate, item.channel_id),
    snapshot_api_url: buildApiUrl(item.scheme, item.host, item.port, preset.snapshotPathTemplate, item.channel_id),
  };
}

function sanitizeCameraId(text: string): string {
  const cleaned = text
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '');
  return cleaned || 'camera';
}

function ensureUniqueId(base: string, used: Set<string>): string {
  let next = base;
  let suffix = 2;
  while (used.has(next)) {
    next = `${base}_${suffix}`;
    suffix += 1;
  }
  used.add(next);
  return next;
}

function formatCaptureActionMessage(base: string, status: CaptureRuntimeStatus | null): string {
  const errors = Array.isArray(status?.start_errors) ? status?.start_errors ?? [] : [];
  if (errors.length === 0) return base;
  return `${base}；部分摄像头启动失败: ${errors.slice(0, 3).join(' | ')}`;
}

export default function CameraConfigPage({ token, role }: Props) {
  const [items, setItems] = useState<CameraSourceConfigItem[]>([]);
  const [statuses, setStatuses] = useState<CameraStatusItem[]>([]);
  const [captureStatus, setCaptureStatus] = useState<CaptureRuntimeStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [captureLoading, setCaptureLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [actingCapture, setActingCapture] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const canEdit = role === 'admin' || role === 'operator';
  const configManagedLabel = items.length > 0 ? '网页配置已保存到数据库' : '网页配置为空，保存后写入数据库';
  const captureRuntimeLabel =
    captureStatus?.worker_count && captureStatus.worker_count > 0
      ? `已下发到 ${captureStatus.worker_count} 路抓拍进程`
      : captureStatus?.desired_running
        ? '等待抓拍进程启动后下发'
        : '抓拍未启动，保存后下次启动生效';

  const statusById = useMemo(() => {
    const map = new Map<string, CameraStatusItem>();
    for (const status of statuses) map.set(status.camera_id, status);
    return map;
  }, [statuses]);

  const runtimeWorkerById = useMemo(() => {
    const map = new Map<string, Record<string, unknown>>();
    for (const worker of captureStatus?.workers ?? []) {
      const cameraId = String(worker.camera_id ?? '').trim();
      if (!cameraId) continue;
      map.set(cameraId, worker);
    }
    return map;
  }, [captureStatus?.workers]);

  const startErrorsByCameraId = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const line of captureStatus?.start_errors ?? []) {
      const text = String(line ?? '').trim();
      if (!text) continue;
      const [cameraId, ...rest] = text.split(':');
      const key = cameraId.trim();
      if (!key) continue;
      const detail = rest.join(':').trim() || text;
      const bucket = map.get(key) ?? [];
      bucket.push(detail);
      map.set(key, bucket);
    }
    return map;
  }, [captureStatus?.start_errors]);

  const loadCaptureStatus = useCallback(async () => {
    setCaptureLoading(true);
    try {
      const resp = await fetchCaptureStatus(token);
      setCaptureStatus(resp);
    } catch (err) {
      setCaptureStatus(null);
      setError((err as Error).message);
    } finally {
      setCaptureLoading(false);
    }
  }, [token]);

  const load = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const [cfg, st, cap] = await Promise.all([fetchCameraConfigs(token), fetchCameras(token), fetchCaptureStatus(token)]);
      const normalized = cfg.items.map((item) => ({
        ...item,
        vendor: item.vendor?.trim() || 'custom',
      }));
      setItems(normalized);
      setStatuses(st.items);
      setCaptureStatus(cap);
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
    let active = true;
    const timer = setInterval(() => {
      if (!active) return;
      void loadCaptureStatus();
    }, 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [loadCaptureStatus]);

  function handleField(
    index: number,
    key: keyof CameraSourceConfigItem,
    value: string | boolean | number,
  ) {
    setItems((prev) =>
      prev.map((item, i) => {
        if (i !== index) return item;
        const nextItem = { ...item, [key]: value } as CameraSourceConfigItem;
        if (nextItem.vendor !== 'custom' && ['host', 'port', 'scheme', 'channel_id'].includes(String(key))) {
          const urls = applyPresetUrls(nextItem);
          return { ...nextItem, ...urls };
        }
        return nextItem;
      }),
    );
  }

  function applyVendorPreset(index: number) {
    setItems((prev) =>
      prev.map((item, i) => {
        if (i !== index) return item;
        const preset = getPreset(item.vendor);
        const merged: CameraSourceConfigItem = {
          ...item,
          scheme: item.scheme?.trim() || preset.defaultScheme,
          port: Number.isFinite(item.port) ? Math.max(1, Math.trunc(item.port)) : preset.defaultPort,
        };
        if (!Number.isFinite(item.port) || item.port <= 0) {
          merged.port = preset.defaultPort;
        }
        const urls = applyPresetUrls(merged);
        return { ...merged, ...urls };
      }),
    );
  }

  function handleVendorChange(index: number, vendor: string) {
    setItems((prev) =>
      prev.map((item, i) => {
        if (i !== index) return item;
        const preset = getPreset(vendor);
        const merged: CameraSourceConfigItem = {
          ...item,
          vendor,
          scheme: item.scheme?.trim() || preset.defaultScheme,
          port: Number.isFinite(item.port) ? Math.max(1, Math.trunc(item.port)) : preset.defaultPort,
        };
        if (!Number.isFinite(item.port) || item.port <= 0) {
          merged.port = preset.defaultPort;
        }
        if (preset.key === 'custom') return merged;
        const urls = applyPresetUrls(merged);
        return { ...merged, ...urls };
      }),
    );
  }

  function handleAdd() {
    setItems((prev) => [...prev, createEmptyCamera(prev.length + 1)]);
  }

  function handleDelete(index: number) {
    setItems((prev) => prev.filter((_, i) => i !== index));
  }

  async function handleSave() {
    setError(null);
    setMessage(null);
    setSaving(true);
    try {
      const usedIds = new Set<string>();
      const cleaned = items
        .map((item, index) => {
          const preset = getPreset(item.vendor);
          const host = item.host.trim();
          const channelId = Number.isFinite(item.channel_id) ? Math.max(1, Math.trunc(item.channel_id)) : 1;
          const baseId = sanitizeCameraId(item.id || item.name || host || `camera_${index + 1}`);
          const id = ensureUniqueId(baseId, usedIds);
          const scheme = item.scheme.trim() || preset.defaultScheme;
          const port = Number.isFinite(item.port) ? Math.max(1, Math.trunc(item.port)) : preset.defaultPort;
          const partial: CameraSourceConfigItem = {
            ...item,
            id,
            name: item.name.trim() || id,
            vendor: item.vendor?.trim() || 'custom',
            host,
            scheme,
            port,
            channel_id: channelId,
            username: item.username.trim(),
            password: item.password,
            enabled: Boolean(item.enabled),
            event_api_url: item.event_api_url.trim(),
            snapshot_api_url: item.snapshot_api_url.trim(),
            rtsp_url: '',
          };
          if (partial.vendor !== 'custom') {
            const urls = applyPresetUrls(partial);
            partial.event_api_url = urls.event_api_url;
            partial.snapshot_api_url = urls.snapshot_api_url;
          }
          partial.rtsp_url = buildRtspUrl(partial);
          return partial;
        })
        .filter(
          (item) =>
            item.id &&
            ((item.event_api_url && item.snapshot_api_url) || (item.host && item.username && item.password)),
        );
      const resp = await saveCameraConfigs(token, cleaned);
      setItems(resp.items);
      let finalMessage = `已保存并应用 ${resp.items.length} 个摄像头配置`;
      const st = await fetchCameras(token);
      setStatuses(st.items);
      if (captureStatus?.running) {
        const restartResp = await restartCapture(token);
        setCaptureStatus(restartResp.status);
        finalMessage = formatCaptureActionMessage(`${finalMessage}；抓拍进程已按启用摄像头重载`, restartResp.status);
      } else {
        finalMessage += '；当前抓拍未启动，配置已保存，启动抓拍后才会生效';
      }
      setMessage(finalMessage);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function handleTest(cameraId: string) {
    setError(null);
    setMessage(null);
    setTestingId(cameraId);
    try {
      const result = await testCamera(token, cameraId);
      const ok = Boolean(result.ok);
      const reason = String(result.reason ?? '');
      setMessage(`测试 ${cameraId}: ${ok ? '可用' : '不可用'} (${reason || 'no reason'})`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setTestingId(null);
    }
  }

  async function handleStartCapture() {
    setError(null);
    setMessage(null);
    setActingCapture(true);
    try {
      const resp = await startCapture(token);
      setCaptureStatus(resp.status);
      setMessage(formatCaptureActionMessage('抓拍进程已启动', resp.status));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setActingCapture(false);
    }
  }

  async function handleStopCapture() {
    setError(null);
    setMessage(null);
    setActingCapture(true);
    try {
      const resp = await stopCapture(token);
      setCaptureStatus(resp.status);
      setMessage('抓拍进程已停止');
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setActingCapture(false);
    }
  }

  async function handleRestartCapture() {
    setError(null);
    setMessage(null);
    setActingCapture(true);
    try {
      const resp = await restartCapture(token);
      setCaptureStatus(resp.status);
      setMessage(formatCaptureActionMessage('抓拍进程已重启', resp.status));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setActingCapture(false);
    }
  }

  async function handleStartCaptureWithCamera(cameraId: string) {
    if (!cameraId.trim()) return;
    setError(null);
    setMessage(null);
    setActingCapture(true);
    try {
      const resp = await startCapture(token, cameraId.trim());
      setCaptureStatus(resp.status);
      setMessage(formatCaptureActionMessage(`抓拍进程已切换到摄像头 ${cameraId}`, resp.status));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setActingCapture(false);
    }
  }

  return (
    <main className="camera-config-page">
      <section className="card">
        <div className="camera-config-head">
          <h2>摄像头配置</h2>
          <button type="button" onClick={() => void load()} disabled={loading}>
            {loading ? '刷新中...' : '刷新'}
          </button>
        </div>
        <p className="muted">配置方式: {configManagedLabel}</p>

        <div className="camera-capture-quick">
          <div>
            <p className="muted">
              抓拍进程: {captureStatus?.running ? '运行中' : '已停止'} | PID: {captureStatus?.pid ?? '-'} | 启动时间:{' '}
              {toDateTimeText(captureStatus?.started_at ?? null)}
            </p>
            <p className="muted">
              抓拍摄像头:{' '}
              {Array.isArray(captureStatus?.active_camera_ids) && captureStatus.active_camera_ids.length > 0
                ? captureStatus.active_camera_ids.join(', ')
                : captureStatus?.active_camera_id ?? '-'}
            </p>
            <p className="muted">
              Worker 数: {captureStatus?.worker_count ?? 0} | 待启动:{' '}
              {captureStatus?.pending_camera_ids && captureStatus.pending_camera_ids.length > 0
                ? captureStatus.pending_camera_ids.join(', ')
                : '-'}
            </p>
            <p className="muted">抓拍参数状态: {captureRuntimeLabel}</p>
          </div>
          <div className="camera-config-actions">
            <button
              type="button"
              onClick={() => void handleStartCapture()}
              disabled={!canEdit || actingCapture || captureStatus?.running}
            >
              {actingCapture && !captureStatus?.running ? '启动中...' : '启动抓拍'}
            </button>
            <button
              type="button"
              onClick={() => void handleStopCapture()}
              disabled={!canEdit || actingCapture || !captureStatus?.running}
            >
              {actingCapture && captureStatus?.running ? '停止中...' : '停止抓拍'}
            </button>
            <button type="button" onClick={() => void handleRestartCapture()} disabled={!canEdit || actingCapture}>
              {actingCapture ? '重启中...' : '重启抓拍'}
            </button>
            <button type="button" onClick={() => void loadCaptureStatus()} disabled={captureLoading}>
              {captureLoading ? '查询中...' : '查询抓拍状态'}
            </button>
          </div>
        </div>
        {captureStatus?.start_errors && captureStatus.start_errors.length > 0 ? (
          <div className="camera-start-errors">
            <strong>最近启动失败</strong>
            <ul>
              {captureStatus.start_errors.slice(0, 6).map((line, idx) => (
                <li key={`${line}-${idx}`}>{line}</li>
              ))}
            </ul>
          </div>
        ) : null}

        <p className="camera-vendor-help muted">
          品牌协议模板: 海康 ISAPI / 大华 CGI / 自定义。先填主机、通道，再点“应用模板”可自动生成事件流和抓图地址。
        </p>

        {!canEdit ? <p className="muted">当前角色为只读，无法保存配置。</p> : null}
        {error ? <p className="error">{error}</p> : null}
        {message ? <p>{message}</p> : null}

        <div className="camera-config-actions">
          <button type="button" onClick={handleAdd} disabled={!canEdit}>
            新增摄像头
          </button>
          <button type="button" onClick={() => void handleSave()} disabled={!canEdit || saving}>
            {saving ? '保存中...' : '保存并应用'}
          </button>
        </div>

        <div className="camera-item-list">
          {items.map((item, index) => {
            const status = statusById.get(item.id);
            const worker = runtimeWorkerById.get(item.id);
            const workerRunning = Boolean(worker?.running);
            const startErrors = startErrorsByCameraId.get(item.id) ?? [];
            const preset = getPreset(item.vendor);
            return (
              <article className="camera-item-card" key={`${item.id}-${index}`}>
                <div className="camera-item-head">
                  <div>
                    <h3>{item.name?.trim() || `摄像头${index + 1}`}</h3>
                    <p className="muted">协议模板: {preset.label}</p>
                  </div>
                  <div className="camera-item-status">
                    {status ? (
                      <>
                        <strong>{status.online ? '在线' : '离线'}</strong>
                        <span className="muted">frames: {status.frames_read}</span>
                        <span className="muted">抓拍: {workerRunning ? '运行中' : item.enabled ? '未运行' : '未启用'}</span>
                      </>
                    ) : (
                      <span className="muted">未加载运行状态</span>
                    )}
                  </div>
                </div>
                <p className="muted">
                  参数状态: {workerRunning ? '已下发到本路抓拍进程' : item.enabled ? '已保存，启动抓拍后生效' : '未启用'}
                </p>
                {startErrors.length > 0 ? (
                  <div className="camera-inline-error">
                    <strong>启动失败</strong>
                    <p>{startErrors.join(' | ')}</p>
                  </div>
                ) : null}

                <div className="camera-item-grid">
                  <label>
                    <span>名称</span>
                    <input value={item.name} onChange={(e) => handleField(index, 'name', e.target.value)} disabled={!canEdit} />
                  </label>
                  <label>
                    <span>品牌协议</span>
                    <select
                      value={item.vendor || 'custom'}
                      onChange={(e) => handleVendorChange(index, e.target.value)}
                      disabled={!canEdit}
                    >
                      {VENDOR_PRESETS.map((vendor) => (
                        <option key={vendor.key} value={vendor.key}>
                          {vendor.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>主机/IP</span>
                    <input value={item.host} onChange={(e) => handleField(index, 'host', e.target.value)} disabled={!canEdit} />
                  </label>
                  <label>
                    <span>端口</span>
                    <input
                      type="number"
                      value={String(item.port)}
                      onChange={(e) => handleField(index, 'port', Number.parseInt(e.target.value, 10) || 80)}
                      disabled={!canEdit}
                    />
                  </label>
                  <label>
                    <span>协议</span>
                    <select value={item.scheme} onChange={(e) => handleField(index, 'scheme', e.target.value)} disabled={!canEdit}>
                      <option value="http">http</option>
                      <option value="https">https</option>
                    </select>
                  </label>
                  <label>
                    <span>用户名</span>
                    <input
                      value={item.username}
                      onChange={(e) => handleField(index, 'username', e.target.value)}
                      disabled={!canEdit}
                    />
                  </label>
                  <label>
                    <span>密码</span>
                    <input
                      type="password"
                      value={item.password}
                      onChange={(e) => handleField(index, 'password', e.target.value)}
                      disabled={!canEdit}
                    />
                  </label>
                  <label>
                    <span>通道</span>
                    <input
                      type="number"
                      value={String(item.channel_id)}
                      onChange={(e) => handleField(index, 'channel_id', Number.parseInt(e.target.value, 10) || 1)}
                      disabled={!canEdit}
                    />
                  </label>
                  <div className="camera-enabled-cell">
                    <span>启用</span>
                    <label className="camera-config-checkbox">
                      <input
                        type="checkbox"
                        checked={item.enabled}
                        onChange={(e) => handleField(index, 'enabled', e.target.checked)}
                        disabled={!canEdit}
                      />
                      <span>{item.enabled ? '是' : '否'}</span>
                    </label>
                  </div>
                </div>

                <div className="camera-item-url-grid">
                  <label>
                    <span>事件流 API 链接</span>
                    <input
                      value={item.event_api_url}
                      onChange={(e) => handleField(index, 'event_api_url', e.target.value)}
                      placeholder="例如: http://ip:80/ISAPI/Event/notification/alertStream"
                      disabled={!canEdit}
                    />
                  </label>
                  <label>
                    <span>抓图 API 链接</span>
                    <input
                      value={item.snapshot_api_url}
                      onChange={(e) => handleField(index, 'snapshot_api_url', e.target.value)}
                      placeholder="例如: http://ip:80/ISAPI/Streaming/channels/101/picture"
                      disabled={!canEdit}
                    />
                  </label>
                </div>

                <div className="camera-item-actions">
                  <button type="button" className="ghost-btn" onClick={() => applyVendorPreset(index)} disabled={!canEdit}>
                    应用模板
                  </button>
                  <button type="button" onClick={() => void handleTest(item.id)} disabled={!item.id || testingId === item.id}>
                    {testingId === item.id ? '测试中...' : '测试'}
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleStartCaptureWithCamera(item.id)}
                    disabled={!canEdit || !item.id || actingCapture}
                  >
                    用此路抓拍
                  </button>
                  <button type="button" className="ghost-btn" onClick={() => handleDelete(index)} disabled={!canEdit}>
                    删除
                  </button>
                </div>
              </article>
            );
          })}

          {items.length === 0 ? (
            <div className="camera-item-empty muted">暂无摄像头配置，点击“新增摄像头”开始</div>
          ) : null}
        </div>
      </section>
    </main>
  );
}
