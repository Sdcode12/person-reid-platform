import { type FormEvent, useEffect, useMemo, useRef, useState } from 'react';

import {
  fetchSearchHistory,
  fetchCameras,
  fetchCapturePhoto,
  submitSearchFeedback,
} from '../api/client';
import type {
  CameraStatusItem,
  SearchHistoryItem,
  SearchItem,
  SearchResponse,
} from '../api/types';

interface Props {
  onSearch: (fd: FormData) => Promise<SearchResponse>;
  token: string;
  role: string;
}

const COLORS = ['', 'black', 'white', 'gray', 'red', 'orange', 'yellow', 'green', 'blue', 'purple', 'brown'];
const POSE_HINTS = ['', 'front_or_back', 'side', 'partial_or_close'];
const IMAGE_MODES = ['', 'color', 'low_light_color', 'ir_bw', 'unknown'];
const COLOR_LABELS: Record<string, string> = {
  '': '不限',
  black: '黑',
  white: '白',
  gray: '灰',
  red: '红',
  orange: '橙',
  yellow: '黄',
  green: '绿',
  blue: '蓝',
  purple: '紫',
  brown: '棕',
};
const POSE_LABELS: Record<string, string> = {
  '': '不限',
  front_or_back: '正背面',
  side: '侧身',
  partial_or_close: '近景/局部',
};
const IMAGE_MODE_LABELS: Record<string, string> = {
  '': '不限',
  color: '彩色',
  low_light_color: '低照度彩色',
  ir_bw: '红外黑白',
  unknown: '未知',
};
const SORT_OPTIONS = [
  { value: 'similarity_desc', label: '相似度降序' },
  { value: 'time_desc', label: '时间降序' },
  { value: 'time_asc', label: '时间升序' },
  { value: 'quality_desc', label: '质量分降序' },
  { value: 'body_desc', label: 'Body分降序' },
  { value: 'attr_desc', label: 'Attr分降序' },
];

function formatDatetimeLocal(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  const hh = String(date.getHours()).padStart(2, '0');
  const mm = String(date.getMinutes()).padStart(2, '0');
  return `${y}-${m}-${d}T${hh}:${mm}`;
}

function getDefaultQueryRange(): { start: string; end: string } {
  const now = new Date();
  const yesterdayStart = new Date(now);
  yesterdayStart.setDate(yesterdayStart.getDate() - 1);
  yesterdayStart.setHours(0, 0, 0, 0);
  return {
    start: formatDatetimeLocal(yesterdayStart),
    end: formatDatetimeLocal(now),
  };
}

function toDatetimeLocalInput(value: string | null | undefined): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return formatDatetimeLocal(date);
}

function boolText(value: boolean | null | undefined, yes = '是', no = '否'): string {
  if (value === null || value === undefined) return '-';
  return value ? yes : no;
}

function formatPercent(value: number | null | undefined, digits = 1): string {
  return `${(Number(value ?? 0) * 100).toFixed(digits)}%`;
}

function summarizeColor(value: string | null | undefined): string {
  const key = String(value ?? '').trim();
  return (COLOR_LABELS[key] ?? key) || '-';
}

function summarizePose(value: string | null | undefined): string {
  const key = String(value ?? '').trim();
  return (POSE_LABELS[key] ?? key) || '-';
}

function summarizeMode(value: string | null | undefined): string {
  const key = String(value ?? '').trim();
  return (IMAGE_MODE_LABELS[key] ?? key) || '-';
}

async function buildResultThumb(blob: Blob, bbox?: number[] | null): Promise<string> {
  const sourceUrl = URL.createObjectURL(blob);
  if (!bbox || bbox.length < 4) return sourceUrl;

  return await new Promise((resolve) => {
    const image = new Image();
    image.onload = () => {
      const [rawX, rawY, rawW, rawH] = bbox.map((value) => Math.max(0, Math.trunc(Number(value) || 0)));
      if (rawW <= 1 || rawH <= 1) {
        resolve(sourceUrl);
        return;
      }

      const padX = rawW * 0.18;
      const padY = rawH * 0.14;
      const cx = rawX + rawW / 2;
      const cy = rawY + rawH / 2;
      const targetRatio = 4 / 5;

      let cropW = rawW + padX * 2;
      let cropH = rawH + padY * 2;
      if (cropW / cropH > targetRatio) {
        cropH = cropW / targetRatio;
      } else {
        cropW = cropH * targetRatio;
      }

      let sx = cx - cropW / 2;
      let sy = cy - cropH / 2;
      sx = Math.max(0, Math.min(sx, image.naturalWidth - cropW));
      sy = Math.max(0, Math.min(sy, image.naturalHeight - cropH));
      cropW = Math.max(1, Math.min(cropW, image.naturalWidth - sx));
      cropH = Math.max(1, Math.min(cropH, image.naturalHeight - sy));

      const canvas = document.createElement('canvas');
      canvas.width = 360;
      canvas.height = 450;
      const ctx = canvas.getContext('2d');
      if (!ctx) {
        resolve(sourceUrl);
        return;
      }
      ctx.drawImage(image, sx, sy, cropW, cropH, 0, 0, canvas.width, canvas.height);
      canvas.toBlob(
        (cropped) => {
          if (!cropped) {
            resolve(sourceUrl);
            return;
          }
          URL.revokeObjectURL(sourceUrl);
          resolve(URL.createObjectURL(cropped));
        },
        'image/jpeg',
        0.92,
      );
    };
    image.onerror = () => resolve(sourceUrl);
    image.src = sourceUrl;
  });
}

export default function SearchPage({ onSearch, token, role }: Props) {
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [queryPreviewUrl, setQueryPreviewUrl] = useState<string | null>(null);
  const [upperColor, setUpperColor] = useState('');
  const [lowerColor, setLowerColor] = useState('');
  const [timeStart, setTimeStart] = useState(() => getDefaultQueryRange().start);
  const [timeEnd, setTimeEnd] = useState(() => getDefaultQueryRange().end);
  const [hasHat, setHasHat] = useState('');
  const [cameraFilter, setCameraFilter] = useState('');
  const [imageMode, setImageMode] = useState('');
  const [poseHint, setPoseHint] = useState('');
  const [minQualityScore, setMinQualityScore] = useState('');
  const [groupByTarget, setGroupByTarget] = useState(true);
  const [diverseCamera, setDiverseCamera] = useState(true);
  const [topK, setTopK] = useState(12);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cameras, setCameras] = useState<CameraStatusItem[]>([]);
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [resultThumbs, setResultThumbs] = useState<Record<string, string>>({});
  const [sortBy, setSortBy] = useState('similarity_desc');
  const [pageSize, setPageSize] = useState(12);
  const [page, setPage] = useState(1);
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const [detailItem, setDetailItem] = useState<SearchItem | null>(null);
  const [feedbackNote, setFeedbackNote] = useState('');
  const [feedbackBusy, setFeedbackBusy] = useState(false);
  const [feedbackMessage, setFeedbackMessage] = useState<string | null>(null);
  const [searchHistory, setSearchHistory] = useState<SearchHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyScope, setHistoryScope] = useState<'mine' | 'all'>(role === 'admin' ? 'mine' : 'mine');
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const resultThumbsRef = useRef<Record<string, string>>({});

  const timelineRows = useMemo(() => {
    const timeline = result?.timeline ?? {};
    return Object.entries(timeline).sort((a, b) => Number(a[0]) - Number(b[0]));
  }, [result]);

  const searchMetrics = result?.metrics ?? {};

  const cameraOptions = useMemo(
    () =>
      cameras.map((camera) => ({
        value: camera.camera_id,
        label: `${camera.camera_name} (${camera.camera_id})`,
      })),
    [cameras],
  );

  const sortedResults = useMemo(() => {
    if (!result) return [];
    const list = [...result.results];
    const score = (item: SearchItem): number => {
      if (sortBy === 'time_desc' || sortBy === 'time_asc') return new Date(item.start_time).getTime();
      if (sortBy === 'quality_desc') return Number(item.quality_score ?? 0);
      if (sortBy === 'body_desc') return Number(item.body_sim ?? 0);
      if (sortBy === 'attr_desc') return Number(item.attr_score ?? 0);
      return Number(item.similarity ?? 0);
    };
    list.sort((a, b) => {
      const left = score(a);
      const right = score(b);
      if (sortBy === 'time_asc') return left - right;
      return right - left;
    });
    return list;
  }, [result, sortBy]);

  const totalPages = useMemo(() => {
    if (!sortedResults.length) return 1;
    return Math.max(1, Math.ceil(sortedResults.length / Math.max(1, pageSize)));
  }, [pageSize, sortedResults.length]);

  const resultOverview = useMemo(() => {
    const top = sortedResults[0];
    const cameraCount = new Set(sortedResults.map((item) => item.camera_id).filter(Boolean)).size;
    const evidenceCount = sortedResults.reduce((total, item) => total + (item.evidence_count ?? item.evidence?.length ?? 0), 0);
    return {
      topSimilarity: top?.similarity ?? 0,
      cameraCount,
      evidenceCount,
    };
  }, [sortedResults]);

  const pagedResults = useMemo(() => {
    const safePage = Math.max(1, Math.min(page, totalPages));
    const start = (safePage - 1) * Math.max(1, pageSize);
    return sortedResults.slice(start, start + Math.max(1, pageSize));
  }, [page, pageSize, sortedResults, totalPages]);

  function clearResultThumbs() {
    Object.values(resultThumbsRef.current).forEach((url) => URL.revokeObjectURL(url));
    resultThumbsRef.current = {};
    setResultThumbs({});
  }

  useEffect(() => {
    setPage((prev) => Math.max(1, Math.min(prev, totalPages)));
  }, [totalPages]);

  useEffect(() => {
    let active = true;
    const loadCameras = async () => {
      try {
        const resp = await fetchCameras(token);
        if (!active) return;
        setCameras(resp.items);
      } catch {
        // keep previous values
      }
    };
    loadCameras();
    return () => {
      active = false;
    };
  }, [token]);

  useEffect(() => {
    let active = true;
    const loadHistory = async () => {
      setHistoryLoading(true);
      try {
        const resp = await fetchSearchHistory(token, 12, role === 'admin' && historyScope === 'all');
        if (!active) return;
        setSearchHistory(resp.items);
      } catch {
        if (!active) return;
      } finally {
        if (active) setHistoryLoading(false);
      }
    };
    void loadHistory();
    return () => {
      active = false;
    };
  }, [historyScope, role, token]);

  useEffect(() => {
    return () => {
      if (queryPreviewUrl) URL.revokeObjectURL(queryPreviewUrl);
    };
  }, [queryPreviewUrl]);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  useEffect(() => {
    resultThumbsRef.current = resultThumbs;
  }, [resultThumbs]);

  useEffect(() => {
    setFeedbackNote('');
    setFeedbackMessage(null);
  }, [detailItem?.track_id]);

  useEffect(() => {
    return () => {
      Object.values(resultThumbsRef.current).forEach((url) => URL.revokeObjectURL(url));
    };
  }, []);

  useEffect(() => {
    let active = true;
    const missing = pagedResults.filter((item) => !resultThumbs[`${item.track_id}:${item.target_key ?? ''}`]);
    if (missing.length === 0) return () => {
      active = false;
    };
    void Promise.allSettled(
      missing.map(async (item) => {
        const thumbKey = `${item.track_id}:${item.target_key ?? ''}`;
        const primaryEvidence = item.evidence?.[0];
        const blob = await fetchCapturePhoto(
          token,
          String(primaryEvidence?.image_path ?? item.image_path ?? ''),
          primaryEvidence?.track_id ?? item.track_id,
        );
        if (!active) return;
        const thumbUrl = await buildResultThumb(blob, primaryEvidence?.person_bbox ?? item.person_bbox);
        if (!active) {
          URL.revokeObjectURL(thumbUrl);
          return;
        }
        setResultThumbs((prev) => {
          if (prev[thumbKey]) {
            URL.revokeObjectURL(thumbUrl);
            return prev;
          }
          return { ...prev, [thumbKey]: thumbUrl };
        });
      }),
    );
    return () => {
      active = false;
    };
  }, [pagedResults, resultThumbs, token]);

  function handleQueryFileChange(file: File | null) {
    setImageFile(file);
    setQueryPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return file ? URL.createObjectURL(file) : null;
    });
  }

  function handleResetFilters() {
    const range = getDefaultQueryRange();
    setUpperColor('');
    setLowerColor('');
    setTimeStart(range.start);
    setTimeEnd(range.end);
    setHasHat('');
    setCameraFilter('');
    setImageMode('');
    setPoseHint('');
    setMinQualityScore('');
    setGroupByTarget(true);
    setDiverseCamera(true);
    setTopK(12);
    setShowAdvanced(false);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!imageFile) {
      setError('请先上传照片');
      return;
    }

    const fd = new FormData();
    fd.append('image', imageFile);
    if (upperColor) fd.append('upper_color', upperColor);
    if (lowerColor) fd.append('lower_color', lowerColor);
    const fallbackRange = getDefaultQueryRange();
    fd.append('time_start', (timeStart || fallbackRange.start).trim());
    fd.append('time_end', (timeEnd || fallbackRange.end).trim());
    if (hasHat === 'true') fd.append('has_hat', 'true');
    if (hasHat === 'false') fd.append('has_hat', 'false');
    if (cameraFilter.trim()) fd.append('camera_id', cameraFilter.trim());
    if (imageMode) fd.append('image_mode', imageMode);
    if (poseHint) fd.append('pose_hint', poseHint);
    if (minQualityScore.trim()) fd.append('min_quality_score', minQualityScore.trim());
    fd.append('face_mode', 'assist');
    fd.append('group_by_target', groupByTarget ? 'true' : 'false');
    fd.append('diverse_camera', diverseCamera ? 'true' : 'false');
    fd.append('top_k', String(Math.max(1, Math.min(100, topK))));

    setLoading(true);
    try {
      const data = await onSearch(fd);
      clearResultThumbs();
      setResult(data);
      setPage(1);
      setShowDiagnostics(false);
      setDetailItem(null);
      try {
        const historyResp = await fetchSearchHistory(token, 12, role === 'admin' && historyScope === 'all');
        setSearchHistory(historyResp.items);
      } catch {
        // keep previous history
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  function exportCsv() {
    if (!sortedResults.length) return;
    const header = [
      'track_id',
      'target_key',
      'similarity',
      'body_sim',
      'upper_sim',
      'lower_sim',
      'face_sim',
      'attr_score',
      'camera_id',
      'start_time',
      'upper_color',
      'lower_color',
      'quality_score',
      'pose_hint',
      'face_used',
      'face_available',
      'image_path',
    ];
    const rows = sortedResults.map((item) => [
      item.track_id,
      item.target_key ?? '',
      item.similarity,
      item.body_sim,
      item.upper_sim ?? '',
      item.lower_sim ?? '',
      item.face_sim ?? '',
      item.attr_score,
      item.camera_id,
      item.start_time,
      item.upper_color,
      item.lower_color,
      item.quality_score ?? '',
      item.pose_hint ?? '',
      item.face_used ?? '',
      item.face_available ?? '',
      item.image_path ?? '',
    ]);
    const csv = [header, ...rows]
      .map((line) => line.map((v) => `"${String(v ?? '').replace(/"/g, '""')}"`).join(','))
      .join('\n');
    const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `search_results_${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

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

  async function handleFeedback(verdict: 'hit' | 'miss') {
    if (!result?.query_id || !detailItem?.track_id) return;
    setFeedbackBusy(true);
    setError(null);
    setFeedbackMessage(null);
    try {
      const resp = await submitSearchFeedback(token, result.query_id, detailItem.track_id, verdict, feedbackNote);
      setFeedbackMessage(
        `${resp.status === 'stored' ? '反馈已记录' : '反馈已提交'}: ${verdict === 'hit' ? '相似' : '不相似'}`,
      );
      if (feedbackNote.trim()) setFeedbackNote('');
      try {
        const historyResp = await fetchSearchHistory(token, 12, role === 'admin' && historyScope === 'all');
        setSearchHistory(historyResp.items);
      } catch {
        // keep previous history
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setFeedbackBusy(false);
    }
  }

  function applyHistoryFilters(item: SearchHistoryItem) {
    setUpperColor(item.upper_color ?? '');
    setLowerColor(item.lower_color ?? '');
    setTimeStart(toDatetimeLocalInput(item.time_start));
    setTimeEnd(toDatetimeLocalInput(item.time_end));
    setHasHat(
      typeof item.has_hat === 'boolean'
        ? item.has_hat
          ? 'true'
          : 'false'
        : '',
    );
    setCameraFilter(item.camera_id ?? '');
    setImageMode(item.image_mode ?? '');
    setPoseHint(item.pose_hint ?? '');
    setMinQualityScore(
      typeof item.min_quality_score === 'number' ? String(item.min_quality_score) : '',
    );
    setGroupByTarget(item.group_by_target ?? true);
    setDiverseCamera(item.diverse_camera ?? true);
    setTopK(item.top_k > 0 ? item.top_k : 12);
    setShowAdvanced(true);
  }

  return (
    <main className="search-page">
      <section className="card search-workbench-card">
        <div className="search-workbench">
          <section className="search-query-panel">
            <div className="search-panel-head">
              <div>
                <p className="eyebrow">Search</p>
                <h2>人体检索工作台</h2>
                <p className="muted">先上传查询图，再按时间和摄像头缩小范围。颜色、帽子、图像模式默认作为辅助条件。</p>
              </div>
            </div>
            <form onSubmit={handleSubmit} className="search-form">
              <div className="search-upload-panel">
                <label>
                  查询图片
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/*"
                    className="hidden-file-input"
                    onChange={(e) => handleQueryFileChange(e.target.files?.[0] ?? null)}
                  />
                  <div className="upload-widget">
                    <button
                      type="button"
                      className="upload-btn"
                      onClick={() => fileInputRef.current?.click()}
                    >
                      {imageFile ? '重新选择图片' : '上传检索图片'}
                    </button>
                    <span className="upload-name">{imageFile?.name ?? '未选择文件'}</span>
                    {queryPreviewUrl ? (
                      <div className="query-preview">
                        <img src={queryPreviewUrl} alt="query preview" />
                      </div>
                    ) : (
                      <div className="search-query-empty">
                        <p>上传一张人的照片做查询入口</p>
                        <p className="muted">默认开启人体混合检索 + 人脸辅助重排</p>
                      </div>
                    )}
                  </div>
                </label>
              </div>

              <div className="search-primary-grid">
                <label>
                  开始时间
                  <input type="datetime-local" value={timeStart} onChange={(e) => setTimeStart(e.target.value)} />
                </label>
                <label>
                  结束时间
                  <input type="datetime-local" value={timeEnd} onChange={(e) => setTimeEnd(e.target.value)} />
                </label>
                <label>
                  摄像头范围
                  <select value={cameraFilter} onChange={(e) => setCameraFilter(e.target.value)}>
                    <option value="">全部摄像头</option>
                    {cameraOptions.map((camera) => (
                      <option key={camera.value} value={camera.value}>
                        {camera.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Top K
                  <input
                    type="number"
                    min={1}
                    max={100}
                    step={1}
                    value={topK}
                    onChange={(e) => setTopK(Number.parseInt(e.target.value, 10) || 12)}
                  />
                </label>
              </div>

              <div className="search-query-actions">
                <button disabled={loading}>{loading ? '搜索中...' : '开始搜索'}</button>
                <button type="button" className="ghost-btn" onClick={handleResetFilters} disabled={loading}>
                  重置条件
                </button>
              </div>

              <div className="search-query-hints">
                <span>默认: 按目标聚合</span>
                <span>默认: 跨摄像头均衡</span>
                <span>人脸模式: 辅助重排</span>
              </div>

              <button
                type="button"
                className="ghost-btn search-advanced-toggle"
                onClick={() => setShowAdvanced((prev) => !prev)}
              >
                {showAdvanced ? '收起高级筛选' : '展开高级筛选'}
              </button>

              {showAdvanced ? (
                <div className="search-advanced-grid">
                  <label>
                    上衣颜色
                    <select value={upperColor} onChange={(e) => setUpperColor(e.target.value)}>
                      {COLORS.map((c) => (
                        <option key={c} value={c}>
                          {COLOR_LABELS[c] ?? c}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    下装颜色
                    <select value={lowerColor} onChange={(e) => setLowerColor(e.target.value)}>
                      {COLORS.map((c) => (
                        <option key={c} value={c}>
                          {COLOR_LABELS[c] ?? c}
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
                      {POSE_HINTS.map((p) => (
                        <option key={p} value={p}>
                          {POSE_LABELS[p] ?? p}
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
                  <label className="search-inline-check">
                    <span>按目标去重</span>
                    <input
                      type="checkbox"
                      checked={groupByTarget}
                      onChange={(e) => setGroupByTarget(e.target.checked)}
                    />
                  </label>
                  <label className="search-inline-check">
                    <span>跨摄像头均衡</span>
                    <input
                      type="checkbox"
                      checked={diverseCamera}
                      onChange={(e) => setDiverseCamera(e.target.checked)}
                    />
                  </label>
                </div>
              ) : null}
            </form>
            {error ? <p className="error">{error}</p> : null}

            <section className="search-history-section">
              <div className="search-panel-head search-history-head">
                <div>
                  <p className="eyebrow">History</p>
                  <h2>最近查询</h2>
                  <p className="muted">保留最近的检索条件、结果数量和人工反馈统计，可直接回填筛选条件。</p>
                </div>
                {role === 'admin' ? (
                  <label>
                    范围
                    <select value={historyScope} onChange={(e) => setHistoryScope(e.target.value as 'mine' | 'all')}>
                      <option value="mine">仅自己</option>
                      <option value="all">全部用户</option>
                    </select>
                  </label>
                ) : null}
              </div>

              {historyLoading ? (
                <div className="search-history-empty">加载查询历史...</div>
              ) : searchHistory.length === 0 ? (
                <div className="search-history-empty">暂无查询历史</div>
              ) : (
                <div className="search-history-list">
                  {searchHistory.map((item) => (
                    <article key={item.query_id} className="search-history-item">
                      <div className="search-history-item-head">
                        <div>
                          <strong>#{item.query_id.slice(0, 8)}</strong>
                          <span>{new Date(item.created_at).toLocaleString()}</span>
                        </div>
                        <button type="button" className="ghost-btn" onClick={() => applyHistoryFilters(item)}>
                          回填条件
                        </button>
                      </div>
                      <div className="search-chip-row">
                        <span className="search-chip">结果 {item.result_count}</span>
                        <span className="search-chip">耗时 {item.elapsed_ms}ms</span>
                        <span className="search-chip">相似 {item.hit_count}</span>
                        <span className="search-chip">误报 {item.miss_count}</span>
                      </div>
                      <div className="search-history-meta">
                        <span>用户: {item.created_by}</span>
                        <span>摄像头: {item.camera_id || '全部'}</span>
                        <span>图像模式: {IMAGE_MODE_LABELS[item.image_mode ?? ''] ?? item.image_mode ?? '不限'}</span>
                        <span>TopK: {item.top_k}</span>
                      </div>
                      <div className="search-history-meta">
                        <span>时间: {item.time_start ? new Date(item.time_start).toLocaleString() : '-'}</span>
                        <span>到 {item.time_end ? new Date(item.time_end).toLocaleString() : '-'}</span>
                      </div>
                      <div className="search-history-meta">
                        <span>上衣: {COLOR_LABELS[item.upper_color ?? ''] ?? item.upper_color ?? '不限'}</span>
                        <span>下装: {COLOR_LABELS[item.lower_color ?? ''] ?? item.lower_color ?? '不限'}</span>
                        <span>帽子: {boolText(item.has_hat, '戴帽', '不戴帽')}</span>
                        <span>姿态: {POSE_LABELS[item.pose_hint ?? ''] ?? item.pose_hint ?? '不限'}</span>
                      </div>
                      <div className="search-history-meta">
                        <span>按目标去重: {boolText(item.group_by_target)}</span>
                        <span>跨摄像头均衡: {boolText(item.diverse_camera)}</span>
                        <span>
                          人脸辅助:
                          {item.face_mode === 'assist' ? ' 开启' : ' 关闭'}
                        </span>
                        <span>
                          最近反馈:
                          {item.latest_feedback_at ? ` ${new Date(item.latest_feedback_at).toLocaleString()}` : ' -'}
                        </span>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </section>
          </section>

          <section className="search-results-panel">
            <div className="search-panel-head search-results-head">
              <div>
                <p className="eyebrow">Results</p>
                <h2>检索结果</h2>
                <p className="muted">结果按目标输出，先看相似度，再结合时间、摄像头和细节确认。</p>
              </div>
              {result ? (
                <div className="search-result-toolbar">
                  <label>
                    排序
                    <select value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
                      {SORT_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    每页
                    <select value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))}>
                      {[12, 24, 48].map((v) => (
                        <option key={v} value={v}>
                          {v}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button type="button" className="ghost-btn" onClick={exportCsv}>
                    导出CSV
                  </button>
                </div>
              ) : null}
            </div>

            {!result ? (
              <div className="search-empty-state">
                <h3>等待查询</h3>
                <p>上传查询图后开始搜索。建议先限定时间范围，再逐步叠加高级条件。</p>
              </div>
            ) : (
              <>
                <div className="search-summary-grid">
                  <article className="search-summary-card">
                    <span>命中目标</span>
                    <strong>{result.count}</strong>
                    <small>按目标聚合后的最终返回数</small>
                  </article>
                  <article className="search-summary-card">
                    <span>最高相似度</span>
                    <strong>{formatPercent(resultOverview.topSimilarity)}</strong>
                    <small>当前排序下的第一名结果</small>
                  </article>
                  <article className="search-summary-card">
                    <span>覆盖摄像头</span>
                    <strong>{resultOverview.cameraCount}</strong>
                    <small>结果分布到的摄像头数量</small>
                  </article>
                  <article className="search-summary-card">
                    <span>证据条数</span>
                    <strong>{resultOverview.evidenceCount}</strong>
                    <small>当前结果关联的证据总数</small>
                  </article>
                </div>

                <section className="search-diagnostics-card">
                  <div className="search-diagnostics-head">
                    <div>
                      <p className="search-result-kicker">诊断</p>
                      <h3>检索诊断</h3>
                      <p className="muted">算法漏斗、重排和时间分布放在这里，避免压过结果本身。</p>
                    </div>
                    <button type="button" className="ghost-btn" onClick={() => setShowDiagnostics((prev) => !prev)}>
                      {showDiagnostics ? '收起诊断' : '展开诊断'}
                    </button>
                  </div>
                  {showDiagnostics ? (
                    <>
                      <div className="search-diagnostics-grid">
                        <article className="search-diagnostic-item">
                          <span>查询耗时</span>
                          <strong>{result.elapsed_ms}ms</strong>
                          <small>本次检索总耗时</small>
                        </article>
                        <article className="search-diagnostic-item">
                          <span>粗筛漏斗</span>
                          <strong>
                            {result.funnel.layer1_count} → {result.funnel.layer2_count} → {result.funnel.layer3_count}
                          </strong>
                          <small>候选库 → 过滤后 → 最终结果</small>
                        </article>
                        <article className="search-diagnostic-item">
                          <span>候选收缩</span>
                          <strong>{formatPercent(searchMetrics.candidate_reduction_rate)}</strong>
                          <small>粗筛后候选减少比例</small>
                        </article>
                        <article className="search-diagnostic-item">
                          <span>人脸辅助</span>
                          <strong>{(searchMetrics.face_assist_used ?? 0) > 0 ? '已启用' : '未启用'}</strong>
                          <small>{(searchMetrics.query_has_face ?? 0) > 0 ? '查询图检测到可用人脸' : '查询图未检测到可用人脸'}</small>
                        </article>
                        <article className="search-diagnostic-item">
                          <span>重排候选</span>
                          <strong>{Math.round(searchMetrics.reranked_count ?? 0)}</strong>
                          <small>二阶段精排参与数量</small>
                        </article>
                      </div>

                      {timelineRows.length > 0 ? (
                        <div className="timeline">
                          <h3>时间分布</h3>
                          {timelineRows.map(([hour, count]) => (
                            <div className="timeline-row" key={hour}>
                              <span>{hour.padStart(2, '0')}:00</span>
                              <div className="bar" style={{ width: `${Math.max(8, count * 20)}px` }} />
                              <span>{count}</span>
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </>
                  ) : null}
                </section>

                {pagedResults.length === 0 ? (
                  <div className="search-empty-state">
                    <h3>没有符合条件的结果</h3>
                    <p>先放宽颜色、姿态、夜间这些辅助条件，再重新搜索。</p>
                  </div>
                ) : (
                  <div className="search-result-grid">
                    {pagedResults.map((item, idx) => (
                      <article
                        className={(page - 1) * pageSize + idx < 3 ? 'search-result-card spotlight' : 'search-result-card'}
                        key={`${item.track_id}-${item.image_path ?? ''}-${idx}`}
                      >
                        <div className="search-result-frame">
                          <span className="search-rank-badge">#{(page - 1) * pageSize + idx + 1}</span>
                          <button
                            type="button"
                            className="search-result-figure"
                            onClick={() => void handlePreview(String(item.image_path ?? ''), item.track_id)}
                            disabled={(!item.image_path && !item.track_id) || previewLoading}
                          >
                            {resultThumbs[`${item.track_id}:${item.target_key ?? ''}`] ? (
                              <img
                                src={resultThumbs[`${item.track_id}:${item.target_key ?? ''}`]}
                                alt={item.target_key ?? `track_${item.track_id}`}
                              />
                            ) : (
                              <div className="search-result-figure-placeholder">加载人物主图...</div>
                            )}
                            <div className="search-figure-overlay">
                              <div className="search-score-pill">{formatPercent(item.similarity)}</div>
                              <span className="search-figure-camera">{item.camera_id}</span>
                            </div>
                          </button>
                        </div>
                        <div className="search-result-card-head">
                          <div>
                            <p className="search-result-kicker">{item.target_key ? '目标组' : 'Track'}</p>
                            <h3>{item.target_key ?? `track_${item.track_id}`}</h3>
                            <p className="muted">{new Date(item.start_time).toLocaleString()}</p>
                          </div>
                          <span className="search-result-count">{item.evidence_count ?? item.evidence?.length ?? 0} 条证据</span>
                        </div>

                        <div className="search-result-stats">
                          <div>
                            <span>上 / 下装</span>
                            <strong>
                              {summarizeColor(item.upper_color)} / {summarizeColor(item.lower_color)}
                            </strong>
                          </div>
                          <div>
                            <span>质量</span>
                            <strong>{item.quality_score !== null && item.quality_score !== undefined ? item.quality_score.toFixed(3) : '-'}</strong>
                          </div>
                          <div>
                            <span>姿态</span>
                            <strong>{summarizePose(item.pose_hint)}</strong>
                          </div>
                          <div>
                            <span>图像模式</span>
                            <strong>{summarizeMode(item.image_mode)}</strong>
                          </div>
                        </div>

                        <div className="search-badge-row">
                          <span className="search-badge">Body {formatPercent(item.body_sim)}</span>
                          {item.upper_sim !== null && item.upper_sim !== undefined ? (
                            <span className="search-badge">Upper {formatPercent(item.upper_sim)}</span>
                          ) : null}
                          {item.lower_sim !== null && item.lower_sim !== undefined ? (
                            <span className="search-badge">Lower {formatPercent(item.lower_sim)}</span>
                          ) : null}
                          {item.face_used ? (
                            <span className="search-badge accent">
                              Face {formatPercent(item.face_sim)}
                            </span>
                          ) : item.face_available ? (
                            <span className="search-badge">候选含人脸</span>
                          ) : null}
                          <span className="search-badge">{boolText(item.has_hat, '戴帽', '无帽')}</span>
                        </div>

                        <div className="search-result-note">先看人物主图和相似度，再通过时间、摄像头与证据确认是否同一目标。</div>

                        <div className="search-evidence-block">
                          <div className="search-evidence-head">
                            <span>同目标证据</span>
                            <strong>{item.evidence_count ?? item.evidence?.length ?? 0} 条</strong>
                          </div>
                          {item.evidence && item.evidence.length > 0 ? (
                            <div className="search-evidence-list compact">
                              {item.evidence.slice(0, 3).map((evidence, evidenceIndex) => (
                                <button
                                  key={`${item.track_id}-${evidence.track_id}-${evidence.start_time}`}
                                  type="button"
                                  className="search-evidence-item"
                                  onClick={() => void handlePreview(String(evidence.image_path ?? ''), evidence.track_id)}
                                  disabled={previewLoading}
                                >
                                  <span>证据 {evidenceIndex + 1}</span>
                                  <strong>{evidence.camera_id}</strong>
                                  <small>
                                    {new Date(evidence.start_time).toLocaleString()} · {formatPercent(evidence.similarity)}
                                  </small>
                                </button>
                              ))}
                            </div>
                          ) : (
                            <p className="muted">当前目标暂无附加证据</p>
                          )}
                        </div>

                        <div className="search-card-actions">
                          <button type="button" className="ghost-btn" onClick={() => setDetailItem(item)}>
                            查看详情
                          </button>
                          <button
                            type="button"
                            onClick={() => void handlePreview(String(item.image_path ?? ''), item.track_id)}
                            disabled={(!item.image_path && !item.track_id) || previewLoading}
                          >
                            {previewLoading ? '加载中...' : '查看原图'}
                          </button>
                        </div>
                      </article>
                    ))}
                  </div>
                )}

                <div className="search-pagination">
                  <button type="button" className="ghost-btn" onClick={() => setPage(1)} disabled={page <= 1}>
                    首页
                  </button>
                  <button
                    type="button"
                    className="ghost-btn"
                    onClick={() => setPage((v) => Math.max(1, v - 1))}
                    disabled={page <= 1}
                  >
                    上一页
                  </button>
                  <span>
                    第 {Math.min(page, totalPages)} / {totalPages} 页
                  </span>
                  <button
                    type="button"
                    className="ghost-btn"
                    onClick={() => setPage((v) => Math.min(totalPages, v + 1))}
                    disabled={page >= totalPages}
                  >
                    下一页
                  </button>
                  <button
                    type="button"
                    className="ghost-btn"
                    onClick={() => setPage(totalPages)}
                    disabled={page >= totalPages}
                  >
                    末页
                  </button>
                </div>

              </>
            )}
          </section>
        </div>
      </section>

      {previewUrl ? (
        <section className="capture-preview-overlay" onClick={closePreview}>
          <div className="capture-preview-dialog card" onClick={(e) => e.stopPropagation()}>
            <div className="capture-preview-head">
              <h3>检索结果预览</h3>
              <button type="button" onClick={closePreview}>
                关闭
              </button>
            </div>
            <p className="muted">{previewPath}</p>
            <img className="capture-preview-image" src={previewUrl} alt="search result preview" />
          </div>
        </section>
      ) : null}
      {detailItem ? (
        <section className="capture-preview-overlay" onClick={() => setDetailItem(null)}>
          <div className="capture-preview-dialog card" onClick={(e) => e.stopPropagation()}>
            <div className="capture-preview-head">
              <h3>检索结果详情</h3>
              <button type="button" onClick={() => setDetailItem(null)}>
                关闭
              </button>
            </div>
            <div className="search-detail-grid">
              <p>Track: {detailItem.track_id}</p>
              <p>目标键: {detailItem.target_key ?? '-'}</p>
              <p>相似度: {formatPercent(detailItem.similarity, 2)}</p>
              <p>Body: {formatPercent(detailItem.body_sim, 2)}</p>
              <p>Upper: {detailItem.upper_sim !== null && detailItem.upper_sim !== undefined ? formatPercent(detailItem.upper_sim, 2) : '-'}</p>
              <p>Lower: {detailItem.lower_sim !== null && detailItem.lower_sim !== undefined ? formatPercent(detailItem.lower_sim, 2) : '-'}</p>
              <p>Face: {detailItem.face_sim !== null && detailItem.face_sim !== undefined ? formatPercent(detailItem.face_sim, 2) : '-'}</p>
              <p>Attr: {formatPercent(detailItem.attr_score, 2)}</p>
              <p>摄像头: {detailItem.camera_id}</p>
              <p>时间: {new Date(detailItem.start_time).toLocaleString()}</p>
              <p>上 / 下装: {summarizeColor(detailItem.upper_color)} / {summarizeColor(detailItem.lower_color)}</p>
              <p>图像模式: {summarizeMode(detailItem.image_mode)}</p>
              <p>质量: {detailItem.quality_score !== null && detailItem.quality_score !== undefined ? detailItem.quality_score.toFixed(3) : '-'}</p>
              <p>姿态: {summarizePose(detailItem.pose_hint)}</p>
              <p>帽子: {boolText(detailItem.has_hat, '戴帽', '无帽')}</p>
              <p>人脸辅助: {boolText(detailItem.face_used, '已使用', '未使用')}</p>
              <p>候选有人脸: {boolText(detailItem.face_available)}</p>
              <p>证据数: {detailItem.evidence_count ?? detailItem.evidence?.length ?? 0}</p>
              <p className="capture-path-cell">路径: {detailItem.image_path ?? '-'}</p>
            </div>
            <div className="search-feedback-box">
              <div className="search-feedback-head">
                <h4>人工反馈</h4>
                {feedbackMessage ? <span className="muted">{feedbackMessage}</span> : null}
              </div>
              <textarea
                value={feedbackNote}
                onChange={(e) => setFeedbackNote(e.target.value)}
                rows={2}
                placeholder="可选备注，例如：衣服相似但不是同一人"
              />
              <div className="search-card-actions">
                <button type="button" onClick={() => void handleFeedback('hit')} disabled={feedbackBusy}>
                  {feedbackBusy ? '提交中...' : '标记为相似'}
                </button>
                <button type="button" className="ghost-btn" onClick={() => void handleFeedback('miss')} disabled={feedbackBusy}>
                  {feedbackBusy ? '提交中...' : '标记为不相似'}
                </button>
              </div>
            </div>
            <div className="search-detail-evidence">
              <h4>证据时间线</h4>
              {detailItem.evidence && detailItem.evidence.length > 0 ? (
                <div className="search-timeline-list">
                  {[...detailItem.evidence]
                    .sort((a, b) => new Date(a.start_time).getTime() - new Date(b.start_time).getTime())
                    .map((evidence) => (
                      <button
                        key={`${detailItem.track_id}-${evidence.track_id}-${evidence.start_time}`}
                        type="button"
                        className="search-timeline-item"
                        onClick={() => void handlePreview(String(evidence.image_path ?? ''), evidence.track_id)}
                        disabled={previewLoading}
                      >
                        <span className="search-timeline-dot" />
                        <div className="search-timeline-body">
                          <strong>{new Date(evidence.start_time).toLocaleString()}</strong>
                          <p>{evidence.camera_id}</p>
                          <small>
                            sim {formatPercent(evidence.similarity)} / body {formatPercent(evidence.body_sim)}
                          </small>
                        </div>
                      </button>
                    ))}
                </div>
              ) : (
                <p className="muted">暂无证据图</p>
              )}
            </div>
          </div>
        </section>
      ) : null}
    </main>
  );
}
