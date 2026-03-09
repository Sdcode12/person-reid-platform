import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import {
  fetchCameraConfigs,
  fetchCaptureLogs,
  fetchCapturePhoto,
  fetchCaptureRecent,
  fetchCaptureStatus,
  restartCapture,
  startCapture,
  stopCapture,
} from '../api/client';
import type { CameraSourceConfigItem, CaptureLogItem, CaptureRuntimeStatus } from '../api/types';

interface Props {
  token: string;
  role: string;
}

const IMAGE_MODE_LABELS: Record<string, string> = {
  color: '彩色',
  low_light_color: '低照度彩色',
  ir_bw: '红外黑白',
  unknown: '未知',
};

function toDateText(value: unknown): string {
  if (typeof value !== 'string' || !value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString();
}

function toText(value: unknown): string {
  if (value === null || value === undefined) return '-';
  if (typeof value === 'string') return value || '-';
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '-';
}

function toNum(value: unknown): number | null {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function renderLogLine(item: CaptureLogItem): string {
  const time = toDateText(item.timestamp);
  return `[${time}] [${item.source}] ${item.line}`;
}

function formatCaptureActionMessage(base: string, status: CaptureRuntimeStatus | null): string {
  const errors = Array.isArray(status?.start_errors) ? status?.start_errors ?? [] : [];
  if (errors.length === 0) return base;
  return `${base}；部分摄像头启动失败: ${errors.slice(0, 3).join(' | ')}`;
}

export default function CapturePage({ token, role }: Props) {
  const navigate = useNavigate();
  const [status, setStatus] = useState<CaptureRuntimeStatus | null>(null);
  const [logs, setLogs] = useState<CaptureLogItem[]>([]);
  const [recentItems, setRecentItems] = useState<Record<string, unknown>[]>([]);
  const [cameraOptions, setCameraOptions] = useState<CameraSourceConfigItem[]>([]);
  const [captureCameraId, setCaptureCameraId] = useState('');
  const [loading, setLoading] = useState(false);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  const canControl = role === 'admin' || role === 'operator';
  const workerItems = useMemo(() => status?.workers ?? [], [status?.workers]);
  const stats = useMemo(() => {
    const total = recentItems.length;
    if (total === 0) {
      return {
        total: 0,
        avgQuality: 0,
        lowLightRatio: 0,
        uniqueCameras: 0,
        topUpperColor: '-',
      };
    }
    let qualitySum = 0;
    let qualityCount = 0;
    let lowLightCount = 0;
    const cameraSet = new Set<string>();
    const upperColorCounter = new Map<string, number>();
    for (const item of recentItems) {
      const quality = toNum(item.quality_score);
      if (quality !== null) {
        qualitySum += quality;
        qualityCount += 1;
      }
      const imageMode = String(item.image_mode ?? '').trim().toLowerCase();
      if (imageMode === 'low_light_color' || imageMode === 'ir_bw') lowLightCount += 1;
      const camera = String(item.camera_id ?? '');
      if (camera) cameraSet.add(camera);
      const upper = String(item.upper_color ?? '').trim() || 'unknown';
      upperColorCounter.set(upper, (upperColorCounter.get(upper) ?? 0) + 1);
    }
    let topUpperColor = '-';
    let topUpperCount = 0;
    for (const [color, count] of upperColorCounter.entries()) {
      if (count > topUpperCount) {
        topUpperColor = color;
        topUpperCount = count;
      }
    }
    return {
      total,
      avgQuality: qualityCount > 0 ? qualitySum / qualityCount : 0,
      lowLightRatio: lowLightCount / total,
      uniqueCameras: cameraSet.size,
      topUpperColor,
    };
  }, [recentItems]);

  const loadRuntime = useCallback(async () => {
    const [st, lg, rc] = await Promise.all([
      fetchCaptureStatus(token),
      fetchCaptureLogs(token, 220),
      fetchCaptureRecent(token, 80),
    ]);
    setStatus(st);
    setLogs(lg.items);
    setRecentItems(rc.items);
  }, [token]);

  const loadAll = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      await loadRuntime();
      try {
        const cams = await fetchCameraConfigs(token);
        setCameraOptions(cams.items);
      } catch {
        // keep previous camera options
      }
      setCaptureCameraId('');
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [loadRuntime, token]);

  useEffect(() => {
    let active = true;
    const boot = async () => {
      if (!active) return;
      await loadAll();
    };
    void boot();
    return () => {
      active = false;
    };
  }, [loadAll]);

  useEffect(() => {
    let active = true;
    const timer = setInterval(async () => {
      if (!active) return;
      try {
        await loadRuntime();
      } catch {
        // keep previous UI values
      }
    }, 6000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [loadRuntime]);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  async function handleStart() {
    setError(null);
    setMessage(null);
    setActing(true);
    try {
      const resp = await startCapture(token, captureCameraId || undefined);
      setStatus(resp.status);
      setMessage(formatCaptureActionMessage('采集进程已启动', resp.status));
      await loadRuntime();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setActing(false);
    }
  }

  async function handleStop() {
    setError(null);
    setMessage(null);
    setActing(true);
    try {
      const resp = await stopCapture(token);
      setStatus(resp.status);
      setMessage('采集进程已停止');
      await loadRuntime();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setActing(false);
    }
  }

  async function handleRestart() {
    setError(null);
    setMessage(null);
    setActing(true);
    try {
      const resp = await restartCapture(token, captureCameraId || undefined);
      setStatus(resp.status);
      setMessage(formatCaptureActionMessage('采集进程已重启', resp.status));
      await loadRuntime();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setActing(false);
    }
  }

  async function handlePreview(path: string, trackId?: number) {
    if (!path && !(typeof trackId === 'number' && trackId > 0)) return;
    setError(null);
    setPreviewLoading(true);
    try {
      const blob = await fetchCapturePhoto(token, path, trackId);
      const nextUrl = URL.createObjectURL(blob);
      setPreviewUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return nextUrl;
      });
      setPreviewPath(path || `track:${trackId}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPreviewLoading(false);
    }
  }

  function closePreview() {
    setPreviewPath(null);
    setPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
  }

  return (
    <main className="capture-page">
      <section className="card capture-runtime-card">
        <div className="capture-runtime-head">
          <h2>采集控制台</h2>
          <div className="capture-actions">
            <button type="button" className="ghost-btn" onClick={() => navigate('/capture-settings')}>
              参数配置
            </button>
            <button type="button" onClick={() => void loadAll()} disabled={loading}>
              {loading ? '刷新中...' : '刷新'}
            </button>
          </div>
        </div>
        <p className="muted">
          状态: {status?.running ? '运行中' : '已停止'} | PID: {status?.pid ?? '-'} | 启动时间:{' '}
          {status?.started_at ? toDateText(status.started_at) : '-'}
        </p>
        <div className="capture-camera-row">
          <label htmlFor="capture-camera-select">抓拍摄像头</label>
          <select
            id="capture-camera-select"
            value={captureCameraId}
            onChange={(e) => setCaptureCameraId(e.target.value)}
            disabled={!canControl}
          >
            <option value="">全部启用摄像头(推荐)</option>
            {cameraOptions.map((cam) => (
              <option key={cam.id} value={cam.id}>
                {cam.name} ({cam.id})
              </option>
            ))}
          </select>
        </div>
        <p className="muted">
          当前抓拍摄像头:{' '}
          {Array.isArray(status?.active_camera_ids) && status.active_camera_ids.length > 0
            ? status.active_camera_ids.join(', ')
            : status?.active_camera_id || captureCameraId || '-'}
        </p>
        <p className="muted">
          Worker 数: {status?.worker_count ?? 0} | 待启动摄像头:{' '}
          {status?.pending_camera_ids && status.pending_camera_ids.length > 0
            ? status.pending_camera_ids.join(', ')
            : '-'}
        </p>
        <p className="muted">
          期望状态: {status?.desired_running ? '保持运行' : '手动停止'} | 自动重启:{' '}
          {status?.auto_restart_enabled ? '开启' : '关闭'} | 重启次数: {status?.restart_count ?? 0} | 等待重启:{' '}
          {status?.restart_pending ? '是' : '否'}
        </p>
        <div className="capture-actions">
          <button type="button" onClick={() => void handleStart()} disabled={!canControl || acting || status?.running}>
            {acting && !status?.running ? '启动中...' : '启动采集'}
          </button>
          <button type="button" onClick={() => void handleStop()} disabled={!canControl || acting || !status?.running}>
            {acting && status?.running ? '停止中...' : '停止采集'}
          </button>
          <button type="button" onClick={() => void handleRestart()} disabled={!canControl || acting}>
            {acting ? '重启中...' : '重启采集'}
          </button>
        </div>
        {!canControl ? <p className="muted">当前角色为只读，无法启动或停止采集。</p> : null}
        {message ? <p>{message}</p> : null}
        {error ? <p className="error">{error}</p> : null}
        {status?.start_errors && status.start_errors.length > 0 ? (
          <div className="capture-inline-errors">
            <strong>最近启动失败</strong>
            <ul>
              {status.start_errors.slice(0, 8).map((line, idx) => (
                <li key={`${line}-${idx}`}>{line}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>

      <section className="card capture-worker-card">
        <div className="capture-runtime-head">
          <h3>多摄像头运行状态</h3>
          <span className="muted">每一路抓拍进程独立运行</span>
        </div>
        {workerItems.length === 0 ? (
          <p className="muted">当前没有运行中的抓拍 worker</p>
        ) : (
          <div className="capture-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>摄像头</th>
                  <th>状态</th>
                  <th>PID</th>
                  <th>启动时间</th>
                  <th>重启次数</th>
                  <th>等待重启</th>
                  <th>退出码</th>
                  <th>参数状态</th>
                </tr>
              </thead>
              <tbody>
                {workerItems.map((worker, idx) => (
                  <tr key={`${String(worker.camera_id ?? 'worker')}-${idx}`}>
                    <td>{toText(worker.camera_id)}</td>
                    <td>{Boolean(worker.running) ? '运行中' : '已退出'}</td>
                    <td>{toText(worker.pid)}</td>
                    <td>{toDateText(worker.started_at)}</td>
                    <td>{toText(worker.restart_count)}</td>
                    <td>{Boolean(worker.restart_pending) ? '是' : '否'}</td>
                    <td>{toText(worker.last_exit_code)}</td>
                    <td>{Boolean(worker.running) ? '已下发并运行' : '未运行'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card capture-stats-card">
        <h3>采集统计（最近80条）</h3>
        <div className="capture-stats-grid">
          <div className="capture-stat">
            <p className="muted">抓拍总数</p>
            <p>{stats.total}</p>
          </div>
          <div className="capture-stat">
            <p className="muted">平均质量分</p>
            <p>{stats.avgQuality.toFixed(3)}</p>
          </div>
          <div className="capture-stat">
            <p className="muted">低照/红外占比</p>
            <p>{(stats.lowLightRatio * 100).toFixed(1)}%</p>
          </div>
          <div className="capture-stat">
            <p className="muted">摄像头数</p>
            <p>{stats.uniqueCameras}</p>
          </div>
          <div className="capture-stat">
            <p className="muted">上衣主色</p>
            <p>{stats.topUpperColor}</p>
          </div>
        </div>
      </section>

      <section className="card capture-live-log-card">
        <h3>运行日志</h3>
        <pre className="capture-log-box">{logs.map(renderLogLine).join('\n') || '暂无日志'}</pre>
      </section>

      <section className="card capture-recent-card">
        <h3>最近抓拍</h3>
        {recentItems.length === 0 ? (
          <p className="muted">暂无数据</p>
        ) : (
          <div className="capture-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>时间</th>
                  <th>摄像头</th>
                  <th>人数</th>
                  <th>颜色</th>
                  <th>质量</th>
                  <th>图像模式</th>
                  <th>预览</th>
                  <th>图片路径</th>
                </tr>
              </thead>
              <tbody>
                {recentItems.map((item, idx) => {
                  const trackId = Number(item.track_id);
                  const hasTrackId = Number.isFinite(trackId) && trackId > 0;
                  const path = String(item.image_path ?? '');
                  return (
                    <tr key={`${toText(item.image_path)}-${idx}`}>
                      <td>{toDateText(item.captured_at)}</td>
                      <td>{toText(item.camera_id)}</td>
                      <td>{toText(item.people_count)}</td>
                      <td>
                        {toText(item.upper_color)} / {toText(item.lower_color)}
                      </td>
                      <td>{toText(item.quality_score)}</td>
                      <td>{IMAGE_MODE_LABELS[String(item.image_mode ?? '').trim().toLowerCase()] ?? toText(item.image_mode)}</td>
                      <td>
                        <button
                          type="button"
                          onClick={() => void handlePreview(path, hasTrackId ? trackId : undefined)}
                          disabled={previewLoading || (!item.image_path && !hasTrackId)}
                        >
                          {previewLoading && previewPath === item.image_path ? '加载中...' : '查看'}
                        </button>
                      </td>
                      <td className="capture-path-cell">{toText(item.image_path)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {previewUrl ? (
        <section className="capture-preview-overlay" onClick={closePreview}>
          <div className="capture-preview-dialog card" onClick={(e) => e.stopPropagation()}>
            <div className="capture-preview-head">
              <h3>抓拍预览</h3>
              <button type="button" onClick={closePreview}>
                关闭
              </button>
            </div>
            <p className="muted">{previewPath}</p>
            <img className="capture-preview-image" src={previewUrl} alt="capture preview" />
          </div>
        </section>
      ) : null}
    </main>
  );
}
