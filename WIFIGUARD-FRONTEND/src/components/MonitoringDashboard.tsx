import { useState, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import {
  Activity,
  ArrowRight,
  Bell,
  Check,
  ChevronDown,
  HeartPulse,
  Radio,
  Settings2,
  ShieldCheck,
  Users,
  Wifi,
  WifiOff,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useAuth } from "@/api/auth";
import { useLocalEdgeStatus } from "@/api/local-edge";
import {
  useConfig,
  useDetectionStatus,
  useDevices,
  useFalls,
  useMvHistory,
  useResidents,
} from "@/api/queries";
import { useLiveDevice } from "@/api/realtime/useLiveStream";
import { usePresentation } from "@/components/usePresentation";
import { Header, ResponseBadge } from "@/components/MonitoringHeader";
import { fmtDateTime, fmtTime, unifiedStatusLabel } from "@/lib/format";
import { monitoringSummary, type SummaryTone } from "@/lib/monitoring-summary";
import { useTick } from "@/lib/mock-store";

export function MonitoringDashboard() {
  if (import.meta.env.VITE_USE_MOCK === "1") useTick(); // eslint-disable-line react-hooks/rules-of-hooks -- immutable build setting
  const { user } = useAuth();
  const facility = user?.service === "FACILITY";
  const detection = useDetectionStatus();
  const localQuery = useLocalEdgeStatus();
  const local = localQuery.data;
  const presentation = usePresentation();
  const { data: residents = [] } = useResidents();
  const { data: devices = [] } = useDevices();
  const { data: config } = useConfig();
  const recent = useFalls({ limit: 5 });
  const pending = useFalls({ response: "PENDING", limit: 1 });
  const [selection, setSelection] = useState<string | null>(null);
  const [showGuide, setShowGuide] = useState(false);
  const activeResident = residents.find((r) => r.id === selection) ?? residents[0];
  const selectedDevice = facility
    ? devices.find((d) => d.id === activeResident?.deviceId)
    : (devices.find((d) => d.id === selection) ??
      devices.find((d) => d.id === residents[0]?.deviceId) ??
      devices[0]);
  const resident = facility
    ? activeResident
    : residents.find(
        (r) => r.deviceId === selectedDevice?.id || r.deviceIds.includes(selectedDevice?.id ?? ""),
      );
  const live = useLiveDevice(selectedDevice?.id);
  const series = useMvHistory(selectedDevice?.id);
  const summary = monitoringSummary(
    live,
    resident,
    detection,
    import.meta.env.VITE_USE_MOCK === "1",
  );
  const boundLocal = local?.enabled && local.device_id === selectedDevice?.id;
  const calibration = boundLocal ? local.calibration : null;
  const calibrated = boundLocal
    ? calibration?.phase === "done" && local.runtime_current === true
    : selectedDevice?.calibrationStage === "DONE";
  const threshold =
    live?.presence?.mv_threshold ?? resident?.thresholdOverride ?? config?.presenceMvThreshold ?? 2;
  const chart = series
    .filter((p) => Number.isFinite(p.mv) && Number.isFinite(p.t))
    .map((p) => ({ time: p.t, mv: p.mv }));
  // Device heartbeat timestamps can keep changing even when CSI stops.
  // Label only the timestamp of actual received movement evidence here.
  const lastDetectionAt = chart.at(-1)?.time;
  const pendingTotal = pending.data?.total;
  const hasPending = pendingTotal != null && pendingTotal > 0;
  const steps = [
    {
      title: "장치 연결",
      detail: "센서와 수신 상태 확인",
      done: summary.connected,
      to: "/devices",
    },
    { title: "빈방 보정", detail: "사람이 없는 상태로 약 60초", done: calibrated, to: "/devices" },
    { title: "대상자 등록", detail: "대상자와 공간을 연결", done: !!resident, to: "/residents" },
    { title: "알림 설정", detail: "수신자와 전송 경로 확인", done: false, to: "/notifications" },
  ];
  return (
    <div className="monitoring-dashboard">
      <Header title="모니터링" />
      <div className="dashboard-content">
        <div className="dashboard-intro">
          <div>
            <h1>실시간 현황</h1>
          </div>
          <button
            className="secondary-button"
            aria-expanded={showGuide}
            onClick={() => setShowGuide(!showGuide)}
          >
            <Settings2 size={16} /> 사용 안내{" "}
            <ChevronDown size={15} className={showGuide ? "rotate-180" : ""} />
          </button>
        </div>

        {showGuide && (
          <section className="setup-guide" aria-label="처음 사용 안내">
            <div className="section-heading">
              <div>
                <h2>처음 시작하는 순서</h2>
                <p>장치 연결과 빈방 보정을 마친 뒤 대상자와 알림을 설정하세요.</p>
              </div>
            </div>
            <div className="setup-steps">
              {steps.map((step, i) => (
                <Link key={step.title} to={step.to} className="setup-step">
                  <span className={step.done ? "step-number is-done" : "step-number"}>
                    {step.done ? <Check size={17} /> : i + 1}
                  </span>
                  <strong>{step.title}</strong>
                  <span>{step.detail}</span>
                  <small>
                    {step.done
                      ? "현재 확인됨"
                      : step.title === "알림 설정"
                        ? "설정 화면에서 확인"
                        : "설정 확인하기"}{" "}
                    <ArrowRight size={12} />
                  </small>
                </Link>
              ))}
            </div>
          </section>
        )}

        <section className="space-selector" aria-label={facility ? "대상자 선택" : "공간 선택"}>
          <div className="space-heading">
            <span className="space-icon">
              <Users size={20} />
            </span>
            <div>
              <span className="eyebrow">선택한 공간</span>
              <h2>
                {presentation.label(
                  selectedDevice?.room ?? resident?.room ?? "공간 미연결",
                  "room",
                )}
                <span> {presentation.label(resident?.name ?? "대상자 미등록", "resident")}</span>
              </h2>
            </div>
          </div>
          <div className="space-actions">
            {(facility ? residents.length > 1 : devices.length > 1) && (
              <select
                aria-label={facility ? "모니터링 대상자" : "모니터링 장치"}
                value={facility ? resident?.id : selectedDevice?.id}
                onChange={(e) => setSelection(e.target.value)}
              >
                {facility
                  ? residents.map((r) => (
                      <option key={r.id} value={r.id}>
                        {presentation.label(r.room, "room")} ·{" "}
                        {presentation.label(r.name, "resident")}
                      </option>
                    ))
                  : devices.map((d) => (
                      <option key={d.id} value={d.id}>
                        {presentation.label(d.room, "room")} ·{" "}
                        {presentation.label(d.name, "device")}
                      </option>
                    ))}
              </select>
            )}
            <Link to="/residents">
              대상 관리 <ArrowRight size={14} />
            </Link>
          </div>
        </section>

        {facility && residents.length > 1 && (
          <section className="resident-overview" aria-label="전체 대상자 상태">
            {residents.map((r) => (
              <button
                key={r.id}
                onClick={() => setSelection(r.id)}
                className={r.id === resident?.id ? "selected" : ""}
              >
                <strong>
                  {presentation.label(r.room, "room")} · {presentation.label(r.name, "resident")}
                </strong>
                <span>{unifiedStatusLabel(r.state, r.presence)}</span>
              </button>
            ))}
          </section>
        )}

        <section className="status-grid" aria-label="현재 상태 요약">
          <StatusCard
            icon={<Users size={21} />}
            label="재실 상태"
            value={summary.presence}
            detail={summary.presenceDetail}
            tone={summary.presenceTone}
          />
          <StatusCard
            icon={<HeartPulse size={21} />}
            label="낙상 감지"
            value={summary.fall}
            detail={summary.fallDetail}
            tone={summary.fallTone}
          />
          <StatusCard
            icon={summary.connected ? <Wifi size={21} /> : <WifiOff size={21} />}
            label="센서 연결"
            value={
              summary.connected ? "수신 중" : live?.online === false ? "연결 끊김" : "수신 대기"
            }
            detail={
              lastDetectionAt != null
                ? `마지막 수신 ${fmtTime(lastDetectionAt)}`
                : "센서 연결을 기다리고 있습니다"
            }
            tone={summary.connected ? "success" : "neutral"}
          />
          <StatusCard
            icon={<Bell size={21} />}
            label="확인할 알림"
            value={
              pending.isError ? "조회 실패" : pendingTotal == null ? "조회 중" : `${pendingTotal}건`
            }
            detail="처리가 필요한 낙상 알림"
            tone={hasPending ? "warning" : "neutral"}
            link="/history"
          />
        </section>

        {(!summary.connected || (boundLocal && !calibrated)) && (
          <div className="readiness-notice" role="status">
            <Radio size={19} />
            <div>
              <strong>
                {!summary.connected ? "모니터링을 준비해 주세요" : "빈방 보정 상태를 확인해 주세요"}
              </strong>
              <p>
                {!summary.connected
                  ? "장치 연결을 확인하고 모니터링을 시작하세요."
                  : "사람이 없는 상태에서 빈방 보정을 완료해 주세요."}
              </p>
            </div>
            <Link to="/devices" className="secondary-button">
              장치 관리 <ArrowRight size={14} />
            </Link>
          </div>
        )}

        <div className="monitoring-main-grid">
          <section className="signal-panel">
            <div className="section-heading">
              <div>
                <h2>
                  <Activity size={18} /> 움직임
                </h2>
                <p>시간별 움직임 변화</p>
              </div>
              <span
                className={`status-pill ${summary.connected ? "tone-success" : "tone-neutral"}`}
              >
                {summary.connected ? "실시간 수신" : "수신 대기"}
              </span>
            </div>
            <div className="signal-chart">
              {chart.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={chart} margin={{ top: 16, right: 15, bottom: 5, left: -18 }}>
                    <defs>
                      <linearGradient id="motionFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="var(--brand-red)" stopOpacity={0.18} />
                        <stop offset="100%" stopColor="var(--brand-red)" stopOpacity={0.01} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid vertical={false} stroke="#edf1f5" />
                    <XAxis
                      dataKey="time"
                      tickFormatter={(t) => fmtTime(t)}
                      minTickGap={55}
                      tick={{ fontSize: 11, fill: "#7a8797" }}
                      axisLine={false}
                      tickLine={false}
                    />
                    <YAxis
                      tickFormatter={(value) => Number(value).toFixed(1)}
                      tick={{ fontSize: 11, fill: "#7a8797" }}
                      axisLine={false}
                      tickLine={false}
                      domain={[0, "dataMax + 0.5"]}
                    />
                    <Tooltip
                      labelFormatter={(value) => fmtTime(Number(value))}
                      formatter={(value) => [Number(value).toFixed(3), "움직임 지수"]}
                      contentStyle={{ borderRadius: 12, border: "1px solid #e4eaf1", fontSize: 12 }}
                    />
                    <ReferenceLine
                      y={threshold}
                      stroke="#eaa248"
                      strokeDasharray="5 5"
                      label={{
                        value: "재실 판단 기준",
                        fill: "#9d691c",
                        fontSize: 10,
                        position: "insideTopRight",
                      }}
                    />
                    <Area
                      type="monotone"
                      dataKey="mv"
                      stroke="var(--brand-red)"
                      fill="url(#motionFill)"
                      strokeWidth={2}
                      isAnimationActive={false}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <div className="chart-empty">
                  <span>
                    <Radio size={29} />
                  </span>
                  <h3>센서 연결 대기</h3>
                  <p>장치를 연결하면 움직임 기록이 표시됩니다.</p>
                  <Link to="/devices" className="text-button">
                    장치 관리 <ArrowRight size={14} />
                  </Link>
                </div>
              )}
            </div>
            <div className="signal-footer">
              <span>
                <i className="legend-dot" /> 움직임 지수
              </span>
              <span className="threshold-legend">— 재실 판단 기준</span>
              <span>{!summary.connected && chart.length > 0 ? "연결 끊김 · 이전 기록" : ""}</span>
            </div>
          </section>
          <section className={`attention-panel ${hasPending ? "has-pending" : ""}`}>
            <div className="section-heading">
              <h2>
                <Bell size={18} /> 확인이 필요한 항목
              </h2>
            </div>
            <div className="attention-body">
              <span className="attention-icon">
                {hasPending ? <Bell size={26} /> : <ShieldCheck size={28} />}
              </span>
              <h3>
                {hasPending
                  ? `확인 대기 ${pendingTotal}건`
                  : pending.isError
                    ? "알림 이력을 불러오지 못했어요"
                    : pendingTotal == null
                      ? "알림 이력을 확인하고 있어요"
                      : "확인 대기 알림이 없습니다"}
              </h3>
              <p>
                {hasPending
                  ? "이력에서 대상자와 발생 시각을 확인하고 처리 상태를 기록해 주세요."
                  : "최근 알림과 처리 상태를 이력에서 확인할 수 있습니다."}
              </p>
              <Link to="/history" className={hasPending ? "primary-button" : "secondary-button"}>
                낙상 이력 확인 <ArrowRight size={15} />
              </Link>
            </div>
          </section>
        </div>

        <section className="recent-events">
          <div className="section-heading">
            <div>
              <h2>최근 낙상 이력</h2>
              <p>감지 이후의 확인·대응 상태</p>
            </div>
            <Link to="/history" className="text-button">
              전체 이력 <ArrowRight size={14} />
            </Link>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>발생 시각</th>
                  <th>대상자 · 공간</th>
                  <th>처리 상태</th>
                </tr>
              </thead>
              <tbody>
                {recent.data?.items.map((f) => (
                  <tr key={f.id}>
                    <td>{fmtDateTime(f.occurredAt)}</td>
                    <td>
                      <strong>{presentation.label(f.residentName, "resident")}</strong>
                      <small>{presentation.label(f.room, "room")}</small>
                    </td>
                    <td>
                      <ResponseBadge response={f.response} />
                    </td>
                  </tr>
                ))}
                {!recent.data?.items.length && (
                  <tr>
                    <td colSpan={3} className="table-empty">
                      {recent.isLoading
                        ? "이력을 불러오는 중입니다"
                        : recent.isError
                          ? "이력 조회에 실패했습니다. 연결을 확인해 주세요."
                          : "아직 저장된 낙상 이력이 없습니다"}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}

function StatusCard({
  icon,
  label,
  value,
  detail,
  tone,
  link,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  detail: string;
  tone: SummaryTone;
  link?: "/history";
}) {
  return (
    <div className={`user-status-card tone-${tone}`}>
      <div className="card-label">
        <span>{label}</span>
        <span className="card-icon">{icon}</span>
      </div>
      <div className="card-value">{value}</div>
      <p>{detail}</p>
      {link && (
        <Link to={link} className="card-link" aria-label="확인 대기 알림 이력 보기">
          <ArrowRight size={16} />
        </Link>
      )}
    </div>
  );
}
