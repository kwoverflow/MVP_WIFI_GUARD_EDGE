import { useEventLogs } from "@/api/queries";
import { fmtTime } from "@/lib/format";

/** 대시보드 임베드 요약 패널 (F-030) — 최근 20건. 스코핑은 서버가 한다. */
export function EventLogPanel() {
  const { data, isLoading } = useEventLogs({ limit: 20 });
  const logs = data?.items ?? [];

  return (
    <div className="bg-surface border border-border rounded-lg overflow-hidden">
      <div className="p-4 border-b border-border">
        <h3 className="text-xs font-semibold tracking-wide">최근 운영 기록</h3>
      </div>
      <div className="divide-y divide-border max-h-64 overflow-y-auto">
        {!isLoading && logs.length === 0 && (
          <div className="p-4 text-xs text-muted font-mono">로그가 없습니다.</div>
        )}
        {logs.map((l) => (
          <div key={l.id} className="p-3 flex items-center gap-3 text-xs font-mono">
            <span className="text-muted">{fmtTime(l.ts)}</span>
            <span
              className={`w-14 text-[10px] font-bold uppercase ${
                l.level === "FALL"
                  ? "text-primary"
                  : l.level === "ERROR"
                    ? "text-primary"
                    : l.level === "WARN"
                      ? "text-warning"
                      : "text-muted"
              }`}
            >
              {l.level}
            </span>
            <span className="text-foreground/80 flex-1">{l.msg}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
