import type { StatusResponse } from '../api/types';

interface Props {
  status: StatusResponse | null;
  role: string;
  title: string;
}

function badgeClass(value: string | undefined): string {
  const v = (value ?? '').trim().toLowerCase();
  if (v === 'ok' || v === 'ready' || v === 'running' || v === 'up') return 'status-badge success';
  if (v === 'down' || v === 'error' || v === 'failed') return 'status-badge danger';
  return 'status-badge';
}

export default function StatusBar({ status, role, title }: Props) {
  return (
    <header className="status-bar">
      <div className="status-title-wrap">
        <h1>{title}</h1>
        <p className="muted">人像检索与摄像头管理控制台</p>
      </div>
      <div className="status-metrics">
        <span className="status-pill">{status?.service ?? 'person-reid-platform'}</span>
        <span className={badgeClass(status?.db)}>DB: {status?.db ?? '...'}</span>
        <span className={badgeClass(status?.ingestion)}>流: {status?.ingestion ?? '...'}</span>
        <span className="status-pill role">角色: {role}</span>
      </div>
    </header>
  );
}
