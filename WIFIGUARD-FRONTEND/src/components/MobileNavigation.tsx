import { Link, useRouterState } from "@tanstack/react-router";
import { Bell, LayoutDashboard, Radio, Settings2, Users } from "lucide-react";
import { useFalls } from "@/api/queries";

/** Same authenticated routes and cached data as desktop; no second mobile backend. */
export function MobileNavigation() {
  const pathname = useRouterState({ select: (r) => r.location.pathname });
  const pending = useFalls({ response: "PENDING", limit: 1 });
  const count = pending.data?.total;
  const items = [
    { to: "/", label: "현황", icon: LayoutDashboard },
    { to: "/history", label: "알림", icon: Bell },
    { to: "/devices", label: "장치", icon: Radio },
    { to: "/residents", label: "대상자", icon: Users },
    { to: "/notifications", label: "설정", icon: Settings2 },
  ];
  return (
    <nav className="mobile-bottom-nav" aria-label="모바일 주요 메뉴">
      {items.map(({ to, label, icon: Icon }) => (
        <Link
          key={to}
          to={to}
          aria-current={pathname === to ? "page" : undefined}
          className={pathname === to ? "is-active" : ""}
        >
          <span className="mobile-nav-icon">
            <Icon size={21} />
            {to === "/history" && count != null && count > 0 && !pending.isError && (
              <small aria-label={`확인 대기 알림 ${count}건`}>{count > 99 ? "99+" : count}</small>
            )}
          </span>
          <span>{label}</span>
        </Link>
      ))}
    </nav>
  );
}
