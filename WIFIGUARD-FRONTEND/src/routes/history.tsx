import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { toast } from "sonner";
import { Header, ResponseBadge } from "./index";
import { useUpdateFallResponse } from "@/api/mutations";
import { useFalls } from "@/api/queries";
import { FALL_RESPONSES, FALL_RESPONSE_LABEL, type FallResponse } from "@/lib/domain";
import { fmtDateTime } from "@/lib/format";
import { usePresentation } from "@/components/usePresentation";
import { FallHistoryDetails } from "@/components/FallHistoryDetails";
import { MobileFallCard } from "@/components/MobileFallCard";
import { alarmStore } from "@/lib/alarm-store";

export const Route = createFileRoute("/history")({
  head: () => ({ meta: [{ title: "낙상 이력 · WIFI-GUARD" }] }),
  component: HistoryPage,
});

function HistoryPage() {
  const [filter, setFilter] = useState<"ALL" | FallResponse>("ALL");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const {
    data: page,
    isLoading,
    isError,
  } = useFalls({ response: filter === "ALL" ? null : filter, limit: 200 });
  const respond = useUpdateFallResponse();
  const presentation = usePresentation();
  const falls = page?.items ?? [];
  const exportCsv = () => {
    const rows = [
      ["timestamp", "resident", "room", "confidence", "duration_s", "response"],
      ...falls.map((f) => [
        f.occurredAt,
        f.residentName,
        f.room,
        String(f.confidence),
        String(f.durationS),
        f.response,
      ]),
    ];
    const csv = rows
      .map((r) => r.map((v) => '"' + v.replaceAll('"', '""') + '"').join(","))
      .join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "fall-history-" + Date.now() + ".csv";
    a.click();
    URL.revokeObjectURL(url);
  };
  const changeResponse = async (id: string, response: FallResponse) => {
    try {
      await respond.mutateAsync({ id, response });
      toast.success("처리 상태를 변경했습니다");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "응답 상태를 바꾸지 못했습니다");
    }
  };

  return (
    <div>
      <Header title="낙상 이력" />
      <div className="p-6 space-y-4 max-w-6xl">
        <div className="user-page-heading">
          <div>
            <h1>알림 및 대응 기록</h1>
            <p>
              {page
                ? "저장된 알림 " + page.total + "건"
                : isError
                  ? "이력을 불러오지 못했습니다"
                  : "이력 확인 중"}
            </p>
          </div>
          <div className="flex gap-2">
            <select
              aria-label="처리 상태 필터"
              value={filter}
              onChange={(e) => setFilter(e.target.value as "ALL" | FallResponse)}
              className="bg-surface border border-border rounded px-3 py-2 text-sm"
            >
              <option value="ALL">전체</option>
              {FALL_RESPONSES.map((r) => (
                <option key={r} value={r}>
                  {FALL_RESPONSE_LABEL[r]}
                </option>
              ))}
            </select>
            <button onClick={exportCsv} className="secondary-button">
              내보내기
            </button>
          </div>
        </div>
        <div className="mobile-record-list mobile-only" aria-label="낙상 알림 목록">
          {falls.map((fall) => (
            <MobileFallCard
              key={fall.id}
              fall={fall}
              residentName={presentation.label(fall.residentName, "resident")}
              roomName={presentation.label(fall.room, "room")}
              open={expandedId === fall.id}
              busy={respond.isPending}
              onToggle={() => setExpandedId(expandedId === fall.id ? null : fall.id)}
              onOpen={() => {
                if (fall.response === "PENDING") alarmStore.raise(fall);
              }}
              onRespond={(value) => void changeResponse(fall.id, value)}
            />
          ))}
          {!falls.length && (
            <div className="mobile-empty-card">
              <h2>
                {isLoading
                  ? "알림을 확인하고 있어요"
                  : isError
                    ? "알림을 불러오지 못했어요"
                    : "저장된 알림이 없습니다"}
              </h2>
              <p>
                {isError
                  ? "연결 상태를 확인하고 다시 시도해 주세요."
                  : "낙상 알림과 처리 결과가 이곳에 저장됩니다."}
              </p>
            </div>
          )}
        </div>
        <div className="desktop-only bg-surface border border-border rounded-lg overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-xs text-muted border-b border-border">
                <th className="p-3 w-6" />
                <th className="p-3">발생 시각</th>
                <th className="p-3">대상자</th>
                <th className="p-3">공간</th>
                <th className="p-3">처리 상태</th>
                <th className="p-3">상태 변경</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {falls.map((fall) => {
                const open = expandedId === fall.id;
                return (
                  <Rows
                    key={fall.id}
                    fall={fall}
                    residentName={presentation.label(fall.residentName, "resident")}
                    roomName={presentation.label(fall.room, "room")}
                    open={open}
                    onToggle={() => setExpandedId(open ? null : fall.id)}
                    onRespond={(value) => void changeResponse(fall.id, value)}
                  />
                );
              })}
              {!falls.length && (
                <tr>
                  <td colSpan={6} className="p-8 text-center text-muted">
                    {isLoading
                      ? "이력을 불러오는 중입니다"
                      : isError
                        ? "이력 조회에 실패했습니다"
                        : "저장된 낙상 알림이 없습니다"}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function Rows({
  fall,
  residentName,
  roomName,
  open,
  onToggle,
  onRespond,
}: {
  fall: import("@/lib/domain").FallEvent;
  residentName: string;
  roomName: string;
  open: boolean;
  onToggle: () => void;
  onRespond: (value: FallResponse) => void;
}) {
  return (
    <>
      <tr className="hover:bg-black/5">
        <td className="p-3">
          <button onClick={onToggle} aria-label="알림 상세 보기" aria-expanded={open}>
            {open ? "▾" : "▸"}
          </button>
        </td>
        <td className="p-3">
          <button onClick={onToggle} className="text-left">
            {fmtDateTime(fall.occurredAt)}
          </button>
        </td>
        <td className="p-3">{residentName}</td>
        <td className="p-3">{roomName}</td>
        <td className="p-3">
          <ResponseBadge response={fall.response} />
        </td>
        <td className="p-3">
          <select
            aria-label="알림 처리 상태"
            value={fall.response}
            onChange={(e) => onRespond(e.target.value as FallResponse)}
            className="bg-background border border-border rounded px-2 py-1 text-sm"
          >
            {FALL_RESPONSES.map((r) => (
              <option key={r} value={r}>
                {FALL_RESPONSE_LABEL[r]}
              </option>
            ))}
          </select>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={6}>
            <FallHistoryDetails fall={fall} />
          </td>
        </tr>
      )}
    </>
  );
}
