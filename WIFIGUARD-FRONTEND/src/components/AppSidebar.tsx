import { useState } from "react";
import { Link, useRouterState, useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Bell,
  Building2,
  ChevronRight,
  ClipboardList,
  Cpu,
  History,
  LayoutDashboard,
  LogOut,
  Menu,
  Radio,
  Settings2,
  UserRound,
  Users,
  X,
  type LucideIcon,
} from "lucide-react";
import { useAuth } from "@/api/auth";
import { useLocalEdgeStatus } from "@/api/local-edge";
import { useDetectionStatus } from "@/api/queries";
import { usePresentation } from "./usePresentation";
import { BrandLogo } from "./BrandLogo";

export function AppSidebar() {
  const { user, facility, logout } = useAuth();
  const detection = useDetectionStatus();
  const local = useLocalEdgeStatus();
  const presentation = usePresentation();
  const pathname = useRouterState({ select: (r) => r.location.pathname });
  const navigate = useNavigate();
  const cache = useQueryClient();
  const [open, setOpen] = useState(false);
  const isFacility = user?.service === "FACILITY";
  const localEnabled = import.meta.env.VITE_PI5_LOCAL === "1" || local.data?.enabled === true;
  const navMain = [
    { to: "/", label: "모니터링", icon: LayoutDashboard },
    { to: "/history", label: "낙상 이력", icon: History },
    { to: "/event-log", label: "활동 기록", icon: ClipboardList },
  ];
  const navManagement = [
    { to: "/devices", label: "장치 관리", icon: Radio },
    { to: "/residents", label: isFacility ? "입소자 관리" : "대상자 관리", icon: Users },
    ...(isFacility && user?.role === "ROOT"
      ? [{ to: "/facility-members", label: "시설 멤버", icon: Building2 }]
      : []),
    { to: "/notifications", label: "알림 설정", icon: Bell },
    { to: "/config", label: "감지 설정", icon: Settings2 },
    ...(localEnabled ? [{ to: "/system", label: "시스템 관리", icon: Cpu }] : []),
    ...(!localEnabled ? [{ to: "/train", label: "모델 학습", icon: Cpu }] : []),
  ];
  async function handleLogout() {
    await logout();
    cache.clear();
    setOpen(false);
    await navigate({ to: "/login" });
  }
  return (
    <>
      <button
        className="mobile-menu-toggle"
        aria-label={open ? "메뉴 닫기" : "메뉴 열기"}
        aria-expanded={open}
        aria-controls="main-navigation"
        onClick={() => setOpen(!open)}
      >
        {open ? <X size={21} /> : <Menu size={21} />}
      </button>
      {open && (
        <button
          className="mobile-menu-backdrop"
          aria-label="메뉴 닫기"
          onClick={() => setOpen(false)}
        />
      )}
      <aside id="main-navigation" className={`app-sidebar ${open ? "mobile-open" : ""}`}>
        <Link to="/" className="sidebar-brand" onClick={() => setOpen(false)}>
          <BrandLogo />
          <div>
            <strong>WIFI-GUARD</strong>
            <small>비접촉 돌봄 서비스</small>
          </div>
        </Link>
        <div className="sidebar-workspace">
          <span className="workspace-symbol">
            {isFacility ? <Building2 size={18} /> : <Users size={18} />}
          </span>
          <div>
            <small>{isFacility ? "시설 서비스" : "가정 서비스"}</small>
            <strong>{isFacility ? (facility?.name ?? "내 시설") : "내 공간"}</strong>
          </div>
        </div>
        <nav aria-label="주 메뉴">
          <p className="nav-group-label">관제</p>
          {navMain.map((n) => (
            <NavItem key={n.to} {...n} active={pathname === n.to} close={() => setOpen(false)} />
          ))}
          <p className="nav-group-label">관리 및 설정</p>
          {navManagement.map((n) => (
            <NavItem key={n.to} {...n} active={pathname === n.to} close={() => setOpen(false)} />
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-service">
            <Activity size={16} />
            <span>
              {detection === "ACTIVE"
                ? "모니터링 중"
                : detection === "OFFLINE"
                  ? "실시간 연결을 확인해 주세요"
                  : "감지 대기"}
            </span>
            <i className={detection === "ACTIVE" ? "is-live" : ""} />
          </div>
          <Link to="/account" className="sidebar-user" onClick={() => setOpen(false)}>
            <span className="user-avatar">
              <UserRound size={19} />
            </span>
            <div>
              <strong>{presentation.label(user?.name ?? "사용자", "account")}</strong>
              <small>{user?.role === "ROOT" ? "관리자" : "사용자 계정"}</small>
            </div>
            <ChevronRight size={14} />
          </Link>
          <button className="sidebar-logout" onClick={() => void handleLogout()}>
            <LogOut size={15} /> 로그아웃
          </button>
        </div>
      </aside>
    </>
  );
}

function NavItem({
  to,
  label,
  icon: Icon,
  active,
  close,
}: {
  to: string;
  label: string;
  icon: LucideIcon;
  active: boolean;
  close: () => void;
}) {
  return (
    <Link
      to={to}
      onClick={close}
      aria-current={active ? "page" : undefined}
      className={`sidebar-nav-item ${active ? "active" : ""}`}
    >
      <Icon size={18} />
      <span>{label}</span>
      {active && <i />}
    </Link>
  );
}
