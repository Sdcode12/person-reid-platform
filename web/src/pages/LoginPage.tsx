import { type FormEvent, useEffect, useState } from 'react';

import type { SetupDbTestResponse, SetupInitializeResponse, SetupStatusResponse } from '../api/types';

interface Props {
  onSubmit: (username: string, password: string) => Promise<void>;
}

interface SetupPageProps {
  initialStatus: SetupStatusResponse | null;
  onTestConnection: (payload: {
    host: string;
    port: number;
    dbname: string;
    user: string;
    password: string;
  }) => Promise<SetupDbTestResponse>;
  onInitialize: (payload: {
    host: string;
    port: number;
    dbname: string;
    user: string;
    password: string;
    admin_username: string;
    admin_password: string;
  }) => Promise<SetupInitializeResponse>;
  onRefreshStatus: () => Promise<void>;
}

function SetupBadge({ ok, label }: { ok: boolean; label: string }) {
  return <span className={ok ? 'setup-pill success' : 'setup-pill'}>{label}</span>;
}

export function SetupPage({ initialStatus, onTestConnection, onInitialize, onRefreshStatus }: SetupPageProps) {
  const [host, setHost] = useState('');
  const [port, setPort] = useState('5432');
  const [dbname, setDbname] = useState('');
  const [user, setUser] = useState('');
  const [password, setPassword] = useState('');
  const [adminUsername, setAdminUsername] = useState('admin');
  const [adminPassword, setAdminPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<SetupDbTestResponse | null>(null);

  useEffect(() => {
    const db = initialStatus?.database;
    if (!db) return;
    if (db.host && !host.trim()) setHost(db.host);
    if (db.port && (!port.trim() || port === '5432')) setPort(String(db.port));
    if (db.dbname && !dbname.trim()) setDbname(db.dbname);
    if (db.user && !user.trim()) setUser(db.user);
  }, [initialStatus, host, port, dbname, user]);

  async function handleTestConnection() {
    setTesting(true);
    setError(null);
    setSuccess(null);
    try {
      const result = await onTestConnection({
        host: host.trim(),
        port: Number(port) || 5432,
        dbname: dbname.trim(),
        user: user.trim(),
        password,
      });
      setTestResult(result);
      if (!result.ok) {
        setError(result.detail);
      }
    } catch (err) {
      setTestResult(null);
      setError((err as Error).message);
    } finally {
      setTesting(false);
    }
  }

  async function handleInitialize(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setSuccess(null);
    if ((adminPassword || '').length < 8) {
      setLoading(false);
      setError('管理员密码至少 8 位。');
      return;
    }
    if (adminPassword !== confirmPassword) {
      setLoading(false);
      setError('两次输入的管理员密码不一致。');
      return;
    }
    try {
      const result = await onInitialize({
        host: host.trim(),
        port: Number(port) || 5432,
        dbname: dbname.trim(),
        user: user.trim(),
        password,
        admin_username: adminUsername.trim(),
        admin_password: adminPassword,
      });
      setSuccess(`初始化完成，管理员账号 ${result.admin_username} 已创建。`);
      await onRefreshStatus();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="setup-page">
      <section className="setup-card card">
        <div className="setup-hero">
          <div>
            <p className="login-kicker">FIRST INSTALL SETUP</p>
            <h1>系统初始化</h1>
            <p className="muted">首次安装时先配置 PostgreSQL，再初始化表结构并创建首个管理员账号。</p>
          </div>
          <div className="setup-status-row">
            <SetupBadge ok={Boolean(initialStatus?.config_exists)} label="配置文件" />
            <SetupBadge ok={Boolean(initialStatus?.db_reachable)} label="数据库连通" />
            <SetupBadge ok={Boolean(initialStatus?.schema_ready)} label="结构已初始化" />
            <SetupBadge ok={Boolean(initialStatus?.admin_exists)} label="管理员已存在" />
          </div>
          {initialStatus?.detail ? <p className="setup-helper">{initialStatus.detail}</p> : null}
        </div>

        <form className="setup-form" onSubmit={handleInitialize}>
          <section className="setup-section">
            <div className="setup-section-head">
              <h2>数据库连接</h2>
              <p className="muted">填写 PostgreSQL 连接信息。密码不会在页面回显。</p>
            </div>
            <div className="setup-field-grid">
              <label>
                主机
                <input placeholder="127.0.0.1" value={host} onChange={(e) => setHost(e.target.value)} />
              </label>
              <label>
                端口
                <input inputMode="numeric" placeholder="5432" value={port} onChange={(e) => setPort(e.target.value)} />
              </label>
              <label>
                数据库名
                <input placeholder="postgres" value={dbname} onChange={(e) => setDbname(e.target.value)} />
              </label>
              <label>
                用户名
                <input placeholder="postgres" value={user} onChange={(e) => setUser(e.target.value)} />
              </label>
            </div>
            <label>
              密码
              <input type="password" placeholder="数据库密码" value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
            {initialStatus?.database?.has_password ? (
              <p className="setup-helper">当前配置文件中已检测到数据库密码；如果正在重试初始化，请重新输入密码。</p>
            ) : null}
            <div className="setup-inline-actions">
              <button type="button" className="ghost-btn" onClick={handleTestConnection} disabled={testing || loading}>
                {testing ? '测试中...' : '测试连接'}
              </button>
              {testResult ? (
                <div className={testResult.ok ? 'setup-result success' : 'setup-result'}>
                  <strong>{testResult.ok ? '连接成功' : '连接失败'}</strong>
                  <span>{testResult.detail}</span>
                  {testResult.ok && testResult.db_version ? (
                    <span>
                      PostgreSQL {testResult.db_version}
                      {testResult.pgvector_installed ? ' · pgvector 已安装' : ' · 未检测到 pgvector'}
                    </span>
                  ) : null}
                </div>
              ) : null}
            </div>
          </section>

          <section className="setup-section">
            <div className="setup-section-head">
              <h2>首个管理员</h2>
              <p className="muted">初始化完成后使用这个账号登录系统。</p>
            </div>
            <div className="setup-field-grid">
              <label>
                管理员用户名
                <input placeholder="admin" value={adminUsername} onChange={(e) => setAdminUsername(e.target.value)} />
              </label>
              <label>
                管理员密码
                <input type="password" placeholder="至少 8 位" value={adminPassword} onChange={(e) => setAdminPassword(e.target.value)} />
              </label>
              <label>
                确认密码
                <input type="password" placeholder="再次输入管理员密码" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} />
              </label>
            </div>
          </section>

          {error ? <p className="error">{error}</p> : null}
          {success ? <p className="setup-success">{success}</p> : null}

          <div className="setup-inline-actions">
            <button disabled={loading}>{loading ? '初始化中...' : '初始化数据库并创建管理员'}</button>
          </div>
        </form>
      </section>
    </main>
  );
}

export default function LoginPage({ onSubmit }: Props) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleLogin(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await onSubmit(username, password);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-card card">
        <div className="login-brand">
          <p className="login-kicker">PERSON REID PLATFORM</p>
          <h1>欢迎登录</h1>
          <p className="muted">使用数据库账号登录摄像头接入、抓拍控制、检索与告警统一入口。</p>
        </div>
        <form className="login-form" onSubmit={handleLogin}>
          <label>
            用户名
            <input placeholder="输入数据库账号" value={username} onChange={(e) => setUsername(e.target.value)} />
          </label>
          <label>
            密码
            <input type="password" placeholder="输入密码" value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          {error ? <p className="error">{error}</p> : null}
          <button disabled={loading}>{loading ? '登录中...' : '登录'}</button>
        </form>
      </section>
    </main>
  );
}
