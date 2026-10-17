import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { toast } from "sonner";
import { Header, SectionTitle } from "./index";
import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useCreateResident, useDeleteResident, useUpdateResident } from "@/api/mutations";
import { useDevices, useResidents } from "@/api/queries";
import { HOME_SPACES, type Device, type Resident } from "@/lib/domain";
import { DETECTION_INACTIVE_LABEL } from "@/lib/format";
import { usePresentation } from "@/components/usePresentation";
import { UserRound } from "lucide-react";

export const Route = createFileRoute("/residents")({
  head: () => ({ meta: [{ title: "입소자 관리 · CSI-Guard" }] }),
  component: ResidentsPage,
});

/** 편집 폼 상태 — 서버 스키마(ResidentIn/Patch)에 대응. id 가 없으면 신규. */
interface ResidentDraft {
  id?: string;
  name: string;
  room: string;
  age: number | null;
  caregiver: string;
  deviceId: string | null;
  deviceIds: string[];
  thresholdOverride: number | null;
}

function toDraft(r: Resident): ResidentDraft {
  return {
    id: r.id,
    name: r.name,
    room: r.room,
    age: r.age ?? null,
    caregiver: r.caregiver ?? "",
    deviceId: r.deviceId ?? null,
    deviceIds: r.deviceIds,
    thresholdOverride: r.thresholdOverride ?? null,
  };
}

/** residents.tsx 의 기존 규칙 그대로(서버도 같은 규칙으로 최종 정규화한다). */
function normalizeMapping(deviceId: string | null, deviceIds: string[]) {
  const ids = deviceIds.length > 0 ? deviceIds : deviceId ? [deviceId] : [];
  const primary = deviceId && ids.includes(deviceId) ? deviceId : (ids[0] ?? null);
  return { deviceId: primary, deviceIds: ids };
}

function ResidentsPage() {
  const { user } = useAuth();
  const presentation = usePresentation();
  const isFacility = user?.service === "FACILITY";
  // 스코핑은 서버가 한다.
  const { data: residents = [], isLoading } = useResidents();
  const { data: devices = [] } = useDevices();
  const create = useCreateResident();
  const update = useUpdateResident();
  const remove = useDeleteResident();
  const [editing, setEditing] = useState<ResidentDraft | null>(null);

  const blank = (): ResidentDraft => ({
    name: "",
    room: isFacility ? "" : "거실",
    age: null,
    caregiver: isFacility ? "" : "본인",
    deviceId: devices[0]?.id ?? null,
    deviceIds: devices[0] ? [devices[0].id] : [],
    thresholdOverride: null,
  });

  const save = async (d: ResidentDraft) => {
    const mapping = normalizeMapping(d.deviceId, d.deviceIds);
    try {
      if (d.id) {
        await update.mutateAsync({
          id: d.id,
          patch: {
            name: d.name,
            room: d.room,
            age: d.age,
            caregiver: d.caregiver || null,
            deviceId: mapping.deviceId,
            deviceIds: mapping.deviceIds,
            thresholdOverride: d.thresholdOverride,
            clearThresholdOverride: d.thresholdOverride === null,
          },
        });
      } else {
        await create.mutateAsync({
          name: d.name,
          room: d.room,
          age: d.age,
          caregiver: d.caregiver || null,
          deviceId: mapping.deviceId,
          deviceIds: mapping.deviceIds,
          thresholdOverride: d.thresholdOverride,
        });
      }
      toast.success(`${d.name} 저장됨`);
      setEditing(null);
    } catch (err) {
      toast.error(err instanceof ApiError || err instanceof Error ? err.message : "저장 실패");
    }
  };

  const removeOne = async (r: Resident) => {
    if (!confirm(`${r.name} 삭제? 낙상 이력은 보존되고 수신자 매핑은 해제됩니다.`)) return;
    try {
      await remove.mutateAsync(r.id);
      toast(`${r.name} 삭제됨`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "삭제 실패");
    }
  };

  const title = isFacility ? "입소자 · 디바이스 관리" : "가족 · 디바이스 관리";
  const subtitle = isFacility
    ? "입소자 정보와 방 번호를 등록하고, 담당 장치를 연결할 수 있습니다."
    : "가족 구성원 정보를 등록하고, 사용할 장치를 연결할 수 있습니다.";

  return (
    <div>
      <Header title={title} />
      <div className="p-6 space-y-4 max-w-6xl">
        <div className="management-heading flex justify-between items-center">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight mb-1">
              {isFacility ? "입소자 관리" : "대상자 관리"}
            </h1>
            <p className="text-sm text-muted">{subtitle}</p>
          </div>
          <button
            onClick={() => setEditing(blank())}
            className="px-4 py-2 bg-primary text-primary-foreground rounded text-xs font-mono uppercase font-bold"
          >
            + {isFacility ? "거주자 등록" : "사용자 등록"}
          </button>
        </div>

        <div className="mobile-record-list mobile-only" aria-label="등록된 대상자 목록">
          {residents.map((r) => (
            <article key={r.id} className="mobile-record-card">
              <div className="mobile-record-heading">
                <span className="mobile-record-symbol">
                  <UserRound size={21} />
                </span>
                <div>
                  <small>{presentation.label(r.room, "room")}</small>
                  <h2>{presentation.label(r.name, "resident")}</h2>
                </div>
                <span
                  className={`status-pill ${r.online === true ? "tone-success" : "tone-neutral"}`}
                >
                  {r.online === true ? "연결됨" : r.online === false ? "연결 끊김" : "연결 미확인"}
                </span>
              </div>
              <dl className="mobile-person-facts">
                <div>
                  <dt>나이</dt>
                  <dd>{r.age != null ? `${r.age}세` : "미등록"}</dd>
                </div>
                <div>
                  <dt>{isFacility ? "담당자" : "관계"}</dt>
                  <dd>{r.caregiver || "미등록"}</dd>
                </div>
              </dl>
              <p className="mobile-sensor-mapping">
                주 장치 ·{" "}
                {presentation.label(
                  devices.find((d) => d.id === r.deviceId)?.name ?? "미연결",
                  "device",
                )}
              </p>
              <div className="mobile-record-actions">
                <button className="secondary-button" onClick={() => setEditing(toDraft(r))}>
                  정보 수정
                </button>
                <button className="text-button" onClick={() => void removeOne(r)}>
                  삭제
                </button>
              </div>
            </article>
          ))}
          {!isLoading && !residents.length && (
            <div className="mobile-empty-card">
              <h2>대상자를 등록해 주세요</h2>
              <p>이름과 공간을 입력하고 사용할 센서를 연결할 수 있습니다.</p>
            </div>
          )}
        </div>
        <div className="desktop-only bg-surface border border-border rounded-lg overflow-hidden">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-[10px] text-muted border-b border-border bg-background/30 font-mono">
                <th className="p-3 font-medium uppercase">{isFacility ? "호실" : "공간"}</th>
                <th className="p-3 font-medium uppercase">이름</th>
                <th className="p-3 font-medium uppercase">나이</th>
                <th className="p-3 font-medium uppercase">{isFacility ? "담당자" : "관계"}</th>
                <th className="p-3 font-medium uppercase">연결 장치</th>
                <th className="p-3 font-medium uppercase">감지 기준</th>
                <th className="p-3 font-medium uppercase">연결 상태</th>
                <th className="p-3 font-medium uppercase text-right">관리</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {residents.map((r) => {
                const names = r.deviceIds.map((id) => {
                  const d = devices.find((x) => x.id === id);
                  return {
                    id,
                    label: presentation.label(d?.name ?? "장치 정보 없음", "device"),
                    primary: id === r.deviceId,
                  };
                });
                return (
                  <tr key={r.id} className="hover:bg-black/5">
                    <td className="p-3 font-mono">{presentation.label(r.room, "room")}</td>
                    <td className="p-3 font-medium">{presentation.label(r.name, "resident")}</td>
                    <td className="p-3 font-mono text-muted">{r.age ?? "—"}</td>
                    <td className="p-3 text-sm">{r.caregiver ?? "—"}</td>
                    <td className="p-3 font-mono text-xs">
                      {names.length === 0 ? (
                        <span className="text-muted">—</span>
                      ) : (
                        <div className="flex flex-wrap gap-1">
                          {names.map((n) => (
                            <span
                              key={n.id}
                              className={`px-1.5 py-0.5 rounded border bg-background text-[10px] ${n.primary ? "border-primary/40 text-primary" : "border-border"}`}
                            >
                              {n.primary ? "★ " : ""}
                              {n.label}
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                    <td className="p-3 font-mono text-xs">
                      {r.thresholdOverride != null ? (
                        <span className="text-warning">{r.thresholdOverride}</span>
                      ) : (
                        <span className="text-muted">기본값</span>
                      )}
                    </td>
                    <td className="p-3">
                      <span
                        className={`text-[10px] font-mono uppercase ${r.online === true ? "text-success" : "text-muted"}`}
                      >
                        {r.online === null || r.online === undefined
                          ? DETECTION_INACTIVE_LABEL
                          : r.online
                            ? "연결됨"
                            : "연결 끊김"}
                      </span>
                    </td>
                    <td className="p-3 text-right space-x-2">
                      <button
                        onClick={() => setEditing(toDraft(r))}
                        className="text-[10px] font-mono uppercase text-muted hover:text-foreground"
                      >
                        수정
                      </button>
                      <button
                        onClick={() => removeOne(r)}
                        className="text-[10px] font-mono uppercase text-primary hover:brightness-110"
                      >
                        삭제
                      </button>
                    </td>
                  </tr>
                );
              })}
              {!isLoading && residents.length === 0 && (
                <tr>
                  <td colSpan={8} className="p-8 text-center text-muted text-xs">
                    {isFacility ? "등록된 거주자가 없습니다." : "등록된 사용자가 없습니다."}
                    {devices.length === 0 &&
                      " 장치는 아직 없어도 등록할 수 있으며, 이후 장치 설정에서 매핑을 추가하세요."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {editing && (
          <EditModal
            draft={editing}
            devices={devices}
            isFacility={isFacility}
            busy={create.isPending || update.isPending}
            onClose={() => setEditing(null)}
            onSave={save}
          />
        )}
      </div>
    </div>
  );
}

function EditModal({
  draft,
  devices,
  isFacility,
  busy,
  onClose,
  onSave,
}: {
  draft: ResidentDraft;
  devices: Device[];
  isFacility: boolean;
  busy: boolean;
  onClose: () => void;
  onSave: (r: ResidentDraft) => void;
}) {
  const [r, setR] = useState<ResidentDraft>({
    ...draft,
    ...normalizeMapping(draft.deviceId, draft.deviceIds),
  });
  const presentation = usePresentation();

  const toggleDevice = (id: string) => {
    setR((prev) => {
      const next = prev.deviceIds.includes(id)
        ? prev.deviceIds.filter((x) => x !== id)
        : [...prev.deviceIds, id];
      const primary =
        prev.deviceId && next.includes(prev.deviceId) ? prev.deviceId : (next[0] ?? null);
      return { ...prev, deviceIds: next, deviceId: primary };
    });
  };
  const setPrimary = (id: string) => {
    setR((prev) => ({
      ...prev,
      deviceId: id,
      deviceIds: prev.deviceIds.includes(id) ? prev.deviceIds : [...prev.deviceIds, id],
    }));
  };

  return (
    <div className="fixed inset-0 bg-black/70 z-40 flex items-center justify-center p-6">
      <div className="bg-surface border border-border rounded-lg max-w-lg w-full max-h-[90vh] overflow-y-auto">
        <div className="p-4 border-b border-border">
          <h3 className="text-sm font-mono uppercase tracking-widest">대상자 정보</h3>
        </div>
        <div className="p-6 grid grid-cols-2 gap-4">
          <Field label="이름">
            <input
              value={r.name}
              onChange={(e) => setR({ ...r, name: e.target.value })}
              className={inputCls}
            />
          </Field>
          <Field label={isFacility ? "방 번호" : "주 사용 공간"}>
            {isFacility ? (
              <input
                value={r.room}
                onChange={(e) => setR({ ...r, room: e.target.value })}
                className={inputCls}
              />
            ) : (
              <select
                value={r.room}
                onChange={(e) => setR({ ...r, room: e.target.value })}
                className={inputCls}
              >
                {HOME_SPACES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <Field label="나이">
            <input
              type="number"
              value={r.age ?? ""}
              onChange={(e) =>
                setR({ ...r, age: e.target.value === "" ? null : Number(e.target.value) })
              }
              className={inputCls}
            />
          </Field>
          <Field label={isFacility ? "담당 요양사" : "관계 / 메모"}>
            <input
              value={r.caregiver}
              onChange={(e) => setR({ ...r, caregiver: e.target.value })}
              className={inputCls}
            />
          </Field>
          <Field label="개별 움직임 감지 기준">
            <input
              type="number"
              step="0.1"
              placeholder="기본값 사용"
              value={r.thresholdOverride ?? ""}
              onChange={(e) =>
                setR({
                  ...r,
                  thresholdOverride: e.target.value === "" ? null : Number(e.target.value),
                })
              }
              className={inputCls}
            />
          </Field>
          <Field label="상태">
            <div className="text-xs text-muted pt-1.5">
              온라인 여부는 실시간 경로 연결 후 자동 표시됩니다.
            </div>
          </Field>

          <div className="col-span-2">
            <div className="text-[10px] font-mono text-muted uppercase mb-1">
              연결 장치 ({r.deviceIds.length})
            </div>
            <div className="text-[10px] text-muted mb-2">
              여러 장치를 연결할 수 있습니다. ★ 표시는 주 장치입니다.
            </div>
            <div className="border border-border rounded divide-y divide-border max-h-56 overflow-y-auto">
              {devices.length === 0 && (
                <div className="p-3 text-xs text-muted text-center">
                  사용 가능한 장치가 없습니다.
                </div>
              )}
              {devices.map((d) => {
                const checked = r.deviceIds.includes(d.id);
                const isPrimary = r.deviceId === d.id;
                return (
                  <label
                    key={d.id}
                    className={`flex items-center gap-2 px-3 py-2 text-sm cursor-pointer ${checked ? "bg-primary/5" : ""}`}
                  >
                    <input type="checkbox" checked={checked} onChange={() => toggleDevice(d.id)} />
                    <div className="flex-1">
                      <div className="font-medium">
                        {presentation.label(d.name, "device")}{" "}
                        <span className="text-[10px] font-mono text-muted">
                          {presentation.label(d.room, "room")}
                        </span>
                      </div>
                      <div className="text-[10px] font-mono text-muted truncate">
                        {d.mqttTopic ?? d.serialPort}
                      </div>
                    </div>
                    {checked && (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.preventDefault();
                          setPrimary(d.id);
                        }}
                        className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${isPrimary ? "border-primary text-primary" : "border-border text-muted"}`}
                        title="주 장치로 설정"
                      >
                        {isPrimary ? "★ 주 장치" : "주 장치로 설정"}
                      </button>
                    )}
                  </label>
                );
              })}
            </div>
          </div>
        </div>
        <div className="p-4 border-t border-border flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-4 py-2 border border-border rounded text-xs font-mono uppercase text-muted"
          >
            취소
          </button>
          <button
            onClick={() => onSave(r)}
            disabled={!r.name || !r.room || busy}
            className="px-4 py-2 bg-primary text-primary-foreground rounded text-xs font-mono uppercase font-bold disabled:opacity-40"
          >
            저장
          </button>
        </div>
      </div>
    </div>
  );
}

const inputCls =
  "w-full bg-background border border-border rounded px-2 py-1.5 text-sm font-mono focus:outline-none focus:border-muted";
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] font-mono text-muted uppercase mb-1">{label}</div>
      {children}
    </div>
  );
}
