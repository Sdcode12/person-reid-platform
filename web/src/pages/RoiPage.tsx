import { type MouseEvent, useCallback, useEffect, useRef, useState } from 'react';

import {
  fetchCameraRoi,
  fetchCameras,
  fetchCameraSnapshot,
  saveCameraRoi,
  testCameraRoi,
} from '../api/client';
import type {
  CameraRoiConfig,
  CameraRoiTestResult,
  CameraStatusItem,
  RoiPoint,
  RoiPolygon,
} from '../api/types';

interface Props {
  token: string;
  role: string;
}

const DRAW_INCLUDE = 'include';
const DRAW_EXCLUDE = 'exclude';

function emptyRoiConfig(cameraId: string): CameraRoiConfig {
  return {
    camera_id: cameraId,
    include: [],
    exclude: [],
    updated_by: 'system',
    updated_at: null,
  };
}

function drawPolygon(
  ctx: CanvasRenderingContext2D,
  polygon: RoiPolygon,
  width: number,
  height: number,
  stroke: string,
  fill: string,
) {
  if (polygon.points.length < 2) return;
  ctx.beginPath();
  polygon.points.forEach((point, index) => {
    const x = point.x * width;
    const y = point.y * height;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.closePath();
  ctx.strokeStyle = stroke;
  ctx.fillStyle = fill;
  ctx.lineWidth = 2;
  ctx.fill();
  ctx.stroke();
}

export default function RoiPage({ token, role }: Props) {
  const [cameras, setCameras] = useState<CameraStatusItem[]>([]);
  const [selectedCameraId, setSelectedCameraId] = useState('');
  const [roiConfig, setRoiConfig] = useState<CameraRoiConfig>(emptyRoiConfig(''));
  const [drawMode, setDrawMode] = useState<typeof DRAW_INCLUDE | typeof DRAW_EXCLUDE>(DRAW_INCLUDE);
  const [draftPoints, setDraftPoints] = useState<RoiPoint[]>([]);
  const [roiError, setRoiError] = useState<string | null>(null);
  const [roiLoading, setRoiLoading] = useState(false);
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const [snapshotUrl, setSnapshotUrl] = useState<string | null>(null);
  const [roiTest, setRoiTest] = useState<CameraRoiTestResult | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const canEditRoi = role === 'admin' || role === 'operator';

  const drawCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    const img = imageRef.current;
    if (!canvas || !img) return;
    const width = Math.max(1, Math.round(img.clientWidth));
    const height = Math.max(1, Math.round(img.clientHeight));
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    ctx.clearRect(0, 0, width, height);
    roiConfig.include.forEach((polygon) =>
      drawPolygon(ctx, polygon, width, height, 'rgba(22, 163, 74, 1)', 'rgba(22, 163, 74, 0.18)'),
    );
    roiConfig.exclude.forEach((polygon) =>
      drawPolygon(ctx, polygon, width, height, 'rgba(220, 38, 38, 1)', 'rgba(220, 38, 38, 0.18)'),
    );

    if (draftPoints.length > 0) {
      ctx.beginPath();
      draftPoints.forEach((point, index) => {
        const x = point.x * width;
        const y = point.y * height;
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = drawMode === DRAW_INCLUDE ? 'rgba(22, 163, 74, 1)' : 'rgba(220, 38, 38, 1)';
      ctx.setLineDash([6, 4]);
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.setLineDash([]);
      draftPoints.forEach((point) => {
        const x = point.x * width;
        const y = point.y * height;
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fillStyle = '#f59e0b';
        ctx.fill();
      });
    }
  }, [draftPoints, drawMode, roiConfig.exclude, roiConfig.include]);

  useEffect(() => {
    drawCanvas();
  }, [drawCanvas, snapshotUrl]);

  useEffect(() => {
    const handleResize = () => drawCanvas();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [drawCanvas]);

  useEffect(() => {
    let active = true;
    const loadCameras = async () => {
      try {
        const resp = await fetchCameras(token);
        if (!active) return;
        setCameras(resp.items);
        if (resp.items.length > 0) {
          setSelectedCameraId((prev) => (prev ? prev : resp.items[0].camera_id));
        }
      } catch (err) {
        if (active) setRoiError((err as Error).message);
      }
    };
    void loadCameras();
    return () => {
      active = false;
    };
  }, [token]);

  useEffect(() => {
    let active = true;
    const loadRoi = async () => {
      if (!selectedCameraId) return;
      setRoiError(null);
      try {
        const config = await fetchCameraRoi(token, selectedCameraId);
        if (active) setRoiConfig(config);
      } catch (err) {
        if (active) {
          setRoiError((err as Error).message);
          setRoiConfig(emptyRoiConfig(selectedCameraId));
        }
      }
    };
    void loadRoi();
    setDraftPoints([]);
    setRoiTest(null);
    return () => {
      active = false;
    };
  }, [selectedCameraId, token]);

  useEffect(() => {
    return () => {
      if (snapshotUrl) URL.revokeObjectURL(snapshotUrl);
    };
  }, [snapshotUrl]);

  const handleRefreshSnapshot = useCallback(async () => {
    if (!selectedCameraId) return;
    setSnapshotLoading(true);
    setRoiError(null);
    try {
      const blob = await fetchCameraSnapshot(token, selectedCameraId, false);
      const nextUrl = URL.createObjectURL(blob);
      setSnapshotUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return nextUrl;
      });
    } catch (err) {
      setRoiError((err as Error).message);
    } finally {
      setSnapshotLoading(false);
    }
  }, [selectedCameraId, token]);

  useEffect(() => {
    void handleRefreshSnapshot();
  }, [handleRefreshSnapshot]);

  function handleCanvasClick(e: MouseEvent<HTMLCanvasElement>) {
    if (!canEditRoi) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;

    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    const point = {
      x: Math.max(0, Math.min(1, Number(x.toFixed(6)))),
      y: Math.max(0, Math.min(1, Number(y.toFixed(6)))),
    };
    setDraftPoints((prev) => [...prev, point]);
  }

  function handleCommitPolygon() {
    if (!canEditRoi) return;
    if (draftPoints.length < 3) {
      setRoiError('至少需要 3 个点才能形成区域');
      return;
    }
    const polygon: RoiPolygon = { points: draftPoints };
    setRoiConfig((prev) =>
      drawMode === DRAW_INCLUDE
        ? { ...prev, include: [...prev.include, polygon] }
        : { ...prev, exclude: [...prev.exclude, polygon] },
    );
    setDraftPoints([]);
    setRoiError(null);
  }

  function handleClearModePolygons() {
    if (!canEditRoi) return;
    setRoiConfig((prev) =>
      drawMode === DRAW_INCLUDE ? { ...prev, include: [] } : { ...prev, exclude: [] },
    );
    setDraftPoints([]);
  }

  function handleUndoPoint() {
    if (!canEditRoi) return;
    setDraftPoints((prev) => prev.slice(0, -1));
  }

  async function handleSaveRoi() {
    if (!selectedCameraId || !canEditRoi) return;
    setRoiLoading(true);
    setRoiError(null);
    try {
      const saved = await saveCameraRoi(token, selectedCameraId, {
        include: roiConfig.include,
        exclude: roiConfig.exclude,
      });
      setRoiConfig(saved);
    } catch (err) {
      setRoiError((err as Error).message);
    } finally {
      setRoiLoading(false);
    }
  }

  async function handleTestRoi() {
    if (!selectedCameraId) return;
    setRoiLoading(true);
    setRoiError(null);
    try {
      const tested = await testCameraRoi(token, selectedCameraId);
      setRoiTest(tested);
    } catch (err) {
      setRoiError((err as Error).message);
    } finally {
      setRoiLoading(false);
    }
  }

  return (
    <main className="roi-page">
      <section className="card roi-card">
        <div className="search-panel-head">
          <div>
            <p className="eyebrow">ROI</p>
            <h2>ROI 过滤配置</h2>
            <p className="muted">在快照上点击画区域。绿色 include 表示保留区域，红色 exclude 表示排除区域。</p>
          </div>
        </div>
        <div className="roi-toolbar">
          <label>
            摄像头
            <select
              value={selectedCameraId}
              onChange={(e) => setSelectedCameraId(e.target.value)}
              disabled={cameras.length === 0}
            >
              {cameras.length === 0 ? <option value="">暂无可用摄像头</option> : null}
              {cameras.map((camera) => (
                <option key={camera.camera_id} value={camera.camera_id}>
                  {camera.camera_name} ({camera.camera_id})
                </option>
              ))}
            </select>
          </label>
          <button type="button" onClick={() => void handleRefreshSnapshot()} disabled={!selectedCameraId || snapshotLoading}>
            {snapshotLoading ? '刷新中...' : '刷新快照'}
          </button>
          <button type="button" onClick={() => void handleTestRoi()} disabled={!selectedCameraId || roiLoading}>
            {roiLoading ? '测试中...' : '测试过滤'}
          </button>
          <button type="button" onClick={() => void handleSaveRoi()} disabled={!selectedCameraId || roiLoading || !canEditRoi}>
            {roiLoading ? '保存中...' : '保存 ROI'}
          </button>
        </div>

        <div className="roi-layout">
          <div className="roi-stage">
            {snapshotUrl ? (
              <>
                <img ref={imageRef} src={snapshotUrl} alt="camera snapshot" onLoad={drawCanvas} />
                <canvas ref={canvasRef} onClick={handleCanvasClick} />
              </>
            ) : (
              <div className="roi-empty muted">暂无快照</div>
            )}
          </div>
          <aside className="roi-panel">
            <label>
              绘制类型
              <select
                value={drawMode}
                onChange={(e) => setDrawMode(e.target.value as typeof DRAW_INCLUDE | typeof DRAW_EXCLUDE)}
                disabled={!canEditRoi}
              >
                <option value={DRAW_INCLUDE}>include 保留</option>
                <option value={DRAW_EXCLUDE}>exclude 排除</option>
              </select>
            </label>
            <p className="muted">
              include: {roiConfig.include.length} 个，exclude: {roiConfig.exclude.length} 个，当前草稿点: {draftPoints.length}
            </p>
            <div className="roi-actions">
              <button type="button" onClick={handleCommitPolygon} disabled={!canEditRoi}>
                完成当前多边形
              </button>
              <button type="button" onClick={handleUndoPoint} disabled={!canEditRoi || draftPoints.length === 0}>
                撤销一个点
              </button>
              <button type="button" onClick={() => setDraftPoints([])} disabled={!canEditRoi || draftPoints.length === 0}>
                清空草稿
              </button>
              <button type="button" onClick={handleClearModePolygons} disabled={!canEditRoi}>
                清空当前类型
              </button>
            </div>
            <p className="muted">
              最近更新: {roiConfig.updated_by} / {roiConfig.updated_at ? new Date(roiConfig.updated_at).toLocaleString() : '未保存'}
            </p>
            {roiTest ? (
              <p>
                测试结果: 原始 {roiTest.raw_people_count} 人，过滤后 {roiTest.filtered_people_count} 人，丢弃 {roiTest.dropped_count} 人
              </p>
            ) : null}
            {!canEditRoi ? <p className="muted">当前角色为只读，无法保存 ROI。</p> : null}
          </aside>
        </div>
        {roiError ? <p className="error">{roiError}</p> : null}
      </section>
    </main>
  );
}
