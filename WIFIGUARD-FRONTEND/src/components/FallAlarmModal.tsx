import { toast } from "sonner";
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiFetch, USE_MOCK } from "@/api/client";
import { useUpdateFallResponse } from "@/api/mutations";
import { alarmStore, useAlarm } from "@/lib/alarm-store";
import type { FallEvent, FallResponse } from "@/lib/domain";
import { fmtDateTime } from "@/lib/format";
import { usePresentation } from "./usePresentation";

/**
 * 전체화면 낙상 알람. 자동 타임아웃 없음 — 사람이 반드시 응답해야 닫힌다 (안전 요구).
 * 응답은 서버에 기록(감사 이력)되고 알람이 해제된다.
 */
export function FallAlarmModal() {
  const alarm = useAlarm();
  const respond = useUpdateFallResponse();
  const presentation = usePresentation();
  const saved = useQuery({ queryKey: ["alarm-persisted", alarm?.id],
    queryFn: () => apiFetch<FallEvent>(`/falls/${alarm!.id}`),
    enabled: Boolean(alarm && !alarm.id.startsWith("live-") && !USE_MOCK), refetchInterval: 2000 });
  useEffect(() => {
    if (alarm && saved.data?.id === alarm.id && saved.data.response !== "PENDING") alarmStore.clear();
  }, [alarm, saved.data]);

  if (!alarm) return null;
  const pc = import.meta.env.VITE_PC_SIMULATION === "1";
  const unpersisted = alarm.id.startsWith("live-");

  const answer = async (response: FallResponse) => {
    if (unpersisted) {
      toast.error(
        "낙상 이벤트가 DB에 저장되지 않아 응답을 기록할 수 없습니다. 이벤트 이력과 적재 오류를 확인하세요.",
      );
      return;
    }
    try {
      await respond.mutateAsync({ id: alarm.id, response });
      alarmStore.clear();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "응답을 저장하지 못했습니다");
    }
  };

  return (
    <div
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="fall-alarm-title"
      className="fall-alarm-modal fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-6"
    >
      <div className="bg-surface border-2 border-primary rounded-lg max-w-lg w-full overflow-hidden shadow-2xl">
        <div className="animate-alert border-b border-primary/40 p-5">
          <div className="flex items-center gap-3">
            <div className="size-3 rounded-full bg-primary animate-pulse" />
            <span className="text-xs font-mono font-bold uppercase tracking-widest text-primary">
              {pc ? "데모 알림 · 실제 응급 상황 아님" : "낙상 감지 · 확인 필요"}
            </span>
          </div>
        </div>
        <div className="p-6 space-y-5">
          <div>
            <h2
              id="fall-alarm-title"
              className="text-3xl font-bold text-foreground tracking-tight mb-1"
            >
              낙상 감지 알람
            </h2>
            <p className="text-sm text-foreground/70">
              {presentation.label(alarm.room, "room")} ·{" "}
              {presentation.label(alarm.residentName, "resident")} · 즉시 확인이 필요합니다.
            </p>
            {pc && (
              <p className="text-xs text-warning mt-2">
                데모 모드에서는 외부 알림이 발송되지 않습니다.
              </p>
            )}
            {unpersisted && (
              <p role="alert" className="text-xs text-warning mt-2">
                알림 저장을 확인하지 못해 처리 상태를 변경할 수 없습니다.
              </p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Stat label="발생 시각" value={fmtDateTime(alarm.occurredAt)} />
            <Stat label="신뢰도" value={`${(alarm.confidence * 100).toFixed(1)}%`} highlight />
            <Stat label="지속 시간" value={`${alarm.durationS.toFixed(2)}s`} />
            <Stat label="공간" value={presentation.label(alarm.room, "room")} />
          </div>

          <button
            className="mobile-only mobile-alarm-confirm"
            disabled={respond.isPending || unpersisted}
            onClick={() => answer("ACKNOWLEDGED")}
          >
            확인했어요
          </button>
          <div className="grid grid-cols-2 gap-3 pt-2">
            <button
              onClick={() => answer("DISPATCHED")}
              disabled={respond.isPending || unpersisted}
              className="py-3 bg-primary text-primary-foreground font-bold rounded text-sm uppercase tracking-wider hover:brightness-110 disabled:opacity-60"
            >
              응급 출동 확인
            </button>
            <button
              onClick={() => answer("FALSE_ALARM")}
              disabled={respond.isPending || unpersisted}
              className="py-3 bg-surface border border-border text-muted font-medium rounded text-sm uppercase tracking-wider hover:text-foreground disabled:opacity-60"
            >
              오탐지 처리
            </button>
          </div>
          <p className="text-center text-[10px] font-mono text-muted uppercase tracking-widest">
            {pc ? "데모 모드 · 외부 전송 없음" : "알림 전송 상태는 알림 설정에서 확인하세요"}
          </p>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className="bg-background border border-border rounded p-3">
      <div className="text-[10px] font-mono text-muted uppercase mb-1">{label}</div>
      <div
        className={`text-sm font-mono ${highlight ? "text-primary font-bold" : "text-foreground"}`}
      >
        {value}
      </div>
    </div>
  );
}
