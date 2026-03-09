import { useState, type ReactNode } from 'react';

import type { StatusResponse } from '../api/types';
import StatusBar from '../components/StatusBar';

export type AppRouteKey =
  | 'analytics'
  | 'search'
  | 'gallery'
  | 'roi'
  | 'capture'
  | 'captureSettings'
  | 'cameras'
  | 'settings'
  | 'userAdmin'
  | 'alerts';

interface RouteTab {
  key: AppRouteKey;
  label: string;
  hint: string;
  adminOnly?: boolean;
}

const ROUTE_TABS: RouteTab[] = [
  { key: 'analytics', label: '数据看板', hint: '总数与趋势' },
  { key: 'search', label: '检索', hint: '按图查人' },
  { key: 'gallery', label: '图片库', hint: '按条件查图' },
  { key: 'roi', label: 'ROI', hint: '区域过滤' },
  { key: 'capture', label: '采集控制', hint: '启动与状态' },
  { key: 'captureSettings', label: '抓拍参数', hint: '保存与调整' },
  { key: 'cameras', label: '摄像头', hint: '源配置与测试' },
  { key: 'settings', label: '个人中心', hint: '账号与安全' },
  { key: 'userAdmin', label: '用户管理', hint: '用户与角色', adminOnly: true },
  { key: 'alerts', label: '告警', hint: '异常与状态' },
];

interface Props {
  role: string;
  status: StatusResponse | null;
  route: AppRouteKey;
  onLogout: () => void;
  onNavigate: (route: AppRouteKey) => void;
  children: ReactNode;
}

export default function AppLayout({ role, status, route, onLogout, onNavigate, children }: Props) {
  const active = ROUTE_TABS.find((item) => item.key === route);
  const visibleTabs = ROUTE_TABS.filter((item) => !item.adminOnly || role === 'admin');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  return (
    <div className={sidebarCollapsed ? 'app-shell sidebar-collapsed' : 'app-shell'}>
      <aside className={sidebarCollapsed ? 'app-sidebar collapsed' : 'app-sidebar'}>
        <div className="sidebar-brand">
          <div className="brand-mark">R</div>
          <div className="sidebar-brand-text">
            <p className="brand-title">ReID Console</p>
            <p className="brand-sub">NewAPI Style Panel</p>
          </div>
          <button
            type="button"
            className="sidebar-toggle"
            onClick={() => setSidebarCollapsed((v) => !v)}
            title={sidebarCollapsed ? '展开菜单' : '收起菜单'}
          >
            {sidebarCollapsed ? '»' : '«'}
          </button>
        </div>
        <nav className="side-nav">
          {visibleTabs.map((tab) => (
            <button
              key={tab.key}
              type="button"
              className={route === tab.key ? 'side-tab active' : 'side-tab'}
              onClick={() => onNavigate(tab.key)}
            >
              <span className="side-tab-label">{tab.label}</span>
              <span className="side-tab-hint">{tab.hint}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span className="role-badge">{role}</span>
          <button type="button" className="ghost-btn" onClick={onLogout}>
            退出登录
          </button>
        </div>
      </aside>

      <section className="app-main">
        <StatusBar status={status} role={role} title={active?.label ?? '控制台'} />
        <div className="app-content">{children}</div>
      </section>
    </div>
  );
}
