import { useCallback, useEffect, useState } from 'react';

import { fetchAlerts } from '../api/client';
import type { AlertItem } from '../api/types';

interface Props {
  token: string;
}

function toDateText(value: string): string {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString();
}

function levelClass(level: string): string {
  const key = level.trim().toLowerCase();
  if (key === 'critical' || key === 'error') return 'alert-level critical';
  if (key === 'warning' || key === 'warn') return 'alert-level warning';
  return 'alert-level info';
}

export default function AlertsPage({ token }: Props) {
  const [items, setItems] = useState<AlertItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const resp = await fetchAlerts(token);
      setItems(resp.items);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    let active = true;
    const boot = async () => {
      if (active) await load();
    };
    void boot();
    const timer = setInterval(() => {
      void load();
    }, 15000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [load]);

  return (
    <main className="alerts-page">
      <section className="card">
        <div className="alerts-head">
          <h2>告警中心</h2>
          <button type="button" onClick={() => void load()} disabled={loading}>
            {loading ? '刷新中...' : '刷新'}
          </button>
        </div>
        {error ? <p className="error">{error}</p> : null}
        {items.length === 0 ? (
          <p className="muted">暂无告警</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>级别</th>
                <th>来源</th>
                <th>消息</th>
                <th>时间</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <span className={levelClass(item.level)}>{item.level}</span>
                  </td>
                  <td>{item.source}</td>
                  <td>{item.message}</td>
                  <td>{toDateText(item.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </main>
  );
}
