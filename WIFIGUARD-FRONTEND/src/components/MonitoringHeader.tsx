import { useEffect, useState, type ReactNode } from "react";
import { Bell, Clock3 } from "lucide-react";
import { useAuth } from "@/api/auth";
import { useDetectionStatus, useResidents } from "@/api/queries";
import { useAlarm } from "@/lib/alarm-store";
import { FALL_RESPONSE_LABEL, type FallResponse, type StateMachine } from "@/lib/domain";
import { stateLabel } from "@/lib/format";
import { DemoModeBadge } from "./DemoModeBadge";
import { BrandLogo } from "./BrandLogo";

const SEVERITY: StateMachine[] = ["FALL", "SUSPECT", "COOLDOWN", "IDLE"];

export function Header({ title }: { title: string }) {
  const [now, setNow] = useState("");
  const { user } = useAuth();
  const detection = useDetectionStatus();
  const alarm = useAlarm();
  const { data: residents = [] } = useResidents();
  const displayState = alarm ? "FALL" : SEVERITY.find((s) => residents.some((r) => r.state === s));
  useEffect(() => {
    const update = () =>
      setNow(
        new Date().toLocaleString("ko-KR", {
          month: "long",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        }),
      );
    update();
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  }, []);
  return (
    <header className="user-page-header">
      <div className="mobile-header-brand" aria-label="WIFI-GUARD">
        <BrandLogo />
        <strong>WIFI-GUARD</strong>
      </div>
      <div>
        <span className="header-path">
          WIFI-GUARD <span>/</span> {user?.service === "FACILITY" ? "시설 서비스" : "가정 서비스"}
        </span>
        <h2>{title}</h2>
      </div>
      <div className="header-meta">
        <DemoModeBadge />
        <span
          className={`status-pill ${detection === "OFFLINE" ? "tone-warning" : detection === "ACTIVE" ? "tone-success" : "tone-neutral"}`}
        >
          <i />
          {detection === "OFFLINE"
            ? "실시간 연결 끊김"
            : detection === "ACTIVE"
              ? "모니터링 중"
              : "감지 준비 중"}
        </span>
        {(displayState === "FALL" || displayState === "SUSPECT") && (
          <span
            className={`header-fall ${displayState === "FALL" ? "text-primary" : "text-muted"}`}
          >
            <Bell size={15} />
            {stateLabel(displayState)}
          </span>
        )}
        <span className="header-clock" title="화면의 현재 시각이며 센서 갱신 시각이 아닙니다">
          <Clock3 size={14} />
          {now || "—"}
        </span>
      </div>
    </header>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return <h3 className="text-xs font-semibold tracking-wide text-muted mb-3">{children}</h3>;
}

export function ResponseBadge({ response }: { response: FallResponse | string }) {
  const tones: Record<FallResponse, string> = {
    PENDING: "tone-warning",
    ACKNOWLEDGED: "tone-info",
    DISPATCHED: "tone-success",
    FALSE_ALARM: "tone-neutral",
  };
  const key = response in tones ? (response as FallResponse) : "PENDING";
  return <span className={`response-badge ${tones[key]}`}>{FALL_RESPONSE_LABEL[key]}</span>;
}
