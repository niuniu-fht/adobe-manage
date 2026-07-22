import {
  Activity,
  BellRing,
  Boxes,
  ClipboardList,
  FileClock,
  LogOut,
  Menu,
  ServerCog,
  Settings,
  X
} from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { apiFetch, emitToast, setCsrfToken } from "../lib/api";

const navItems = [
  { to: "/", label: "总览", icon: Activity },
  { to: "/instances", label: "实例", icon: ServerCog },
  { to: "/tokens", label: "Token", icon: Boxes },
  { to: "/logs", label: "请求日志", icon: FileClock },
  { to: "/alerts", label: "告警", icon: BellRing },
  { to: "/audit", label: "操作审计", icon: ClipboardList },
  { to: "/settings", label: "系统设置", icon: Settings }
];

const titleMap: Record<string, string> = {
  "/": "运行总览",
  "/instances": "实例管理",
  "/tokens": "Token 管理",
  "/logs": "请求日志",
  "/alerts": "告警中心",
  "/audit": "操作审计",
  "/settings": "系统设置"
};

export function AppShell({ onLogout }: { onLogout: () => void }) {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const title = location.pathname.startsWith("/instances/")
    ? "实例详情"
    : titleMap[location.pathname] || "运维中心";

  async function logout() {
    try {
      await apiFetch("/auth/logout", { method: "POST" });
    } catch {
      // The local session is cleared below even if the request already expired.
    }
    setCsrfToken("");
    emitToast("已退出", "info");
    onLogout();
  }

  return (
    <div className="app-frame">
      <aside className={`sidebar${open ? " sidebar-open" : ""}`}>
        <div className="sidebar-brand">
          <span className="brand-glyph">A2</span>
          <div><strong>Adobe2API</strong><small>FLEET CONTROL</small></div>
          <button className="icon-btn sidebar-close" onClick={() => setOpen(false)} title="关闭导航">
            <X size={18} />
          </button>
        </div>
        <nav>
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              onClick={() => setOpen(false)}
              className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}
            >
              <item.icon size={18} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <button className="nav-link logout-link" type="button" onClick={logout}>
          <LogOut size={18} /><span>退出</span>
        </button>
      </aside>
      {open && <button className="sidebar-scrim" onClick={() => setOpen(false)} aria-label="关闭导航" />}
      <main className="workspace">
        <header className="topbar">
          <button className="icon-btn mobile-menu" onClick={() => setOpen(true)} title="打开导航">
            <Menu size={20} />
          </button>
          <div><p className="eyebrow">OPERATIONS</p><h1>{title}</h1></div>
          <div className="topbar-live"><i /> 30 秒采集</div>
        </header>
        <div className="page-content"><Outlet /></div>
      </main>
    </div>
  );
}
