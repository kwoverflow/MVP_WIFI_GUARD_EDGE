import { Bell, ChevronDown, Clock3, MapPin } from "lucide-react";
import type { FallEvent, FallResponse } from "@/lib/domain";
import { FALL_RESPONSES, FALL_RESPONSE_LABEL } from "@/lib/domain";
import { fmtDateTime } from "@/lib/format";
import { ResponseBadge } from "./MonitoringHeader";
import { FallHistoryDetails } from "./FallHistoryDetails";

export function MobileFallCard({
  fall,
  residentName,
  roomName,
  open,
  busy,
  onToggle,
  onOpen,
  onRespond,
}: {
  fall: FallEvent;
  residentName: string;
  roomName: string;
  open: boolean;
  busy: boolean;
  onToggle: () => void;
  onOpen: () => void;
  onRespond: (response: FallResponse) => void;
}) {
  return (
    <article
      className={`mobile-record-card mobile-fall-card ${fall.response === "PENDING" ? "needs-response" : ""}`}
    >
      <div className="mobile-record-heading">
        {fall.response === "PENDING" ? (
          <button
            className="mobile-record-symbol"
            aria-label="낙상 알림 크게 보기"
            onClick={onOpen}
          >
            <Bell size={20} />
          </button>
        ) : (
          <span className="mobile-record-symbol">
            <Bell size={20} />
          </span>
        )}
        <div>
          <small>낙상 알림</small>
          <h2>{residentName}</h2>
        </div>
        <ResponseBadge response={fall.response} />
      </div>
      <div className="mobile-record-facts">
        <span>
          <MapPin size={14} />
          {roomName}
        </span>
        <span>
          <Clock3 size={14} />
          {fmtDateTime(fall.occurredAt)}
        </span>
      </div>
      <div className="mobile-fall-actions">
        {fall.response === "PENDING" && (
          <button
            className="primary-button"
            disabled={busy}
            onClick={() => onRespond("ACKNOWLEDGED")}
          >
            확인했어요
          </button>
        )}
        <button className="secondary-button" aria-expanded={open} onClick={onToggle}>
          상세 보기 <ChevronDown size={15} className={open ? "rotate-180" : ""} />
        </button>
        <select
          aria-label={`${residentName} 알림 처리 상태`}
          value={fall.response}
          disabled={busy}
          onChange={(e) => onRespond(e.target.value as FallResponse)}
        >
          {FALL_RESPONSES.map((value) => (
            <option key={value} value={value}>
              {FALL_RESPONSE_LABEL[value]}
            </option>
          ))}
        </select>
      </div>
      {open && <FallHistoryDetails fall={fall} />}
    </article>
  );
}
