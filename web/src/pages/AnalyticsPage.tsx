import { type FormEvent, useCallback, useEffect, useMemo, useState } from 'react';

import { fetchAnalyticsDashboard, fetchCameraConfigs } from '../api/client';
import type {
  AnalyticsDashboardResponse,
  AnalyticsDistributionItem,
  CameraSourceConfigItem,
} from '../api/types';

interface Props {
  token: string;
}

const DONUT_COLORS = ['#2563eb', '#0f766e', '#d97706', '#7c3aed', '#dc2626', '#0891b2', '#6b7280'];

function formatDatetimeLocal(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  const hh = String(date.getHours()).padStart(2, '0');
  const mm = String(date.getMinutes()).padStart(2, '0');
  return `${y}-${m}-${d}T${hh}:${mm}`;
}

function buildPresetRange(days: number): { start: string; end: string } {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - days);
  return {
    start: formatDatetimeLocal(start),
    end: formatDatetimeLocal(end),
  };
}

function toDisplayDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function toIsoInput(value: string): string | undefined {
  if (!value.trim()) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return undefined;
  return date.toISOString();
}

function formatNumber(value: number | null | undefined): string {
  return new Intl.NumberFormat('zh-CN').format(Number(value ?? 0));
}

function formatRatio(value: number | null | undefined): string {
  if (value === null || value === undefined) return '无对比';
  const pct = value * 100;
  const sign = pct > 0 ? '+' : '';
  return `${sign}${pct.toFixed(Math.abs(pct) >= 10 ? 0 : 1)}%`;
}

function sumValues(items: AnalyticsDistributionItem[]): number {
  return items.reduce((acc, item) => acc + Number(item.value || 0), 0);
}

function buildDonutItems(items: AnalyticsDistributionItem[], total: number): AnalyticsDistributionItem[] {
  if (total <= 0) return [];
  const normalized = items.filter((item) => item.value > 0);
  const shown = sumValues(normalized);
  if (shown >= total) return normalized;
  return [
    ...normalized,
    {
      key: 'other',
      label: '其他',
      value: total - shown,
      ratio: (total - shown) / total,
    },
  ];
}

function buildNiceTickStep(maxValue: number): number {
  const safeMax = Math.max(1, maxValue);
  const roughStep = safeMax / 4;
  const magnitude = 10 ** Math.floor(Math.log10(roughStep));
  const normalized = roughStep / magnitude;
  if (normalized <= 1) return magnitude;
  if (normalized <= 2) return 2 * magnitude;
  if (normalized <= 5) return 5 * magnitude;
  return 10 * magnitude;
}

function bucketMs(granularity: string): number {
  if (granularity === 'hour') return 60 * 60 * 1000;
  if (granularity === 'day') return 24 * 60 * 60 * 1000;
  return 7 * 24 * 60 * 60 * 1000;
}

function formatBucketRangeText(
  item: AnalyticsDashboardResponse['trend'][number],
  granularity: string,
  rangeStart: string,
  rangeEnd: string,
): string {
  const bucketStart = new Date(item.bucket_start);
  const bucketEnd = new Date(item.bucket_end);
  const selectedStart = new Date(rangeStart);
  const selectedEnd = new Date(rangeEnd);
  if (Number.isNaN(bucketStart.getTime()) || Number.isNaN(bucketEnd.getTime())) return item.label;
  const effectiveStart =
    !Number.isNaN(selectedStart.getTime()) && bucketStart < selectedStart ? selectedStart : bucketStart;
  const effectiveEndExclusive =
    !Number.isNaN(selectedEnd.getTime()) && bucketEnd > selectedEnd ? selectedEnd : bucketEnd;
  const displayEnd = new Date(Math.max(effectiveStart.getTime(), effectiveEndExclusive.getTime() - 1000));
  const actualSpanMs = Math.max(0, effectiveEndExclusive.getTime() - effectiveStart.getTime());
  const fullBucketMs = bucketMs(granularity);
  if (granularity === 'day' && actualSpanMs >= fullBucketMs - 1000) {
    return `${effectiveStart.toLocaleDateString('zh-CN')} 全天`;
  }
  if (granularity === 'week' && actualSpanMs >= fullBucketMs - 1000) {
    return `${effectiveStart.toLocaleDateString('zh-CN')} ~ ${displayEnd.toLocaleDateString('zh-CN')}`;
  }
  if (granularity === 'hour' && actualSpanMs >= fullBucketMs - 1000) {
    return `${effectiveStart.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })} ~ ${displayEnd.toLocaleString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })}`;
  }
  return `${effectiveStart.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })} ~ ${displayEnd.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })}`;
}

function hasPartialBoundary(
  items: AnalyticsDashboardResponse['trend'],
  granularity: string,
  rangeStart: string,
  rangeEnd: string,
): boolean {
  if (items.length === 0 || granularity === 'hour') return false;
  const firstBucketStart = new Date(items[0].bucket_start);
  const lastBucketEnd = new Date(items[items.length - 1].bucket_end);
  const selectedStart = new Date(rangeStart);
  const selectedEnd = new Date(rangeEnd);
  if ([firstBucketStart, lastBucketEnd, selectedStart, selectedEnd].some((item) => Number.isNaN(item.getTime()))) {
    return false;
  }
  return selectedStart.getTime() > firstBucketStart.getTime() || selectedEnd.getTime() < lastBucketEnd.getTime();
}

function partialBoundaryNote(granularity: string): string {
  if (granularity === 'week') {
    return '首尾时间段按实际筛选范围截断，不代表完整自然周。';
  }
  return '首尾时间段按实际筛选范围截断，不代表完整自然日。';
}

function TrendChart({
  items,
  granularity,
  rangeStart,
  rangeEnd,
}: {
  items: AnalyticsDashboardResponse['trend'];
  granularity: string;
  rangeStart: string;
  rangeEnd: string;
}) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  if (items.length === 0) {
    return <div className="analytics-empty-chart">当前筛选范围内暂无趋势数据</div>;
  }
  const width = 900;
  const height = 280;
  const padLeft = 44;
  const padRight = 18;
  const padTop = 18;
  const padBottom = 36;
  const innerWidth = width - padLeft - padRight;
  const innerHeight = height - padTop - padBottom;
  const renderAsBars = granularity === 'day' || granularity === 'week';
  const rawMax = Math.max(1, ...items.map((item) => item.value));
  const tickStep = buildNiceTickStep(rawMax);
  const maxValue = Math.max(tickStep * 4, Math.ceil(rawMax / tickStep) * tickStep);
  const yTicks = Array.from({ length: Math.floor(maxValue / tickStep) + 1 }, (_, index) => {
    const value = index * tickStep;
    const y = padTop + innerHeight - (value / maxValue) * innerHeight;
    return { value, y };
  });
  const coords = items.map((item, index) => {
    const x = items.length === 1 ? padLeft + innerWidth / 2 : padLeft + (index * innerWidth) / (items.length - 1);
    const y = padTop + innerHeight - (item.value / maxValue) * innerHeight;
    return { x, y, value: item.value, label: item.label, item, index };
  });
  const polyline = coords.map((point) => `${point.x},${point.y}`).join(' ');
  const labelStride = items.length <= 8 ? 1 : Math.max(1, Math.ceil(items.length / (renderAsBars ? 8 : 6)));
  const activePoint = hoveredIndex === null ? null : coords[hoveredIndex];
  const barSlotWidth = items.length === 0 ? innerWidth : innerWidth / Math.max(items.length, 1);
  const barWidth = Math.min(56, Math.max(18, barSlotWidth * 0.58));
  const partialBoundary = hasPartialBoundary(items, granularity, rangeStart, rangeEnd);
  const baselineY = padTop + innerHeight;
  const minBarHeight = 4;

  return (
    <div className="analytics-line-wrap" onMouseLeave={() => setHoveredIndex(null)}>
      <svg className="analytics-line-svg" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
        {yTicks.map((tick) => (
          <g key={tick.value}>
            <line x1={padLeft} y1={tick.y} x2={width - padRight} y2={tick.y} className="analytics-grid-line" />
            <text x={padLeft - 8} y={tick.y + 4} className="analytics-axis-text">
              {formatNumber(tick.value)}
            </text>
          </g>
        ))}
        {activePoint && !renderAsBars ? (
          <line
            x1={activePoint.x}
            y1={padTop}
            x2={activePoint.x}
            y2={height - padBottom}
            className="analytics-guide-line"
          />
        ) : null}
        {renderAsBars ? (
          coords.map((point) => (
            <g key={`${point.label}-${point.x}`}>
              {(() => {
                const rawHeight = Math.max(0, baselineY - point.y);
                const visualHeight = point.value > 0 ? Math.max(minBarHeight, rawHeight) : minBarHeight;
                const visualY = baselineY - visualHeight;
                return (
                  <rect
                    x={point.x - barWidth / 2}
                    y={visualY}
                    width={barWidth}
                    height={visualHeight}
                    rx="10"
                    className={
                      hoveredIndex === point.index
                        ? point.value === 0
                          ? 'analytics-bar analytics-bar-zero active'
                          : 'analytics-bar active'
                        : point.value === 0
                          ? 'analytics-bar analytics-bar-zero'
                          : 'analytics-bar'
                    }
                  />
                );
              })()}
              <rect
                x={point.x - Math.max(barWidth, 24) / 2}
                y={padTop}
                width={Math.max(barWidth, 24)}
                height={innerHeight}
                rx="12"
                className="analytics-hit-area"
                onMouseEnter={() => setHoveredIndex(point.index)}
              />
            </g>
          ))
        ) : (
          <>
            <polyline points={polyline} className="analytics-line-path" />
            {coords.map((point) => (
              <g key={`${point.label}-${point.x}`}>
                <circle cx={point.x} cy={point.y} r="4.5" className={hoveredIndex === point.index ? 'analytics-line-dot active' : 'analytics-line-dot'} />
                <circle
                  cx={point.x}
                  cy={point.y}
                  r="13"
                  className="analytics-hit-area"
                  onMouseEnter={() => setHoveredIndex(point.index)}
                />
              </g>
            ))}
          </>
        )}
      </svg>
      {activePoint ? (
        <div className="analytics-chart-tooltip">
          <em>{activePoint.label}</em>
          <strong>{formatNumber(activePoint.value)}</strong>
          <span>{formatBucketRangeText(activePoint.item, granularity, rangeStart, rangeEnd)}</span>
        </div>
      ) : null}
      <div className="analytics-line-labels" style={{ gridTemplateColumns: `repeat(${items.length}, minmax(0, 1fr))` }}>
        {coords.map((point, index) => {
          const shouldShow = index === 0 || index === coords.length - 1 || index % labelStride === 0;
          return (
            <span
              key={`${point.label}-${index}`}
              className={
                hoveredIndex === index
                  ? 'analytics-axis-label active'
                  : shouldShow
                    ? 'analytics-axis-label'
                    : 'analytics-axis-label muted'
              }
            >
              {shouldShow ? point.label : ''}
            </span>
          );
        })}
      </div>
      {partialBoundary ? <p className="analytics-chart-note">{partialBoundaryNote(granularity)}</p> : null}
    </div>
  );
}

function DonutChart({
  title,
  items,
  total,
}: {
  title: string;
  items: AnalyticsDistributionItem[];
  total: number;
}) {
  const chartItems = buildDonutItems(items, total);
  if (chartItems.length === 0 || total <= 0) {
    return (
      <section className="card analytics-chart-card">
        <div className="analytics-card-head">
          <h3>{title}</h3>
        </div>
        <div className="analytics-empty-chart">当前筛选范围内暂无分布数据</div>
      </section>
    );
  }

  let offset = 0;
  const gradientParts = chartItems.map((item, index) => {
    const color = DONUT_COLORS[index % DONUT_COLORS.length];
    const start = offset;
    offset += item.ratio * 360;
    return `${color} ${start}deg ${offset}deg`;
  });

  return (
    <section className="card analytics-chart-card">
      <div className="analytics-card-head">
        <h3>{title}</h3>
      </div>
      <div className="analytics-donut-layout">
        <div className="analytics-donut-shell">
          <div className="analytics-donut-ring" style={{ background: `conic-gradient(${gradientParts.join(', ')})` }}>
            <div className="analytics-donut-hole">
              <strong>{formatNumber(total)}</strong>
              <span>总量</span>
            </div>
          </div>
        </div>
        <div className="analytics-legend">
          {chartItems.map((item, index) => (
            <div key={item.key} className="analytics-legend-item">
              <span className="analytics-legend-color" style={{ backgroundColor: DONUT_COLORS[index % DONUT_COLORS.length] }} />
              <div>
                <strong>{item.label}</strong>
                <p>
                  {formatNumber(item.value)} · {(item.ratio * 100).toFixed(1)}%
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function TopCameraBars({ items, total }: { items: AnalyticsDashboardResponse['top_cameras']; total: number }) {
  return (
    <section className="card analytics-ranking-card">
      <div className="analytics-card-head">
        <h3>摄像头新增排行</h3>
        <span className="muted">按当前筛选时间段统计</span>
      </div>
      {items.length === 0 || total <= 0 ? (
        <div className="analytics-empty-chart">当前筛选范围内暂无排行数据</div>
      ) : (
        <div className="analytics-bars">
          {items.map((item, index) => (
            <article key={`${item.camera_id}-${index}`} className="analytics-bar-row">
              <div className="analytics-bar-copy">
                <strong>{item.label}</strong>
                <span>{formatNumber(item.value)}</span>
              </div>
              <div className="analytics-bar-track">
                <div
                  className="analytics-bar-fill"
                  style={{ width: `${Math.max(8, (item.value / Math.max(1, items[0].value)) * 100)}%` }}
                />
              </div>
              <p className="muted">{(item.ratio * 100).toFixed(1)}%</p>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

export default function AnalyticsPage({ token }: Props) {
  const defaultRange = useMemo(() => buildPresetRange(7), []);
  const [rangeStart, setRangeStart] = useState(defaultRange.start);
  const [rangeEnd, setRangeEnd] = useState(defaultRange.end);
  const [granularity, setGranularity] = useState('auto');
  const [cameraId, setCameraId] = useState('');
  const [cameraOptions, setCameraOptions] = useState<CameraSourceConfigItem[]>([]);
  const [data, setData] = useState<AnalyticsDashboardResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (override?: { start?: string; end?: string; granularity?: string; cameraId?: string }) => {
      const nextStart = override?.start ?? rangeStart;
      const nextEnd = override?.end ?? rangeEnd;
      const nextGranularity = override?.granularity ?? granularity;
      const nextCameraId = override?.cameraId ?? cameraId;
      setError(null);
      setLoading(true);
      try {
        const resp = await fetchAnalyticsDashboard(token, {
          rangeStart: toIsoInput(nextStart),
          rangeEnd: toIsoInput(nextEnd),
          granularity: nextGranularity,
          cameraId: nextCameraId || undefined,
        });
        setData(resp);
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setLoading(false);
      }
    },
    [cameraId, granularity, rangeEnd, rangeStart, token],
  );

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let active = true;
    const loadCameras = async () => {
      try {
        const resp = await fetchCameraConfigs(token);
        if (!active) return;
        setCameraOptions(resp.items);
      } catch {
        if (!active) return;
        setCameraOptions([]);
      }
    };
    void loadCameras();
    return () => {
      active = false;
    };
  }, [token]);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void load();
  }

  function applyPreset(days: number) {
    const next = buildPresetRange(days);
    setRangeStart(next.start);
    setRangeEnd(next.end);
    void load({ start: next.start, end: next.end });
  }

  const rangeLabel = useMemo(() => {
    if (!data) return '-';
    return `${toDisplayDate(data.range_start)} ~ ${toDisplayDate(data.range_end)}`;
  }, [data]);

  return (
    <main className="analytics-page">
      <section className="card analytics-filter-card">
        <div className="overview-head">
          <div>
            <h2>数据看板</h2>
            <p className="muted">看累计总数、时段新增、趋势和分布。</p>
          </div>
          <button type="button" onClick={() => void load()} disabled={loading}>
            {loading ? '刷新中...' : '刷新'}
          </button>
        </div>
        <div className="analytics-preset-row">
          <button type="button" className="ghost-btn" onClick={() => applyPreset(1)}>
            24小时
          </button>
          <button type="button" className="ghost-btn" onClick={() => applyPreset(7)}>
            7天
          </button>
          <button type="button" className="ghost-btn" onClick={() => applyPreset(30)}>
            30天
          </button>
        </div>
        <form className="analytics-filter-grid" onSubmit={handleSubmit}>
          <label>
            开始时间
            <input type="datetime-local" value={rangeStart} onChange={(e) => setRangeStart(e.target.value)} />
          </label>
          <label>
            结束时间
            <input type="datetime-local" value={rangeEnd} onChange={(e) => setRangeEnd(e.target.value)} />
          </label>
          <label>
            粒度
            <select value={granularity} onChange={(e) => setGranularity(e.target.value)}>
              <option value="auto">自动</option>
              <option value="hour">小时</option>
              <option value="day">天</option>
              <option value="week">周</option>
            </select>
          </label>
          <label>
            摄像头
            <select value={cameraId} onChange={(e) => setCameraId(e.target.value)}>
              <option value="">全部摄像头</option>
              {cameraOptions.map((camera) => (
                <option key={camera.id} value={camera.id}>
                  {camera.name} ({camera.id})
                </option>
              ))}
            </select>
          </label>
          <button type="submit" disabled={loading}>
            {loading ? '加载中...' : '应用筛选'}
          </button>
        </form>
        {data?.note ? <p className="analytics-note">{data.note}</p> : null}
        {error ? <p className="error">{error}</p> : null}
      </section>

      <section className="analytics-kpi-grid">
        <article className="capture-stat analytics-kpi-card">
          <p className="muted">累计总数</p>
          <p>{formatNumber(data?.total_count)}</p>
          <span className="analytics-kpi-foot">当前口径下的累计抓拍记录</span>
        </article>
        <article className="capture-stat analytics-kpi-card">
          <p className="muted">今日新增</p>
          <p>{formatNumber(data?.today_count)}</p>
          <span className="analytics-kpi-foot">按今日 00:00 到当前统计</span>
        </article>
        <article className="capture-stat analytics-kpi-card">
          <p className="muted">时段新增</p>
          <p>{formatNumber(data?.range_count)}</p>
          <span className="analytics-kpi-foot">较上一相同时段 {formatRatio(data?.range_change_ratio)}</span>
        </article>
        <article className="capture-stat analytics-kpi-card">
          <p className="muted">活跃摄像头</p>
          <p>{formatNumber(data?.active_camera_count)}</p>
          <span className="analytics-kpi-foot">{rangeLabel}</span>
        </article>
      </section>

      <section className="analytics-main-grid">
        <section className="card analytics-trend-card">
          <div className="analytics-card-head">
            <div>
              <h3>新增趋势</h3>
              <p className="muted">
                {rangeLabel} · 粒度 {data?.granularity ?? granularity}
              </p>
            </div>
            <span className={data?.source === 'local' ? 'analytics-source-tag warn' : 'analytics-source-tag'}>
              {data?.source === 'local' ? '本地回退' : '数据库'}
            </span>
          </div>
          <TrendChart
            items={data?.trend ?? []}
            granularity={data?.granularity ?? granularity}
            rangeStart={data?.range_start ?? ''}
            rangeEnd={data?.range_end ?? ''}
          />
        </section>

        <div className="analytics-side-grid">
          <DonutChart title="摄像头占比" items={data?.camera_distribution ?? []} total={data?.range_count ?? 0} />
          <DonutChart title="图像模式占比" items={data?.mode_distribution ?? []} total={data?.range_count ?? 0} />
        </div>
      </section>

      <TopCameraBars items={data?.top_cameras ?? []} total={data?.range_count ?? 0} />
    </main>
  );
}
