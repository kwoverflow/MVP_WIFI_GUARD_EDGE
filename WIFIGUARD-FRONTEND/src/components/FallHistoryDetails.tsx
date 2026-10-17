import type { FallEvent } from "@/lib/domain";
import { fmtDateTime } from "@/lib/format";
import { ResponseBadge } from "./MonitoringHeader";

/** Show recorded facts only. A saved alert does not contain a measured MV waveform. */
export function FallHistoryDetails({ fall }: { fall: FallEvent }) {
  return (
    <section className="fall-history-detail" aria-label="알림 상세">
      <dl>
        <div>
          <dt>발생 시각</dt>
          <dd>{fmtDateTime(fall.occurredAt)}</dd>
        </div>
        <div>
          <dt>감지 확률</dt>
          <dd>{(fall.confidence * 100).toFixed(1)}%</dd>
        </div>
        <div>
          <dt>지속 시간</dt>
          <dd>{fall.durationS.toFixed(2)}초</dd>
        </div>
        <div>
          <dt>처리 상태</dt>
          <dd>
            <ResponseBadge response={fall.response} />
          </dd>
        </div>
        <div>
          <dt>처리 시각</dt>
          <dd>{fall.respondedAt ? fmtDateTime(fall.respondedAt) : "미처리"}</dd>
        </div>
      </dl>
      <p>이 알림에 저장된 움직임 기록이 없습니다.</p>
    </section>
  );
}
