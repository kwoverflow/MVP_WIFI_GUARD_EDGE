import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useAuth } from "@/api/auth";
import { bindLocalEdge, localEdgeCommand, useLocalEdgeStatus } from "@/api/local-edge";

const PHASES: Record<string, string> = {
  waiting_empty_room: "빈방 확인 대기",
  leaving: "퇴실 대기 30초",
  waiting_csi: "센서 데이터 대기",
  measuring: "빈방 기준 측정 30초",
  computing: "기준값 계산 중",
  saving: "저장 중",
  done: "실제 빈방 보정 완료",
  error: "보정 실패 · 추론 차단",
  cancelled: "보정 취소 · 추론 차단",
};

export function numberText(value: number | null | undefined, unit = "") {
  return value == null || !Number.isFinite(value) ? "미측정" : `${value.toFixed(2)}${unit}`;
}

export function Pi5LocalPanel({
  deviceId,
  canBind = false,
  controls = true,
  compact = false,
}: {
  deviceId?: string;
  canBind?: boolean;
  controls?: boolean;
  compact?: boolean;
}) {
  const query = useLocalEdgeStatus();
  const cache = useQueryClient();
  const { user } = useAuth();
  const [busy, setBusy] = useState(false);
  const data = query.data;
  const calibrationKey =
    data?.enabled && data?.calibration
      ? `${data.calibration.run_id}/${data.calibration.phase}`
      : null;
  useEffect(() => {
    if (calibrationKey) {
      void cache.invalidateQueries({ queryKey: ["devices"] });
      void cache.invalidateQueries({ queryKey: ["config"] });
    }
  }, [calibrationKey, cache]); // phase changes only, not every resource poll
  if (!data?.enabled) return null;
  const administrator = user?.service === "HOME" || user?.role === "ROOT";
  const bound = !deviceId || deviceId === data.device_id;
  const permitted = administrator && data.agent_available && !busy;
  const stopped = ["inactive", "failed"].includes(data.agent?.edge_state ?? "");
  const calibration = data.calibration;
  const resource = data.resources;
  const resourceUsable = resource?.fresh === true;
  const configuration = data.runtime?.pi5_services?.runtime_configuration;

  async function action(value: "start" | "stop" | "recalibrate" | "confirm-empty-room") {
    if (!data?.device_id) return;
    if (
      value === "confirm-empty-room" &&
      !window.confirm(
        "이 방에 사람이 없음을 확인했습니다. 지금부터 퇴실 대기와 측정이 끝날 때까지 약 60초 이상 방 밖에 머무르세요.",
      )
    )
      return;
    if (
      value === "recalibrate" &&
      !window.confirm(
        "수신·추론을 중단하고 새 보정을 준비합니다. 이후 빈방 확인을 다시 해야 합니다.",
      )
    )
      return;
    setBusy(true);
    try {
      await localEdgeCommand(data.device_id, value, calibration?.run_id);
      toast("요청을 처리하고 있습니다");
      await query.refetch();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "명령 실패");
    } finally {
      setBusy(false);
    }
  }

  async function bind() {
    if (!deviceId) return;
    setBusy(true);
    try {
      await bindLocalEdge(deviceId);
      await query.refetch();
      toast.success("장치를 연결했습니다");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "연결 실패");
    } finally {
      setBusy(false);
    }
  }

  if (compact)
    return (
      <section className="device-operation-panel" aria-label="모니터링 제어">
        <div className="section-heading">
          <div>
            <h2>모니터링 제어</h2>
            <p>
              {!data.agent_available
                ? "장치 연결을 확인해 주세요."
                : data.runtime_current
                  ? stopped
                    ? "모니터링 중지됨"
                    : "연결됨"
                  : "연결 대기"}
            </p>
          </div>
        </div>
        {controls && (
          <div className="operation-buttons">
            {bound && data.device_id && (
              <>
                <button
                  disabled={!permitted}
                  onClick={() => void action("start")}
                  className="primary-button"
                >
                  모니터링 시작
                </button>
                <button
                  disabled={!permitted}
                  onClick={() => void action("stop")}
                  className="secondary-button"
                >
                  모니터링 중지
                </button>
                <button
                  disabled={!permitted}
                  onClick={() => void action("recalibrate")}
                  className="secondary-button"
                >
                  빈방 보정
                </button>
                {calibration?.phase === "waiting_empty_room" && (
                  <button
                    disabled={!permitted || !data.runtime_current}
                    onClick={() => void action("confirm-empty-room")}
                    className="primary-button"
                  >
                    빈방 확인 · 보정 시작
                  </button>
                )}
              </>
            )}
            {canBind && deviceId && (!data.device_id || !bound) && (
              <button
                disabled={!permitted || !stopped}
                onClick={() => void bind()}
                className="secondary-button"
              >
                이 장치 연결
              </button>
            )}
          </div>
        )}
        {calibration && (
          <p className="operation-status">
            빈방 보정:{" "}
            {!data.runtime_current
              ? "상태 확인 중"
              : calibration.phase === "done"
                ? "완료"
                : (PHASES[calibration.phase] ?? "진행 중")}
          </p>
        )}
        {calibration?.phase === "waiting_empty_room" && (
          <p className="text-sm text-muted">
            사람이 없는 상태를 확인한 뒤 보정을 시작하세요. 약 60초 동안 방 밖에 머물러 주세요.
          </p>
        )}
        {calibration?.error && (
          <p role="alert" className="text-warning text-sm">
            보정을 완료하지 못했습니다. 장치 연결을 확인하고 다시 시도해 주세요.
          </p>
        )}
        {data.desired_revision && (
          <p className="text-xs text-muted">
            설정:{" "}
            {data.runtime_current && configuration?.applied_revision === data.desired_revision
              ? "적용됨"
              : "적용 대기"}
          </p>
        )}
        {data.agent?.last_error && (
          <p role="alert" className="text-warning text-sm">
            장치 작동을 확인해 주세요. 자세한 내용은 고급 진단에서 확인할 수 있습니다.
          </p>
        )}
      </section>
    );

  return (
    <section
      className="bg-surface border border-border rounded-lg p-4 space-y-4"
      aria-label="Pi5 로컬 운영 및 전체 자원"
    >
      <h2 className="font-semibold">
        {data.simulation?.enabled
          ? "PC 실행 중 · 전체 서비스 실측 자원"
          : "Pi5 로컬 운영 · 전체 서비스 자원"}
      </h2>
      <p className="text-xs text-muted">
        수신 상태: {data.agent?.edge_state ?? "미확인"} · 실행 데이터:{" "}
        {data.runtime_current ? "최신" : "대기/오래됨"} · 모델 stride:{" "}
        {numberText(data.runtime?.stride_seconds, "초")}
      </p>
      {!data.agent_available && (
        <p className="text-warning text-sm">제어 서비스 미응답 · 설치/서비스 상태를 확인하세요.</p>
      )}
      {data.agent?.last_error && <p className="text-warning text-xs">{data.agent.last_error}</p>}
      {controls && (
        <div className="flex gap-2 flex-wrap">
          {bound && data.device_id && (
            <>
              <button
                disabled={!permitted}
                onClick={() => void action("start")}
                className="border rounded px-3 py-2 disabled:opacity-40"
              >
                수신 시작
              </button>
              <button
                disabled={!permitted}
                onClick={() => void action("stop")}
                className="border rounded px-3 py-2 disabled:opacity-40"
              >
                수신·추론 중지
              </button>
              <button
                disabled={!permitted}
                onClick={() => void action("recalibrate")}
                className="border rounded px-3 py-2 disabled:opacity-40"
              >
                새 빈방 보정
              </button>
              {calibration?.phase === "waiting_empty_room" && (
                <button
                  disabled={!permitted || !data.runtime_current}
                  onClick={() => void action("confirm-empty-room")}
                  className="bg-primary text-primary-foreground rounded px-3 py-2 disabled:opacity-40"
                >
                  빈방 확인 · 측정 시작
                </button>
              )}
            </>
          )}
          {canBind && deviceId && (!data.device_id || !bound) && (
            <button
              disabled={!permitted || !stopped}
              onClick={() => void bind()}
              className="border rounded px-3 py-2 disabled:opacity-40"
            >
              선택한 SPI 장치를 이 Pi5에 연결
            </button>
          )}
        </div>
      )}
      {calibration && (
        <p className="text-sm">
          보정{data.simulation?.enabled ? "(모의 RAW 기준값 계산)" : ""}:{" "}
          {data.simulation?.enabled && calibration.phase === "done"
            ? "모의 빈방 기준값 측정 완료"
            : (PHASES[calibration.phase] ?? calibration.phase)}{" "}
          · 단계 경과 {numberText(calibration.phase_elapsed_seconds, "초")}
          {calibration.error && <span className="block text-warning">{calibration.error}</span>}
          {calibration.phase === "done" && (
            <span className="block">
              MV {numberText(calibration.presence_mv_threshold)} · baseline{" "}
              {numberText(calibration.wander_baseline)}
            </span>
          )}
        </p>
      )}
      <p className="text-xs text-muted">
        SPI 펌웨어 AGC: 미지원 · 빈방 기준값 보정과는 별개입니다. 퇴실 판정 후 추가 10초만 추론을
        유지하며 센서 오류에는 유예하지 않습니다.
      </p>
      {data.runtime?.pi5_services?.inference_gate && (
        <p className="text-sm">
          추론 게이트: {data.runtime.pi5_services.inference_gate.reason} · 유예 잔여{" "}
          {numberText(data.runtime.pi5_services.inference_gate.grace_remaining_seconds, "초")}
        </p>
      )}
      {data.desired_revision && (
        <p className="text-xs">
          화면 설정:{" "}
          {configuration?.applied_revision === data.desired_revision
            ? "실행 코드 적용 확인"
            : "실행 코드 적용 대기"}
          {configuration?.last_error && ` · ${configuration.last_error}`}
        </p>
      )}
      {data.last_command && (
        <p role="status" className="text-xs">
          최근 명령: {data.last_command.action} · {data.last_command.state}
          {data.last_command.error && ` · ${data.last_command.error}`}
        </p>
      )}
      {!resourceUsable ? (
        <p className="text-warning text-sm">
          전체 자원 측정 대기/오래됨 · 0으로 표시하지 않습니다.
        </p>
      ) : (
        <>
          <p className="text-xs text-muted">
            {resource.scope}. CPU는 전체 코어 기준.{" "}
            {data.simulation?.enabled
              ? "PC 메모리는 프로세스 RSS 합계로 공유 페이지가 중복 계산될 수 있습니다. Pi5 메모리 측정값이 아닙니다."
              : "메모리는 파일 캐시를 포함한 cgroup 청구량입니다."}{" "}
            모델 GPU 미사용.
          </p>
          {!resource.complete && (
            <p className="text-warning text-xs">
              필수 서비스 일부 미동작/첫 측정 중 · 전체 합계는 미측정.{" "}
              {Object.keys(resource.errors).join(", ")}
            </p>
          )}
          <div className="flex flex-wrap gap-4 text-sm">
            <span>전체 CPU {numberText(resource.total.cpu_percent_host_basis, "%")}</span>
            <span>전체 메모리 {numberText(resource.total.memory_mib, " MiB")}</span>
            <span>스왑 {numberText(resource.total.swap_mib, " MiB")}</span>
            <span>온도 {numberText(resource.system.temperature_c, "℃")}</span>
            <span>시스템 RAM 여유 {numberText(resource.system.ram_available_mib, " MiB")}</span>
            <span>
              파일시스템 사용/여유 {numberText(resource.system.disk_used_gib, " GiB")} /{" "}
              {numberText(resource.system.disk_free_gib, " GiB")}
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="text-xs w-full">
              <thead>
                <tr>
                  <th className="text-left">처리</th>
                  <th>CPU</th>
                  <th>메모리</th>
                  <th>스왑</th>
                  <th>누적 I/O 읽기/쓰기</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(resource.groups).map(([name, group]) => (
                  <tr key={name}>
                    <td>{name}</td>
                    <td className="text-center">{numberText(group.cpu_percent_host_basis, "%")}</td>
                    <td className="text-center">{numberText(group.memory_mib, " MiB")}</td>
                    <td className="text-center">{numberText(group.swap_mib, " MiB")}</td>
                    <td className="text-center">
                      {numberText(group.io_read_bytes_total / 2 ** 20, " MiB")} /{" "}
                      {numberText(group.io_write_bytes_total / 2 ** 20, " MiB")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <p className="text-xs">
        처리 P95: 전처리 {numberText(data.runtime?.stage_ms?.prepare?.p95, "ms")} · 피처{" "}
        {numberText(data.runtime?.stage_ms?.features?.p95, "ms")} · 추론{" "}
        {numberText(data.runtime?.stage_ms?.inference?.p95, "ms")} · 전체{" "}
        {numberText(data.runtime?.processing_ms?.p95, "ms")}
      </p>
    </section>
  );
}
