import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { toast } from "sonner";
import { Header, SectionTitle } from "./index";
import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useCreateDevice, useDeleteDevice, useUpdateDevice } from "@/api/mutations";
import { useDevices } from "@/api/queries";
import { LinkStatusPanel } from "@/components/LinkStatusPanel";
import { Pi5LocalPanel } from "@/components/Pi5LocalPanel";
import { localEdgeCommand } from "@/api/local-edge";
import { USE_MOCK } from "@/api/client";
import { usePresentation } from "@/components/usePresentation";
import { isCalibrationDemoRunning, runCalibrationDemo, secondsLeft } from "@/lib/calibration-sim";
import {
  CALIBRATION_STAGE_LABEL,
  HOME_SPACES,
  type CalibrationStage,
  type Connection,
  type Device,
} from "@/lib/domain";
import { fmtDateTime } from "@/lib/format";
import { Radio } from "lucide-react";

export const Route = createFileRoute("/devices")({
  head: () => ({ meta: [{ title: "장치 설정 · CSI-Guard" }] }),
  component: DevicesPage,
});

type CalibProgress = { stage: CalibrationStage; progress: number };

function errMsg(err: unknown, fallback: string) {
  return err instanceof ApiError || err instanceof Error ? err.message : fallback;
}

/**
 * 장치 관리. 캘리브레이션은 실시간 경로(엣지 ack)가 붙기 전까지 61초 데모 타이머로 진행을 보여주고
 * 완료 값만 PATCH /devices/{id} 로 영속한다. 진행 중 새로고침하면 타이머는 사라지고 서버 상태만 남는다.
 */
function DevicesPage() {
  const { user } = useAuth();
  const presentation = usePresentation();
  const isFacility = user?.service === "FACILITY";
  const locationLabel = isFacility ? "호실" : "공간";
  const { data: devices = [], isLoading } = useDevices();
  const create = useCreateDevice();
  const update = useUpdateDevice();
  const remove = useDeleteDevice();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [resetConfirm, setResetConfirm] = useState<Device | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [calib, setCalib] = useState<Record<string, CalibProgress>>({});
  const selected = devices.find((d) => d.id === selectedId) ?? devices[0];

  const startCalibration = async (device: Device) => {
    if (!USE_MOCK) {
      try {
        await localEdgeCommand(device.id, "recalibrate");
        toast("새 빈방 보정 접수 · Pi5 운영 패널에서 빈방 확인을 진행하세요");
      } catch (err) {
        toast.error(errMsg(err, "실제 보정 제어가 연결되지 않았습니다"));
      }
      return;
    }
    try {
      await update.mutateAsync({
        id: device.id,
        patch: { calibrating: true, calibrationStage: "LEAVING", calibrationProgress: 0 },
      });
    } catch (err) {
      toast.error(errMsg(err, "재설정을 시작하지 못했습니다"));
      return;
    }
    runCalibrationDemo(device.id, {
      onProgress: (stage, progress) =>
        setCalib((c) => ({ ...c, [device.id]: { stage, progress } })),
      onDone: async (result) => {
        setCalib((c) => {
          const next = { ...c };
          delete next[device.id];
          return next;
        });
        try {
          await update.mutateAsync({
            id: device.id,
            patch: {
              calibrating: false,
              calibrationStage: "DONE",
              calibrationProgress: 1,
              presenceMvThreshold: result.presenceMvThreshold,
              wanderBaseline: result.wanderBaseline,
            },
          });
          toast.success(
            `${device.name} 캘리브레이션 완료 · 움직임 임계값=${result.presenceMvThreshold.toFixed(2)} 재실 baseline=${result.wanderBaseline.toFixed(2)}`,
          );
        } catch (err) {
          toast.error(errMsg(err, "캘리브레이션 결과를 저장하지 못했습니다"));
        }
      },
    });
  };

  const cancelStaleCalibration = async (device: Device) => {
    try {
      await update.mutateAsync({
        id: device.id,
        patch: { calibrating: false, calibrationStage: "IDLE", calibrationProgress: 0 },
      });
    } catch (err) {
      toast.error(errMsg(err, "상태를 초기화하지 못했습니다"));
    }
  };

  const removeDevice = async (device: Device) => {
    if (!confirm(`${device.name} 삭제?`)) return;
    try {
      await remove.mutateAsync(device.id);
      setSelectedId(null);
      toast("장치 삭제됨");
    } catch (err) {
      if (err instanceof ApiError && err.code === "DEVICE_IS_PRIMARY") {
        toast.error(
          "거주자의 주 장치라 삭제할 수 없습니다. 재실 대상 관리에서 주 장치를 먼저 바꾸세요.",
        );
      } else {
        toast.error(errMsg(err, "삭제 실패"));
      }
    }
  };

  return (
    <div>
      <Header title="장치 관리" />
      <div className="p-6 space-y-4 max-w-7xl">
        <div className="management-heading flex justify-between items-center">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight mb-1">등록된 장치</h1>
            <p className="text-sm text-muted">
              {isFacility
                ? "등록된 모든 장치의 연결 상태를 확인하고, 필요할 때 재설정할 수 있습니다."
                : "거실 · 침실 · 화장실 등 설치한 공간별 장치 상태를 확인하고 재설정할 수 있습니다."}
            </p>
          </div>
          <button
            onClick={() => setAddOpen(true)}
            className="px-4 py-2 bg-primary text-primary-foreground rounded text-xs font-mono uppercase font-bold"
          >
            + 장치 추가
          </button>
        </div>

        <Pi5LocalPanel compact deviceId={selected?.id} canBind={selected?.connection === "SPI"} />
        <details className="technical-disclosure">
          <summary>연결 상세</summary>
          <div className="technical-content">
            <LinkStatusPanel />
          </div>
        </details>

        <div className="mobile-record-list mobile-only" aria-label="등록된 센서 목록">
          {devices.map((d) => (
            <button
              key={d.id}
              onClick={() => setSelectedId(d.id)}
              aria-pressed={d.id === selected?.id}
              className={`mobile-record-card mobile-device-card ${d.id === selected?.id ? "is-selected" : ""}`}
            >
              <div className="mobile-record-heading">
                <span className="mobile-record-symbol">
                  <Radio size={21} />
                </span>
                <div>
                  <small>{presentation.label(d.room, "room")}</small>
                  <h2>{presentation.label(d.name, "device")}</h2>
                </div>
                <span
                  className={`status-pill ${d.calibrating || calib[d.id] ? "tone-warning" : d.online === true ? "tone-success" : "tone-neutral"}`}
                >
                  {d.calibrating || calib[d.id]
                    ? "보정 중"
                    : d.online === true
                      ? "연결됨"
                      : d.online === false
                        ? "연결 끊김"
                        : "연결 미확인"}
                </span>
              </div>
              <div className="mobile-record-facts">
                <span>{d.connection} 연결</span>
                <span>
                  {d.lastSeenAt ? `마지막 연결 ${fmtDateTime(d.lastSeenAt)}` : "연결 기록 없음"}
                </span>
              </div>
            </button>
          ))}
          {!isLoading && !devices.length && (
            <div className="mobile-empty-card">
              <h2>등록된 센서가 없습니다</h2>
              <p>장치 추가를 눌러 사용할 센서를 등록해 주세요.</p>
            </div>
          )}
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="desktop-only lg:col-span-2 bg-surface border border-border rounded-lg overflow-hidden">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="text-[10px] text-muted border-b border-border bg-background/30 font-mono">
                  <th className="p-3 uppercase">이름</th>
                  <th className="p-3 uppercase">{locationLabel}</th>
                  <th className="p-3 uppercase">연결</th>
                  <th className="p-3 uppercase">상태</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {devices.map((d) => {
                  const running = !!calib[d.id] || d.calibrating;
                  return (
                    <tr
                      key={d.id}
                      onClick={() => setSelectedId(d.id)}
                      className={`hover:bg-black/5 cursor-pointer ${d.id === selected?.id ? "bg-primary/5" : ""}`}
                    >
                      <td className="p-3 font-medium">{presentation.label(d.name, "device")}</td>
                      <td className="p-3 font-mono">{presentation.label(d.room, "room")}</td>
                      <td className="p-3">
                        <span
                          className={`text-[9px] font-mono uppercase px-1.5 py-0.5 rounded border ${d.connection === "MQTT" ? "text-primary border-primary/40" : "text-warning border-warning/40"}`}
                        >
                          {d.connection}
                        </span>
                      </td>
                      <td className="p-3">
                        {running ? (
                          <span className="text-[10px] font-mono uppercase text-warning">
                            보정 중
                          </span>
                        ) : (
                          <span
                            className={`text-[10px] font-mono uppercase ${d.online ? "text-success" : "text-muted"}`}
                          >
                            {d.online ? "연결됨" : "연결 끊김"}
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
                {!isLoading && devices.length === 0 && (
                  <tr>
                    <td colSpan={4} className="p-8 text-center text-muted text-xs">
                      등록된 장치가 없습니다. 오른쪽 위 "+ 장치 추가"로 등록하세요.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {selected && (
            <div className="bg-surface border border-border rounded-lg p-4 space-y-4">
              <div>
                <SectionTitle>장치 상세</SectionTitle>
                <div className="text-sm font-semibold">
                  {presentation.label(selected.name, "device")}
                </div>
                <div className="text-[10px] font-mono text-muted">
                  {selected.mac && `장치 주소 ${selected.mac}`}{" "}
                  {selected.fw && `버전 ${selected.fw}`}
                </div>
              </div>

              <details className="device-info-disclosure">
                <summary>장치 정보</summary>
                <div className="grid grid-cols-2 gap-2 text-xs pt-3">
                  <Info
                    label="기준 신호 세기"
                    value={selected.baseRssi != null ? `${selected.baseRssi} dBm` : "—"}
                  />
                  <Info
                    label="현재 신호 세기"
                    value={selected.currentRssi != null ? `${selected.currentRssi} dBm` : "—"}
                    highlight
                  />
                  <Info label="AGC" value={selected.agc != null ? String(selected.agc) : "—"} />
                  <Info
                    label="신호 잡음"
                    value={selected.noiseFloor != null ? `${selected.noiseFloor} dBm` : "—"}
                  />
                  <Info
                    label="움직임 임계값"
                    value={
                      selected.presenceMvThreshold != null
                        ? selected.presenceMvThreshold.toFixed(2)
                        : "미측정"
                    }
                  />
                  <Info
                    label="재실 기준값"
                    value={
                      selected.wanderBaseline != null
                        ? selected.wanderBaseline.toFixed(2)
                        : "미측정"
                    }
                  />
                  <Info
                    label={
                      selected.connection === "MQTT"
                        ? "MQTT Topic"
                        : selected.connection === "SPI"
                          ? "SPI"
                          : "Serial Port"
                    }
                    value={
                      selected.connection === "MQTT"
                        ? (selected.mqttTopic ?? "—")
                        : selected.connection === "SPI"
                          ? "Pi5 SPI0 / Batch8"
                          : (selected.serialPort ?? "—")
                    }
                    wide
                  />
                  <Info
                    label="마지막 연결 확인"
                    value={selected.lastSeenAt ? fmtDateTime(selected.lastSeenAt) : "—"}
                    wide
                  />
                </div>
              </details>

              {!USE_MOCK ? (
                <p className="text-xs text-muted">
                  상단에서 모니터링을 시작하거나 빈방 보정을 진행할 수 있습니다.
                </p>
              ) : calib[selected.id] ? (
                <CalibratingPanel progress={calib[selected.id]} />
              ) : selected.calibrating ? (
                <div className="border border-warning/50 rounded p-3 text-center space-y-2">
                  <div className="text-[10px] font-mono uppercase text-warning">
                    캘리브레이션 진행 중으로 저장됨 ·{" "}
                    {CALIBRATION_STAGE_LABEL[selected.calibrationStage]}
                  </div>
                  <p className="text-[10px] text-muted">
                    이 브라우저에 진행 타이머가 없습니다(새로고침 등). 다시 시작하거나 상태를
                    초기화하세요.
                  </p>
                  <div className="flex gap-2 justify-center">
                    <button
                      onClick={() => setResetConfirm(selected)}
                      className="px-3 py-1.5 bg-warning text-black rounded text-[10px] font-mono uppercase font-bold"
                    >
                      다시 시작
                    </button>
                    <button
                      onClick={() => cancelStaleCalibration(selected)}
                      className="px-3 py-1.5 border border-border rounded text-[10px] font-mono uppercase text-muted"
                    >
                      초기화
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  onClick={() => setResetConfirm(selected)}
                  className="w-full py-2.5 bg-warning text-black rounded text-xs font-mono uppercase font-bold hover:brightness-110"
                >
                  장치 재설정 (Recalibrate)
                </button>
              )}

              <button
                onClick={() => removeDevice(selected)}
                disabled={remove.isPending}
                className="w-full py-1.5 border border-border rounded text-[10px] font-mono uppercase text-muted hover:text-primary disabled:opacity-50"
              >
                장치 삭제
              </button>
            </div>
          )}
        </div>

        {resetConfirm && (
          <ResetConfirmModal
            device={resetConfirm}
            onCancel={() => setResetConfirm(null)}
            onConfirm={() => {
              const target = resetConfirm;
              setResetConfirm(null);
              void startCalibration(target);
              if (USE_MOCK) toast("데모 보정 시작 · 약 61초 (실제 센서 보정 아님)");
            }}
          />
        )}

        {addOpen && (
          <AddDeviceModal
            isFacility={isFacility}
            busy={create.isPending}
            onClose={() => setAddOpen(false)}
            onCreate={async (input) => {
              try {
                const dev = await create.mutateAsync(input);
                setAddOpen(false);
                setSelectedId(dev.id);
                toast.success(
                  `${dev.name} 등록 완료${USE_MOCK ? " · 데모 보정 시작" : " · SPI 장치를 Pi5에 연결한 뒤 수신을 시작하세요"}`,
                );
                if (USE_MOCK) void startCalibration(dev);
              } catch (err) {
                toast.error(errMsg(err, "장치를 추가하지 못했습니다"));
              }
            }}
          />
        )}
      </div>
    </div>
  );
}

function Info({
  label,
  value,
  highlight,
  wide,
}: {
  label: string;
  value: string;
  highlight?: boolean;
  wide?: boolean;
}) {
  return (
    <div className={`bg-background border border-border rounded p-2 ${wide ? "col-span-2" : ""}`}>
      <div className="text-[9px] font-mono uppercase text-muted">{label}</div>
      <div className={`font-mono text-xs truncate ${highlight ? "text-primary font-bold" : ""}`}>
        {value}
      </div>
    </div>
  );
}

function CalibratingPanel({ progress }: { progress: CalibProgress }) {
  const secLeft = secondsLeft(progress.stage, progress.progress);
  return (
    <div className="border-2 border-primary rounded p-3 text-center">
      <div className="text-[10px] font-mono uppercase text-muted">
        {CALIBRATION_STAGE_LABEL[progress.stage]}
      </div>
      <div className="text-2xl font-mono font-bold text-primary my-1">{secLeft}s</div>
      <div className="h-1 bg-background rounded overflow-hidden">
        <div
          className="h-full bg-primary transition-all"
          style={{ width: `${progress.progress * 100}%` }}
        />
      </div>
      <p className="mt-2 text-[9px] text-muted font-mono">
        데모 타이머 · 엣지 연동 전까지 결과값은 시뮬레이션
      </p>
    </div>
  );
}

function ResetConfirmModal({
  device,
  onCancel,
  onConfirm,
}: {
  device: Device;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const alreadyRunning = isCalibrationDemoRunning(device.id);
  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-6">
      <div className="bg-surface border-2 border-warning rounded-lg max-w-md w-full">
        <div className="p-4 border-b border-border">
          <h3 className="text-sm font-mono uppercase tracking-widest text-warning">
            ⚠ 장치 재설정 확인
          </h3>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <p>
            <span className="font-semibold">{device.name}</span> ({device.room}) 장치를
            재설정합니다.
            {alreadyRunning && (
              <span className="ml-1 text-[10px] font-mono text-warning uppercase">
                진행 중인 타이머를 다시 시작합니다
              </span>
            )}
          </p>
          <div className="border border-warning/40 bg-warning/5 rounded p-3 text-xs space-y-1">
            <div className="font-semibold">진행 절차 (4단계, 총 약 61초):</div>
            <div>
              1. <b>30초간</b> 감지 공간에서 사람이 <b>완전히 벗어나</b> 있어야 합니다.
            </div>
            <div>2. 장치가 명령에 응답할 때까지 대기 (약 0.2초).</div>
            <div>3. 장치의 AGC 보정이 끝날 때까지 대기 (약 1초).</div>
            <div>
              4. 이어서 <b>30초간</b> 움직임 임계값/재실 baseline을 재수집합니다.
            </div>
          </div>
        </div>
        <div className="p-4 border-t border-border flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="px-4 py-2 border border-border rounded text-xs font-mono uppercase text-muted"
          >
            취소
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 bg-warning text-black rounded text-xs font-mono uppercase font-bold"
          >
            재설정 시작
          </button>
        </div>
      </div>
    </div>
  );
}

function AddDeviceModal({
  isFacility,
  busy,
  onClose,
  onCreate,
}: {
  isFacility: boolean;
  busy: boolean;
  onClose: () => void;
  onCreate: (input: {
    name: string;
    room: string;
    connection: Connection;
    serialPort?: string | null;
    mqttTopic?: string | null;
  }) => void;
}) {
  const [name, setName] = useState("");
  const [room, setRoom] = useState(isFacility ? "" : "거실");
  const [connection, setConnection] = useState<Connection>("MQTT");
  const [serialPort, setSerialPort] = useState("");
  const locationLabel = isFacility ? "호실" : "공간";
  const valid = !!name && !!room && (connection !== "SERIAL" || !!serialPort);

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-6">
      <div className="bg-surface border border-border rounded-lg max-w-sm w-full p-5 space-y-3">
        <h3 className="text-sm font-semibold">장치 추가</h3>
        <p className="text-[11px] text-muted">
          장치를 등록한 뒤 연결 상태를 확인하고 빈방 보정을 진행하세요.
        </p>
        <div>
          <label className="text-[10px] font-mono uppercase text-muted">장치 이름</label>
          <input
            placeholder={isFacility ? "예: 302호 장치" : "예: 거실 장치"}
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full mt-1 bg-background border border-border rounded px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="text-[10px] font-mono uppercase text-muted">{locationLabel}</label>
          {isFacility ? (
            <input
              placeholder="예: 302"
              value={room}
              onChange={(e) => setRoom(e.target.value)}
              className="w-full mt-1 bg-background border border-border rounded px-3 py-2 text-sm"
            />
          ) : (
            <select
              value={room}
              onChange={(e) => setRoom(e.target.value)}
              className="w-full mt-1 bg-background border border-border rounded px-3 py-2 text-sm"
            >
              {HOME_SPACES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          )}
        </div>
        <div>
          <label className="text-[10px] font-mono uppercase text-muted">연결 방식</label>
          <div className="grid grid-cols-3 gap-2 mt-1">
            {(["MQTT", "SERIAL", "SPI"] as Connection[]).map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => setConnection(c)}
                className={`py-2 rounded text-xs font-mono uppercase border ${connection === c ? "bg-primary text-primary-foreground border-primary" : "border-border text-muted"}`}
              >
                {c}
              </button>
            ))}
          </div>
          <p className="mt-1 text-[9px] text-muted font-mono">
            {connection === "MQTT"
              ? "MQTT 토픽은 서버가 wifiguard/{시설}/{장치} 형식으로 발급합니다."
              : connection === "SPI"
                ? "Pi5 로컬 수신용입니다. 등록 후 운영 패널에서 이 Pi5에 연결하세요."
                : "USB 직렬 연결입니다. 현재 수신기에서는 이 방식으로 수신을 시작할 수 없습니다."}
          </p>
        </div>
        {connection === "SERIAL" && (
          <div>
            <label className="text-[10px] font-mono uppercase text-muted">Serial Port</label>
            <input
              placeholder="COM4 또는 /dev/cu.usbmodemXXXX"
              value={serialPort}
              onChange={(e) => setSerialPort(e.target.value)}
              className="w-full mt-1 bg-background border border-border rounded px-3 py-2 text-sm font-mono"
            />
          </div>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <button
            onClick={onClose}
            className="px-4 py-2 border border-border rounded text-xs font-mono uppercase text-muted"
          >
            취소
          </button>
          <button
            onClick={() =>
              onCreate({
                name: name.trim(),
                room: room.trim(),
                connection,
                serialPort: connection === "SERIAL" ? serialPort.trim() : null,
                mqttTopic: null,
              })
            }
            disabled={!valid || busy}
            className="px-4 py-2 bg-primary text-primary-foreground rounded text-xs font-mono uppercase font-bold disabled:opacity-50"
          >
            장치 등록
          </button>
        </div>
      </div>
    </div>
  );
}
