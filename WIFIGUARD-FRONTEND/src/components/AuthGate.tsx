import { useEffect, useRef, type ReactNode } from "react";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import { useAuth } from "@/api/auth";
import { AppSidebar } from "@/components/AppSidebar";
import { FallAlarmModal } from "@/components/FallAlarmModal";
import { LiveBridge } from "@/components/LiveBridge";
import { MobileNavigation } from "@/components/MobileNavigation";

/** 인증 없이 접근 가능한 경로의 단일 진실원. */
export const PUBLIC_PATHS = ["/login", "/signup"];

export function AuthGate({ children }: { children: ReactNode }) {
  // status === "loading" 이 기존 `hydrated` 게이트 역할을 한다 (SSR 에서는 항상 loading → hydration 불일치 없음).
  const { status } = useAuth();
  const pathname = useRouterState({ select: (r) => r.location.pathname });
  const routePending = useRouterState({ select: (r) => r.isLoading });
  const navigate = useNavigate();
  const isPublic = PUBLIC_PATHS.includes(pathname);
  const mainRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!routePending && window.matchMedia("(max-width: 760px)").matches) {
      mainRef.current?.scrollTo({ top: 0 });
    }
  }, [pathname, routePending]);

  useEffect(() => {
    if (status === "loading") return;
    if (status === "anon" && !isPublic) {
      navigate({ to: "/login" });
    } else if (status === "authed" && isPublic) {
      navigate({ to: "/" });
    }
  }, [status, isPublic, navigate]);

  if (status === "loading") {
    return (
      <div className="min-h-screen flex items-center justify-center text-muted font-mono text-xs">
        Loading…
      </div>
    );
  }

  if (isPublic || status !== "authed") {
    return <>{children}</>;
  }

  return (
    <div className="app-shell flex h-screen bg-background text-foreground overflow-hidden">
      <AppSidebar />
      <main ref={mainRef} className="app-main flex-1 overflow-y-auto min-w-0">
        {children}
      </main>
      <MobileNavigation />
      <FallAlarmModal />
      {/* 실시간 소켓을 앱 전역에서 하나 유지하고 낙상 전이를 알람으로 올린다.
          HOME/FACILITY 구분 없이 항상 렌더된다 — 다기기 관제가 필요한 쪽이 FACILITY 다. */}
      <LiveBridge />
    </div>
  );
}
