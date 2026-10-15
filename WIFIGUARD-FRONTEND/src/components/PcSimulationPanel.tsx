import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { Cpu, Database, FlaskConical, Radio, BellRing } from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { localEdgeCommand, setSimulationScenario, testSimulationAlert, useLocalEdgeStatus } from "@/api/local-edge";
import { AlertDialog, AlertDialogContent, AlertDialogTitle, AlertDialogDescription, AlertDialogFooter, AlertDialogAction, AlertDialogCancel } from "@/components/ui/alert-dialog";

const SCENARIOS = [
  { id: "empty", label: "빈방", detail: "낮은 변화의 RAW 입력" },
  { id: "motion", label: "움직임", detail: "움직임을 포함한 RAW 입력" },
  { id: "still", label: "미세 움직임", detail: "호흡 대역의 RAW 입력" },
  { id: "disconnect", label: "수신 끊김", detail: "입력 중단 · 즉시 추론 차단" },
] as const;

export function PcSimulationPanel() {
  const query = useLocalEdgeStatus();
  const cache = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [confirmation, setConfirmation] = useState<"calibration" | "alert" | null>(null);
  const data = query.data;
  if (!data?.simulation?.enabled) return null;
  const simulation = data.simulation;
  const live = Date.now() / 1000 - simulation.updated_at_unix < 5;
  const done = data.calibration?.phase === "done";
  async function perform(task: () => Promise<unknown>, message: string) {
    setBusy(true);
    try {
      await task(); toast.success(message); await query.refetch();
      void cache.invalidateQueries({ queryKey: ["falls"] });
      void cache.invalidateQueries({ queryKey: ["event-logs"] });
    } catch (error) { toast.error(error instanceof Error ? error.message : "모의 시험 요청 실패"); }
    finally { setBusy(false); }
  }
  function command(action: "start" | "stop" | "confirm-empty-room") {
    if (!data?.device_id) return;
    void perform(() => localEdgeCommand(data.device_id!, action, data.calibration?.run_id), "실행 명령 접수");
  }
  const disabled = busy || !live || !data.agent_available;
  return <section className="bg-surface border border-primary/30 rounded-lg p-5 space-y-4" aria-label="PC 통합 모의검증">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h2 className="font-semibold flex items-center gap-2"><FlaskConical className="size-4 text-primary" />PC 통합 모의검증 · Pi5 로컬 구조</h2>
        <p className="text-xs text-muted mt-1">실제 백엔드·DB·전처리·피처·FP32 모델 사용. 센서 입력만 모의 데이터이며 Pi5 성능 측정이 아닙니다.</p></div>
      <span className="text-xs border rounded px-2 py-1 text-primary">외부 알림 발송 안 함</span>
    </div>
    <div className="grid grid-cols-1 md:grid-cols-4 gap-2 text-xs">
      {[{ icon: Radio, title: "① RAW 센서 입력", value: "WGSP CRC 해석 · 320Hz" },
        { icon: Cpu, title: "② 재실 → 모델", value: "재실 0.5초 · 모델 0.5초" },
        { icon: Database, title: "③ 실제 DB 적재", value: `${simulation.ingest?.presence_sink?.written ?? 0}개 재실 행 저장` },
        { icon: BellRing, title: "④ 로컬 알림 수신", value: `메일 ${simulation.notification.email_captured} · 푸시 ${simulation.notification.push_http_captured}` }]
        .map(item => <div key={item.title} className="border border-border rounded p-3"><item.icon className="size-4 text-muted mb-2" /><p className="font-medium">{item.title}</p><p className="text-muted mt-1">{item.value}</p></div>)}
    </div>
    <div className="flex flex-wrap gap-2">
      <button disabled={disabled} onClick={() => command("start")} className="border rounded px-3 py-2 text-xs disabled:opacity-40">모의 수신 시작</button>
      <button disabled={disabled} onClick={() => command("stop")} className="border rounded px-3 py-2 text-xs disabled:opacity-40">수신·추론 중지</button>
      {data.calibration?.phase === "waiting_empty_room" && <button disabled={disabled || !data.runtime_current || simulation.scenario !== "empty"} onClick={() => setConfirmation("calibration")} className="bg-primary text-primary-foreground rounded px-3 py-2 text-xs disabled:opacity-40">빈방 보정 시작 · 약 60초</button>}
      <Link to="/devices" className="text-xs underline p-2 text-muted">장치 연결·새 보정</Link>
      <Link to="/config" className="text-xs underline p-2 text-muted">설정 적용 확인</Link>
    </div>
    <div className="grid grid-cols-2 md:grid-cols-4 gap-2">{SCENARIOS.map(item => <button key={item.id}
      disabled={disabled || (!done && item.id !== "empty" && item.id !== "disconnect")}
      onClick={() => void perform(() => setSimulationScenario(item.id), "RAW 입력 변경 · 실제 판정 결과를 기다려주세요")}
      className={`text-left border rounded p-3 disabled:opacity-40 ${simulation.scenario === item.id ? "border-primary bg-primary/5" : "border-border"}`}>
      <p className="text-sm font-medium">{item.label}</p><p className="text-[10px] text-muted mt-1">{item.detail}</p>
    </button>)}</div>
    <div className="flex flex-wrap justify-between items-center gap-3 border-t border-border pt-3">
      <p className="text-xs text-muted">먼저 보정을 완료하세요. 빈방 전환에는 원래 타임아웃 + 추가 10초 유예가 적용됩니다.<br />PC 시계열 DB는 일반 PostgreSQL입니다. Timescale 압축·보존 정책은 별도 검증 대상입니다.</p>
      <button disabled={disabled} className="border border-primary/40 rounded px-3 py-2 text-xs disabled:opacity-40" onClick={() => setConfirmation("alert")}>알림 경로 시험 · 이벤트 주입</button>
    </div>
    {!live && <p className="text-warning text-xs">PC 실행기 응답이 오래되었습니다. 상태를 정상으로 간주하지 않습니다.</p>}
    <AlertDialog open={confirmation !== null} onOpenChange={open => { if (!open) setConfirmation(null); }}>
      <AlertDialogContent>
        <AlertDialogTitle>{confirmation === "calibration" ? "모의 빈방 보정 시작" : "알림 경로 시험 확인"}</AlertDialogTitle>
        <AlertDialogDescription>{confirmation === "calibration"
          ? "PC 입력이 빈방 시나리오인지 확인하세요. 기존 보정 코드로 30초 대기 + 30초 기준값 측정을 진행합니다. 이 과정에서는 낙상 추론이 중지됩니다."
          : "시험용 확정 낙상 이벤트를 DB와 관제 화면, 로컬 메일·푸시 수신기에 전달합니다. 모델의 낙상 판정이 아니며 실제 외부 알림을 보내지 않습니다."}</AlertDialogDescription>
        <AlertDialogFooter><AlertDialogCancel>취소</AlertDialogCancel><AlertDialogAction disabled={disabled} onClick={() => {
          if (confirmation === "calibration") command("confirm-empty-room");
          else void perform(testSimulationAlert, "시험 이벤트 적재 · 관제 알람과 로컬 수신 결과를 확인하세요");
        }}>시험 실행</AlertDialogAction></AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </section>;
}
