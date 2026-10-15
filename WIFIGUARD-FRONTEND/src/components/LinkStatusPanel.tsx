/**
 * 기기 링크 진단 — `/ws/live` 의 `link` 블록을 보여 준다.
 *
 * 구 `LegacyLivePanels` 를 대체한다. 그쪽은 로컬 백엔드의 `/monitor/status` 를 1초마다
 * 폴링했고, HOME 계정 + `VITE_ENABLE_LIVE=1` 에서만 렌더됐다. 이제 클라우드 WS 하나로
 * HOME·FACILITY 모두 같은 값을 본다.
 *
 * 값이 없으면 **"모름"이라고 말한다.** 0 이나 "끊김"으로 채우면 텔레메트리가 아직 안 온 것과
 * 실제로 끊긴 것을 구별할 수 없다.
 */

import { useLive } from "@/api/realtime/useLiveStream";

const STATUS_LABEL: Record<string, string> = {
  idle: "연결 안 함",
  connecting: "연결 중",
  open: "연결됨",
  reconnecting: "재연결 중",
  unauthorized: "세션 만료",
};

export function LinkStatusPanel() {
  const live = useLive();
  const devices = Object.values(live.devices);
  const withLink = devices.filter((d) => d.link);

  if (live.status !== "open") {
    return (
      <div className="bg-surface border border-border rounded-lg p-4 flex items-start gap-3">
        <div className="text-muted text-lg leading-none">📡</div>
        <div>
          <div className="text-sm font-semibold">
            실시간 링크 · {STATUS_LABEL[live.status] ?? live.status}
          </div>
          <p className="text-xs text-muted leading-relaxed">
            {live.status === "unauthorized"
              ? "다시 로그인하면 실시간 정보가 표시됩니다."
              : "엣지가 MQTT 로 올린 텔레메트리가 도착하면 수신률·RSSI·프레임 통계가 여기 표시됩니다."}
            {live.lastError && (
              <span className="block mt-1 font-mono text-[10px] text-warning">
                {live.lastError}
              </span>
            )}
          </p>
        </div>
      </div>
    );
  }

  if (withLink.length === 0) {
    return (
      <div className="bg-surface border border-border rounded-lg p-4 flex items-start gap-3">
        <div className="text-success text-lg leading-none">📡</div>
        <div>
          <div className="text-sm font-semibold">실시간 링크 연결됨 · 기기 텔레메트리 대기</div>
          <p className="text-xs text-muted leading-relaxed">
            서버와는 연결됐지만 아직 어떤 기기의 텔레메트리도 오지 않았습니다. 엣지가 기동되면
            수신률·RSSI·프레임 통계가 표시됩니다.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-surface border border-border rounded-lg overflow-hidden">
      <div className="px-4 py-2.5 border-b border-border flex items-center justify-between">
        <h3 className="text-xs font-mono uppercase text-muted">엣지 링크 진단</h3>
        <span className="text-[10px] font-mono text-success px-2 py-0.5 rounded bg-success/10 border border-success/30">
          ● {withLink.length}대 수신 중
          {live.droppedFrames > 0 && (
            <span className="ml-1.5 text-warning">· 파싱 실패 {live.droppedFrames}</span>
          )}
        </span>
      </div>
      <table className="w-full text-left text-xs font-mono">
        <thead className="text-muted border-b border-border">
          <tr>
            <th className="px-4 py-2 font-normal">기기</th>
            <th className="px-4 py-2 font-normal">전송</th>
            <th className="px-4 py-2 font-normal">수신률</th>
            <th className="px-4 py-2 font-normal">RSSI</th>
            <th className="px-4 py-2 font-normal">프레임</th>
            <th className="px-4 py-2 font-normal">체크섬 오류</th>
            <th className="px-4 py-2 font-normal">재동기</th>
          </tr>
        </thead>
        <tbody>
          {withLink.map((d) => {
            const l = d.link!;
            const bad = l.checksum_errors > 0 || l.resyncs > 0;
            return (
              <tr key={d.device_id} className="border-b border-border/50 last:border-0">
                <td className="px-4 py-2">{d.device_id.slice(0, 8)}…</td>
                <td className="px-4 py-2">{l.transport}</td>
                <td className="px-4 py-2">{l.hz_1s != null ? `${l.hz_1s.toFixed(0)}Hz` : "—"}</td>
                <td className="px-4 py-2">{l.rssi != null ? `${l.rssi}dBm` : "—"}</td>
                <td className="px-4 py-2">{l.frames_ok.toLocaleString()}</td>
                <td className={`px-4 py-2 ${bad ? "text-warning" : ""}`}>{l.checksum_errors}</td>
                <td className={`px-4 py-2 ${bad ? "text-warning" : ""}`}>{l.resyncs}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
