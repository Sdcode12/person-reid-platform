import { useCallback, useEffect, useMemo, useState } from 'react';

import { deleteCaptureItems, fetchCameraConfigs, fetchCapturePhoto, queryCaptureItems, syncCaptureToDb } from '../api/client';

type MaybeRecord = Record<string, unknown>;

interface Props {
  token: string;
  role: string;
}

const COLORS = ['', 'black', 'white', 'gray', 'red', 'orange', 'yellow', 'green', 'blue', 'purple', 'brown'];
const POSES = ['', 'front_or_back', 'side', 'partial_or_close'];
const IMAGE_MODES = ['', 'color', 'low_light_color', 'ir_bw', 'unknown'];
const IMAGE_MODE_LABELS: Record<string, string> = {
  '': '不限',
  color: '彩色',
  low_light_color: '低照度彩色',
  ir_bw: '红外黑白',
  unknown: '未知',
};

function formatDatetimeLocal(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  const hh = String(date.getHours()).padStart(2, '0');
  const mm = String(date.getMinutes()).padStart(2, '0');
  return `${y}-${m}-${d}T${hh}:${mm}`;
}

function defaultRange(): { start: string; end: string } {
  const now = new Date();
  const yesterdayStart = new Date(now);
  yesterdayStart.setDate(yesterdayStart.getDate() - 1);
  yesterdayStart.setHours(0, 0, 0, 0);
  return {
    start: formatDatetimeLocal(yesterdayStart),
    end: formatDatetimeLocal(now),
  };
}

function textOf(value: unknown): string {
  if (value === null || value === undefined) return '-';
  if (typeof value === 'string') return value || '-';
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '-';
}

function dateText(value: unknown): string {
  if (typeof value !== 'string') return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString();
}

function toPositiveInt(value: unknown): number | null {
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  const intValue = Math.trunc(n);
  return intValue > 0 ? intValue : null;
}

function itemSelectionKey(item: MaybeRecord): string | null {
  const trackId = toPositiveInt(item.track_id);
  if (trackId !== null) return `track:${trackId}`;
  const path = String(item.image_path ?? '').trim();
  if (path) return `path:${path}`;
  return null;
}

export default function PhotoLibraryPage({ token, role }: Props) {
  const [items, setItems] = useState<MaybeRecord[]>([]);
  const [cameraOptions, setCameraOptions] = useState<string[]>([]);
  const [timeStart, setTimeStart] = useState(() => defaultRange().start);
  const [timeEnd, setTimeEnd] = useState(() => defaultRange().end);
  const [cameraId, setCameraId] = useState('');
  const [upperColor, setUpperColor] = useState('');
  const [lowerColor, setLowerColor] = useState('');
  const [hasHat, setHasHat] = useState('');
  const [imageMode, setImageMode] = useState('');
  const [poseHint, setPoseHint] = useState('');
  const [minQualityScore, setMinQualityScore] = useState('');
  const [limit, setLimit] = useState(120);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteMessage, setDeleteMessage] = useState<string | null>(null);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const canSync = role === 'admin' || role === 'operator';
  const canDelete = role === 'admin';

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await queryCaptureItems(token, {
        limit,
        scan_limit: 5000,
        camera_id: cameraId || undefined,
        upper_color: upperColor || undefined,
        lower_color: lowerColor || undefined,
        has_hat: hasHat === '' ? undefined : hasHat === 'true',
        image_mode: imageMode || undefined,
        pose_hint: poseHint || undefined,
        min_quality_score: minQualityScore.trim() ? Number(minQualityScore) : undefined,
        time_start: timeStart,
        time_end: timeEnd,
      });
      setItems(result.items);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [
    cameraId,
    hasHat,
    imageMode,
    limit,
    lowerColor,
    minQualityScore,
    poseHint,
    timeEnd,
    timeStart,
    token,
    upperColor,
  ]);

  useEffect(() => {
    let active = true;
    const boot = async () => {
      try {
        const cfg = await fetchCameraConfigs(token);
        if (!active) return;
        setCameraOptions(cfg.items.map((item) => item.id));
      } catch {
        // keep empty options
      }
      if (active) await load();
    };
    void boot();
    return () => {
      active = false;
    };
  }, [load, token]);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  useEffect(() => {
    setSelectedKeys([]);
  }, [items]);

  async function handlePreview(path: string, trackId?: number) {
    if (!path && !(typeof trackId === 'number' && trackId > 0)) return;
    setPreviewLoading(true);
    setError(null);
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

  function resetFilters() {
    const d = defaultRange();
    setTimeStart(d.start);
    setTimeEnd(d.end);
    setCameraId('');
    setUpperColor('');
    setLowerColor('');
    setHasHat('');
    setImageMode('');
    setPoseHint('');
    setMinQualityScore('');
    setLimit(120);
  }

  async function handleSyncDb() {
    if (!canSync) return;
    setSyncing(true);
    setError(null);
    setDeleteMessage(null);
    try {
      const stats = await syncCaptureToDb(token, 5000, true);
      const totalText =
        typeof stats.total_records === 'number' ? ` / 数据库总量 ${stats.total_records}` : '';
      const updatedText = typeof stats.updated === 'number' ? ` / 更新 ${stats.updated}` : '';
      const purgedText =
        typeof stats.purged_local_images === 'number' ? ` / 清理本地图 ${stats.purged_local_images}` : '';
      setSyncMessage(
        `同步完成: 扫描 ${stats.scanned} / 新增 ${stats.inserted}${updatedText} / 跳过 ${stats.skipped} / 错误 ${stats.errors}${purgedText}${totalText}`,
      );
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSyncing(false);
    }
  }

  function alignRangeToDay() {
    const base = timeStart ? new Date(timeStart) : new Date();
    if (Number.isNaN(base.getTime())) return;
    const start = new Date(base);
    const end = new Date(base);
    start.setHours(0, 0, 0, 0);
    end.setHours(23, 59, 0, 0);
    setTimeStart(formatDatetimeLocal(start));
    setTimeEnd(formatDatetimeLocal(end));
  }

  function alignRangeToMonth() {
    const base = timeStart ? new Date(timeStart) : new Date();
    if (Number.isNaN(base.getTime())) return;
    const start = new Date(base.getFullYear(), base.getMonth(), 1, 0, 0, 0, 0);
    const end = new Date(base.getFullYear(), base.getMonth() + 1, 0, 23, 59, 0, 0);
    setTimeStart(formatDatetimeLocal(start));
    setTimeEnd(formatDatetimeLocal(end));
  }

  function toggleItemSelection(item: MaybeRecord) {
    const key = itemSelectionKey(item);
    if (!key) return;
    setSelectedKeys((prev) => (prev.includes(key) ? prev.filter((itemKey) => itemKey !== key) : [...prev, key]));
  }

  function toggleSelectAllCurrentPage() {
    if (allSelectedOnPage) {
      setSelectedKeys([]);
      return;
    }
    setSelectedKeys(selectableKeys);
  }

  function buildDeleteFilterPayload(dryRun: boolean) {
    return {
      camera_id: cameraId || undefined,
      upper_color: upperColor || undefined,
      lower_color: lowerColor || undefined,
      has_hat: hasHat === '' ? undefined : hasHat === 'true',
      image_mode: imageMode || undefined,
      pose_hint: poseHint || undefined,
      min_quality_score: minQualityScore.trim() ? Number(minQualityScore) : undefined,
      time_start: timeStart.trim() ? timeStart : undefined,
      time_end: timeEnd.trim() ? timeEnd : undefined,
      delete_local_files: true,
      dry_run: dryRun,
    };
  }

  function buildDeleteSelectionPayload() {
    const selectedSet = new Set(selectedKeys);
    const trackIds: number[] = [];
    const imagePaths: string[] = [];
    const seenTrackIds = new Set<number>();
    const seenImagePaths = new Set<string>();
    for (const item of items) {
      const key = itemSelectionKey(item);
      if (!key || !selectedSet.has(key)) continue;
      const trackId = toPositiveInt(item.track_id);
      if (trackId !== null) {
        if (!seenTrackIds.has(trackId)) {
          seenTrackIds.add(trackId);
          trackIds.push(trackId);
        }
        continue;
      }
      const path = String(item.image_path ?? '').trim();
      if (path && !seenImagePaths.has(path)) {
        seenImagePaths.add(path);
        imagePaths.push(path);
      }
    }
    return { track_ids: trackIds, image_paths: imagePaths, delete_local_files: true, dry_run: false };
  }

  async function handleDeleteSelected() {
    if (!canDelete || selectedCount === 0) return;
    const payload = buildDeleteSelectionPayload();
    const matched = payload.track_ids.length + payload.image_paths.length;
    if (matched <= 0) return;
    const confirmed = window.confirm(`确认删除已勾选图片？共 ${matched} 条。该操作会删除数据库记录，并尽量清理可定位的本地图片。`);
    if (!confirmed) return;
    setDeleting(true);
    setError(null);
    setSyncMessage(null);
    setDeleteMessage(null);
    try {
      const result = await deleteCaptureItems(token, payload);
      setDeleteMessage(
        `删除完成: 删除 ${result.deleted} 条 / 清理本地图 ${result.deleted_local_images ?? 0} / 清理 sidecar ${result.deleted_local_sidecars ?? 0}`,
      );
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setDeleting(false);
    }
  }

  async function handleDeleteFiltered() {
    if (!canDelete) return;
    setDeleting(true);
    setError(null);
    setSyncMessage(null);
    setDeleteMessage(null);
    try {
      const preview = await deleteCaptureItems(token, buildDeleteFilterPayload(true));
      if (preview.matched <= 0) {
        setDeleteMessage('当前筛选范围没有可删除的数据。');
        return;
      }
      const confirmed = window.confirm(
        `确认删除当前筛选结果？共 ${preview.matched} 条。该操作不受“返回条数”限制，会删除整个筛选范围内的数据库记录。`,
      );
      if (!confirmed) return;
      const result = await deleteCaptureItems(token, buildDeleteFilterPayload(false));
      setDeleteMessage(
        `删除完成: 匹配 ${result.matched} 条 / 删除 ${result.deleted} 条 / 清理本地图 ${result.deleted_local_images ?? 0} / 清理 sidecar ${result.deleted_local_sidecars ?? 0}`,
      );
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setDeleting(false);
    }
  }

  const totalText = useMemo(() => `共 ${items.length} 条`, [items.length]);
  const selectableKeys = useMemo(
    () => items.map((item) => itemSelectionKey(item)).filter((value): value is string => Boolean(value)),
    [items],
  );
  const allSelectedOnPage = useMemo(
    () => selectableKeys.length > 0 && selectableKeys.every((key) => selectedKeys.includes(key)),
    [selectableKeys, selectedKeys],
  );
  const selectedCount = useMemo(() => selectedKeys.length, [selectedKeys]);

  return (
    <main className="gallery-page">
      <section className="card">
        <div className="gallery-head">
          <h2>图片库筛选</h2>
          <div className="gallery-head-actions">
            <button type="button" className="ghost-btn" onClick={() => void handleSyncDb()} disabled={!canSync || syncing}>
              {syncing ? '同步中...' : '同步到数据库'}
            </button>
            <button type="button" className="ghost-btn" onClick={alignRangeToDay}>
              对齐到当天
            </button>
            <button type="button" className="ghost-btn" onClick={alignRangeToMonth}>
              对齐到当月
            </button>
            <button
              type="button"
              className="danger-btn"
              onClick={() => void handleDeleteSelected()}
              disabled={!canDelete || deleting || selectedCount === 0}
            >
              {deleting && selectedCount > 0 ? '删除中...' : `删除勾选${selectedCount > 0 ? `(${selectedCount})` : ''}`}
            </button>
            <button type="button" className="danger-btn" onClick={() => void handleDeleteFiltered()} disabled={!canDelete || deleting}>
              {deleting ? '处理中...' : '删除当前筛选'}
            </button>
            <button type="button" className="ghost-btn" onClick={resetFilters}>
              重置
            </button>
            <button type="button" onClick={() => void load()} disabled={loading}>
              {loading ? '查询中...' : '查询'}
            </button>
          </div>
        </div>
        {syncMessage ? <p className="muted gallery-sync-note">{syncMessage}</p> : null}
        {deleteMessage ? <p className="muted gallery-sync-note">{deleteMessage}</p> : null}
        {!canSync ? <p className="muted gallery-sync-note">当前角色只读，可筛选查询，不可执行入库同步。</p> : null}
        {!canDelete ? <p className="muted gallery-sync-note">删除功能仅管理员可用。按天、按月删除可先用“对齐到当天/当月”再执行“删除当前筛选”。</p> : null}
        {canDelete ? <p className="muted gallery-sync-note">“删除当前筛选”会删除整个筛选范围内的数据，不受“返回条数”限制。</p> : null}
        <div className="gallery-filter-grid">
          <label>
            开始时间
            <input type="datetime-local" value={timeStart} onChange={(e) => setTimeStart(e.target.value)} />
          </label>
          <label>
            结束时间
            <input type="datetime-local" value={timeEnd} onChange={(e) => setTimeEnd(e.target.value)} />
          </label>
          <label>
            摄像头
            <select value={cameraId} onChange={(e) => setCameraId(e.target.value)}>
              <option value="">不限</option>
              {cameraOptions.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          </label>
          <label>
            上衣颜色
            <select value={upperColor} onChange={(e) => setUpperColor(e.target.value)}>
              {COLORS.map((c) => (
                <option key={`u-${c}`} value={c}>
                  {c || '不限'}
                </option>
              ))}
            </select>
          </label>
          <label>
            下装颜色
            <select value={lowerColor} onChange={(e) => setLowerColor(e.target.value)}>
              {COLORS.map((c) => (
                <option key={`l-${c}`} value={c}>
                  {c || '不限'}
                </option>
              ))}
            </select>
          </label>
          <label>
            帽子
            <select value={hasHat} onChange={(e) => setHasHat(e.target.value)}>
              <option value="">不限</option>
              <option value="true">戴帽</option>
              <option value="false">不戴帽</option>
            </select>
          </label>
          <label>
            图像模式
            <select value={imageMode} onChange={(e) => setImageMode(e.target.value)}>
              {IMAGE_MODES.map((mode) => (
                <option key={mode} value={mode}>
                  {IMAGE_MODE_LABELS[mode] ?? mode}
                </option>
              ))}
            </select>
          </label>
          <label>
            姿态
            <select value={poseHint} onChange={(e) => setPoseHint(e.target.value)}>
              {POSES.map((p) => (
                <option key={p} value={p}>
                  {p || '不限'}
                </option>
              ))}
            </select>
          </label>
          <label>
            最低质量分
            <input
              type="number"
              min={0}
              max={1}
              step={0.01}
              value={minQualityScore}
              onChange={(e) => setMinQualityScore(e.target.value)}
              placeholder="0.00 - 1.00"
            />
          </label>
          <label>
            返回条数
            <input
              type="number"
              min={1}
              max={1000}
              step={1}
              value={limit}
              onChange={(e) => setLimit(Number.parseInt(e.target.value, 10) || 120)}
            />
          </label>
        </div>
      </section>

      <section className="card">
        <div className="gallery-head">
          <h2>筛选结果</h2>
          <p className="muted">{totalText}</p>
        </div>
        {error ? <p className="error">{error}</p> : null}
        {items.length === 0 ? (
          <p className="muted">暂无结果</p>
        ) : (
          <div className="gallery-table-wrap">
            <table>
              <thead>
                <tr>
                  <th className="gallery-select-cell">
                    <label className="gallery-checkbox">
                      <input type="checkbox" checked={allSelectedOnPage} onChange={toggleSelectAllCurrentPage} disabled={items.length === 0} />
                      <span>全选</span>
                    </label>
                  </th>
                  <th>时间</th>
                  <th>摄像头</th>
                  <th>颜色</th>
                  <th>帽子/图像模式</th>
                  <th>质量/姿态</th>
                  <th>预览</th>
                  <th>路径</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item, idx) => {
                  const path = String(item.image_path ?? '');
                  const trackId = Number(item.track_id);
                  const hasTrackId = Number.isFinite(trackId) && trackId > 0;
                  const key = itemSelectionKey(item);
                  return (
                    <tr key={`${path}-${idx}`}>
                      <td className="gallery-select-cell">
                        <label className="gallery-checkbox">
                          <input
                            type="checkbox"
                            checked={key ? selectedKeys.includes(key) : false}
                            onChange={() => toggleItemSelection(item)}
                            disabled={!key || !canDelete}
                          />
                          <span>{key ? '选择' : '不可删'}</span>
                        </label>
                      </td>
                      <td>{dateText(item.captured_at)}</td>
                      <td>{textOf(item.camera_id)}</td>
                      <td>
                        {textOf(item.upper_color)} / {textOf(item.lower_color)}
                      </td>
                      <td>
                        hat:{textOf(item.has_hat)} / mode:{IMAGE_MODE_LABELS[String(item.image_mode ?? '')] ?? textOf(item.image_mode)}
                      </td>
                      <td>
                        {textOf(item.quality_score)} / {textOf(item.pose_hint)}
                      </td>
                      <td>
                        <button
                          type="button"
                          onClick={() => void handlePreview(path, hasTrackId ? trackId : undefined)}
                          disabled={(!path && !hasTrackId) || previewLoading}
                        >
                          {previewLoading && previewPath === path ? '加载中...' : '查看'}
                        </button>
                      </td>
                      <td className="capture-path-cell">{path || '-'}</td>
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
              <h3>图片预览</h3>
              <button type="button" onClick={closePreview}>
                关闭
              </button>
            </div>
            <p className="muted">{previewPath}</p>
            <img className="capture-preview-image" src={previewUrl} alt="gallery preview" />
          </div>
        </section>
      ) : null}
    </main>
  );
}
