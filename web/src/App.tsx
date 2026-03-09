import { useEffect, useMemo, useState } from 'react';
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';

import { AUTH_EXPIRED_EVENT, fetchAuthMe, fetchSetupStatus, fetchStatus, initializeSetup, login, search, testSetupDatabase } from './api/client';
import type { SearchResponse, SetupStatusResponse, StatusResponse } from './api/types';
import AnalyticsPage from './pages/AnalyticsPage';
import AlertsPage from './pages/AlertsPage';
import CameraConfigPage from './pages/CameraConfigPage';
import CapturePage from './pages/CapturePage';
import AppLayout, { type AppRouteKey } from './layout/AppLayout';
import LoginPage, { SetupPage } from './pages/LoginPage';
import CaptureSettingsPage, { SettingsPage, UserAdminPage } from './pages/OverviewPage';
import PhotoLibraryPage from './pages/PhotoLibraryPage';
import RoiPage from './pages/RoiPage';
import SearchPage from './pages/SearchPage';

const TOKEN_KEY = 'reid_token';
const ROLE_KEY = 'reid_role';

const ROUTE_TO_PATH: Record<AppRouteKey, string> = {
  analytics: '/analytics',
  search: '/search',
  gallery: '/gallery',
  roi: '/roi',
  capture: '/capture',
  captureSettings: '/capture-settings',
  cameras: '/cameras',
  settings: '/settings',
  userAdmin: '/user-admin',
  alerts: '/alerts',
};

function routeFromPath(pathname: string): AppRouteKey {
  const path = pathname.trim().toLowerCase();
  if (path === '/analytics') return 'analytics';
  if (path === '/gallery') return 'gallery';
  if (path === '/roi') return 'roi';
  if (path === '/capture') return 'capture';
  if (path === '/capture-settings') return 'captureSettings';
  if (path === '/cameras') return 'cameras';
  if (path === '/settings') return 'settings';
  if (path === '/user-admin') return 'userAdmin';
  if (path === '/alerts') return 'alerts';
  return 'search';
}

export default function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY));
  const [role, setRole] = useState<string>(() => localStorage.getItem(ROLE_KEY) ?? 'operator');
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [setupStatus, setSetupStatus] = useState<SetupStatusResponse | null>(null);
  const [setupLoading, setSetupLoading] = useState(true);
  const [setupError, setSetupError] = useState<string | null>(null);
  const route = useMemo(() => routeFromPath(location.pathname), [location.pathname]);

  const isAuthed = useMemo(() => Boolean(token), [token]);
  const setupRequired = Boolean(setupStatus?.setup_required);

  useEffect(() => {
    let active = true;
    const loadSetupStatus = async () => {
      setSetupLoading(true);
      setSetupError(null);
      try {
        const next = await fetchSetupStatus();
        if (!active) return;
        setSetupStatus(next);
        if (next.setup_required) {
          localStorage.removeItem(TOKEN_KEY);
          localStorage.removeItem(ROLE_KEY);
          setToken(null);
          setStatus(null);
        }
      } catch (err) {
        if (!active) return;
        setSetupError((err as Error).message);
        setSetupStatus(null);
      } finally {
        if (active) setSetupLoading(false);
      }
    };
    void loadSetupStatus();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (setupLoading || setupRequired) return;
    if (location.pathname === '/') {
      navigate(ROUTE_TO_PATH.search, { replace: true });
    }
  }, [location.pathname, navigate, setupLoading, setupRequired]);

  useEffect(() => {
    if (!token || setupLoading || setupRequired) return;

    let active = true;
    const syncMe = async () => {
      try {
        const me = await fetchAuthMe(token);
        if (active) {
          localStorage.setItem(ROLE_KEY, me.role);
          setRole(me.role);
        }
      } catch {}
    };
    const pullStatus = async () => {
      try {
        const s = await fetchStatus(token);
        if (active) setStatus(s);
      } catch {
        if (active) setStatus(null);
      }
    };

    void syncMe();
    pullStatus();
    const timer = setInterval(pullStatus, 30000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [token, setupLoading, setupRequired]);

  useEffect(() => {
    const onAuthExpired = () => {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(ROLE_KEY);
      setToken(null);
      setStatus(null);
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, onAuthExpired);
    return () => {
      window.removeEventListener(AUTH_EXPIRED_EVENT, onAuthExpired);
    };
  }, []);

  async function handleLogin(username: string, password: string) {
    const resp = await login(username, password);
    localStorage.setItem(TOKEN_KEY, resp.access_token);
    localStorage.setItem(ROLE_KEY, resp.role);
    setToken(resp.access_token);
    setRole(resp.role);
  }

  async function handleSearch(fd: FormData): Promise<SearchResponse> {
    if (!token) throw new Error('not authenticated');
    return await search(token, fd);
  }

  function handleLogout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(ROLE_KEY);
    setToken(null);
    setStatus(null);
  }

  function handleNavigate(next: AppRouteKey) {
    navigate(ROUTE_TO_PATH[next]);
  }

  async function handleRefreshSetupStatus() {
    setSetupLoading(true);
    setSetupError(null);
    try {
      const next = await fetchSetupStatus();
      setSetupStatus(next);
      if (next.setup_required) {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(ROLE_KEY);
        setToken(null);
        setStatus(null);
      } else {
        navigate('/', { replace: true });
      }
    } catch (err) {
      setSetupError((err as Error).message);
    } finally {
      setSetupLoading(false);
    }
  }

  if (setupLoading) {
    return (
      <main className="login-page">
        <section className="login-card card">
          <div className="login-brand">
            <p className="login-kicker">SYSTEM CHECK</p>
            <h1>检查初始化状态</h1>
            <p className="muted">正在确认系统是否已完成数据库初始化。</p>
          </div>
        </section>
      </main>
    );
  }

  if (setupRequired) {
    return (
      <Routes>
        <Route
          path="/setup"
          element={
            <SetupPage
              initialStatus={setupStatus}
              onTestConnection={testSetupDatabase}
              onInitialize={initializeSetup}
              onRefreshStatus={handleRefreshSetupStatus}
            />
          }
        />
        <Route path="*" element={<Navigate to="/setup" replace />} />
      </Routes>
    );
  }

  if (setupError && !setupStatus) {
    return (
      <main className="login-page">
        <section className="login-card card">
          <div className="login-brand">
            <p className="login-kicker">SYSTEM CHECK</p>
            <h1>初始化状态读取失败</h1>
            <p className="muted">{setupError}</p>
          </div>
          <button onClick={() => void handleRefreshSetupStatus()}>重试</button>
        </section>
      </main>
    );
  }

  if (!isAuthed) return <LoginPage onSubmit={handleLogin} />;

  return (
    <AppLayout role={role} status={status} route={route} onLogout={handleLogout} onNavigate={handleNavigate}>
      <Routes>
        <Route path="/search" element={<SearchPage onSearch={handleSearch} token={token ?? ''} role={role} />} />
        <Route path="/analytics" element={<AnalyticsPage token={token ?? ''} />} />
        <Route path="/gallery" element={<PhotoLibraryPage token={token ?? ''} role={role} />} />
        <Route path="/roi" element={<RoiPage token={token ?? ''} role={role} />} />
        <Route path="/capture" element={<CapturePage token={token ?? ''} role={role} />} />
        <Route path="/capture-settings" element={<CaptureSettingsPage token={token ?? ''} role={role} />} />
        <Route path="/cameras" element={<CameraConfigPage token={token ?? ''} role={role} />} />
        <Route path="/settings" element={<SettingsPage token={token ?? ''} role={role} />} />
        <Route path="/user-admin" element={<UserAdminPage token={token ?? ''} role={role} />} />
        <Route path="/alerts" element={<AlertsPage token={token ?? ''} />} />
        <Route path="*" element={<Navigate to="/search" replace />} />
      </Routes>
    </AppLayout>
  );
}
