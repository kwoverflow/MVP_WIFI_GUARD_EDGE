import { Link, createFileRoute } from "@tanstack/react-router";
import { BellRing, Mail, Smartphone } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Header, SectionTitle } from "./index";
import { useAuth } from "@/api/auth";
import { ApiError, apiFetch, USE_MOCK } from "@/api/client";
import { useCreateRecipient, useDeleteRecipient, useUpdateRecipient } from "@/api/mutations";
import { useRecipients, useResidents } from "@/api/queries";
import { usePresentation } from "@/components/usePresentation";
import {
  RECIPIENT_ROLES,
  RECIPIENT_ROLE_LABEL,
  type Recipient,
  type RecipientRole,
  type Resident,
} from "@/lib/domain";

export const Route = createFileRoute("/notifications")({
  head: () => ({ meta: [{ title: "알림 설정 · WIFI-GUARD" }] }),
  component: NotificationsPage,
});

/** 편집 폼 — 서버 RecipientIn/Patch 대응. 채널: SMTP email + ntfy mobile push. */
interface RecipientDraft {
  id?: string;
  name: string;
  role: RecipientRole;
  phone: string;
  email: string;
  emailEnabled: boolean;
  sms: boolean;
  push: boolean;
  ars: boolean;
  ntfyServer: string;
  ntfyTopic: string;
  enabled: boolean;
  residentId: string | null;
}

const DEFAULT_NTFY_SERVER = "https://ntfy.sh";
const randomTopic = () => Math.random().toString(36).substring(2, 10);

function toDraft(r: Recipient): RecipientDraft {
  return {
    id: r.id,
    name: r.name,
    role: r.role,
    phone: r.phone ?? "",
    email: r.email ?? "",
    emailEnabled: r.emailEnabled,
    sms: r.sms,
    push: r.push,
    ars: r.ars,
    ntfyServer: r.ntfyServer ?? DEFAULT_NTFY_SERVER,
    ntfyTopic: r.ntfyTopic ?? "",
    enabled: r.enabled,
    residentId: r.residentId ?? null,
  };
}

function NotificationsPage() {
  const { user } = useAuth();
  const isFacility = user?.service === "FACILITY";
  // 스코핑은 서버가 한다 (FACILITY facility_id / HOME owner_user_id).
  const { data: recipients = [], isLoading } = useRecipients();
  const { data: residents = [] } = useResidents();
  const create = useCreateRecipient();
  const update = useUpdateRecipient();
  const remove = useDeleteRecipient();
  const [editing, setEditing] = useState<RecipientDraft | null>(null);

  const shared = useMemo(() => recipients.filter((r) => !r.residentId), [recipients]);
  const byResident = useMemo(() => {
    const map: Record<string, Recipient[]> = {};
    recipients.forEach((r) => {
      if (r.residentId) (map[r.residentId] ??= []).push(r);
    });
    return map;
  }, [recipients]);

  const blank = (residentId: string | null): RecipientDraft => ({
    name: "",
    role: "FAMILY",
    phone: "",
    email: "",
    emailEnabled: true,
    sms: false,
    push: true,
    ars: false,
    ntfyServer: DEFAULT_NTFY_SERVER,
    ntfyTopic: randomTopic(),
    enabled: true,
    residentId,
  });

  const save = async (d: RecipientDraft) => {
    const body = {
      name: d.name,
      role: d.role,
      phone: d.phone || null,
      email: d.email || null,
      emailEnabled: d.emailEnabled,
      sms: d.sms,
      push: d.push,
      ars: d.ars,
      ntfyServer: d.push ? d.ntfyServer || DEFAULT_NTFY_SERVER : null,
      ntfyTopic: d.push ? d.ntfyTopic || null : null,
      enabled: d.enabled,
      residentId: d.residentId,
    };
    try {
      if (d.id) {
        await update.mutateAsync({
          id: d.id,
          patch: { ...body, clearResident: d.residentId === null },
        });
      } else {
        await create.mutateAsync(body);
      }
      toast.success(`${d.name} 저장됨`);
      setEditing(null);
    } catch (err) {
      toast.error(err instanceof ApiError || err instanceof Error ? err.message : "저장 실패");
    }
  };

  const removeOne = async (r: Recipient) => {
    try {
      await remove.mutateAsync(r.id);
      toast(`${r.name} 삭제됨`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "삭제 실패");
    }
  };

  const testOne = async (r: Recipient) => {
    if (USE_MOCK) return toast("데모 모드에서는 외부 알림을 발송하지 않습니다");
    if (!window.confirm("등록된 수신자에게 테스트 알림을 보낼까요?")) return;
    try {
      const result = await apiFetch<{ channels: { channel: string }[] }>(
        `/recipients/${r.id}/test`,
        { method: "POST" },
      );
      toast.success(`테스트 발송 성공 · ${result.channels.map((item) => item.channel).join(", ")}`);
    } catch (err) {
      toast.error(err instanceof ApiError || err instanceof Error ? err.message : "발송 실패");
    }
  };

  return (
    <div>
      <Header title="알림 설정" />
      <div className="p-6 space-y-6 max-w-6xl">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight mb-1">알림 수신자</h1>
          <p className="text-sm text-muted">
            {isFacility
              ? "입소자별로 낙상 알림을 받을 연락처를 관리합니다."
              : "낙상 알림을 받을 연락처와 전송 방법을 관리합니다."}
          </p>
        </div>

        <section className="mobile-notification-intro mobile-only" aria-label="휴대폰 알림 안내">
          <span className="mobile-record-symbol">
            <BellRing size={24} />
          </span>
          <h2>알림을 놓치지 않도록</h2>
          <p>받은 낙상 알림과 대응 결과를 확인하고, 알림을 받을 수신자를 관리하세요.</p>
          <Link to="/history" className="primary-button">
            받은 알림 보기
          </Link>
          <small>
            잠금화면 푸시는 Android 앱 또는 ntfy 앱에서 수신합니다. 이 웹 화면은 알림 조회·처리
            화면입니다.
          </small>
        </section>

        {!isFacility && (
          <details className="bg-surface border border-border rounded-lg p-4 space-y-2">
            <summary className="text-sm font-semibold cursor-pointer">ntfy 앱 연결 안내</summary>
            <ol className="text-xs text-muted space-y-1 list-decimal list-inside">
              <li>휴대폰 앱 스토어에서 "ntfy"를 검색해 NTFY 앱을 설치합니다.</li>
              <li>아래 "+ 수신자 추가"에서 휴대폰 알림을 켜면 구독 코드가 자동 생성됩니다.</li>
              <li>
                그 코드를 NTFY 앱에서 구독하면, 알림 서비스가 켜진 뒤 낙상 감지 시 푸시를 받습니다.
              </li>
            </ol>
          </details>
        )}

        {isFacility ? (
          <>
            <section>
              <div className="flex justify-between items-center mb-3">
                <SectionTitle>입소자별 알림 수신자</SectionTitle>
                <span className="text-[10px] font-mono text-muted uppercase">
                  {residents.length}명
                </span>
              </div>
              <div className="space-y-4">
                {residents.map((res) => (
                  <ResidentGroup
                    key={res.id}
                    resident={res}
                    recipients={byResident[res.id] ?? []}
                    onAdd={() => setEditing(blank(res.id))}
                    onEdit={(r) => setEditing(toDraft(r))}
                    onDelete={removeOne}
                    onTest={testOne}
                  />
                ))}
                {!isLoading && residents.length === 0 && (
                  <div className="bg-surface border border-border rounded p-6 text-center text-xs text-muted">
                    등록된 입소자가 없습니다. 입소자 관리에서 먼저 추가하세요.
                  </div>
                )}
              </div>
            </section>

            <section>
              <div className="flex justify-between items-center mb-3">
                <SectionTitle>공용 수신자 (전체 낙상 알림)</SectionTitle>
                <button
                  onClick={() => setEditing(blank(null))}
                  className="px-3 py-1.5 bg-primary text-primary-foreground rounded text-[10px] font-mono uppercase font-bold"
                >
                  + 공용 수신자
                </button>
              </div>
              <RecipientTable
                rows={shared}
                onEdit={(r) => setEditing(toDraft(r))}
                onDelete={removeOne}
                onTest={testOne}
                emptyText="공용 수신자가 없습니다. 시설 당직실·관리자 등 전체 알림 대상을 등록하세요."
              />
            </section>
          </>
        ) : (
          <section>
            <div className="flex justify-between items-center mb-3">
              <SectionTitle>알림 수신자</SectionTitle>
              <button
                onClick={() => setEditing(blank(null))}
                className="px-3 py-1.5 bg-primary text-primary-foreground rounded text-[10px] font-mono uppercase font-bold"
              >
                + 수신자 추가
              </button>
            </div>
            <RecipientTable
              rows={recipients}
              onEdit={(r) => setEditing(toDraft(r))}
              onDelete={removeOne}
              onTest={testOne}
              emptyText="등록된 수신자가 없습니다."
            />
          </section>
        )}

        {editing && (
          <EditModal
            draft={editing}
            residents={residents}
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

function ResidentGroup({
  resident,
  recipients,
  onAdd,
  onEdit,
  onDelete,
  onTest,
}: {
  resident: Resident;
  recipients: Recipient[];
  onAdd: () => void;
  onEdit: (r: Recipient) => void;
  onDelete: (r: Recipient) => void;
  onTest: (r: Recipient) => void;
}) {
  return (
    <div className="bg-surface border border-border rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-background/30">
        <div className="flex items-center gap-3">
          <span className="text-[10px] font-mono uppercase text-muted">{resident.room}호</span>
          <span className="text-sm font-semibold">{resident.name}</span>
          <span className="text-[10px] font-mono text-muted">
            {resident.age ?? "—"}세 · 담당 {resident.caregiver ?? "—"}
          </span>
          <span className="text-[9px] font-mono uppercase px-1.5 py-0.5 rounded border border-border text-muted">
            {recipients.length}명
          </span>
        </div>
        <button
          onClick={onAdd}
          className="px-3 py-1 border border-primary/40 text-primary rounded text-[10px] font-mono uppercase hover:bg-primary/10"
        >
          + 수신자 추가
        </button>
      </div>
      <RecipientTable
        rows={recipients}
        onEdit={onEdit}
        onDelete={onDelete}
        onTest={onTest}
        embedded
        emptyText={`${resident.name}님의 수신자가 없습니다. 낙상 알림이 공용 수신자에게만 발송됩니다.`}
      />
    </div>
  );
}

function RecipientTable({
  rows,
  onEdit,
  onDelete,
  onTest,
  emptyText,
  embedded,
}: {
  rows: Recipient[];
  onEdit: (r: Recipient) => void;
  onDelete: (r: Recipient) => void;
  onTest: (r: Recipient) => void;
  emptyText: string;
  embedded?: boolean;
}) {
  const presentation = usePresentation();
  return (
    <div
      className={
        embedded
          ? ""
          : "recipient-table-wrap bg-surface border border-border rounded-lg overflow-hidden"
      }
    >
      <div className="mobile-record-list mobile-only" aria-label="알림 수신자 목록">
        {rows.map((r) => (
          <article key={r.id} className="mobile-record-card">
            <div className="mobile-record-heading">
              <span className="mobile-record-symbol">
                <BellRing size={20} />
              </span>
              <div>
                <small>{RECIPIENT_ROLE_LABEL[r.role]}</small>
                <h2>{presentation.label(r.name, "recipient")}</h2>
              </div>
              <span className={`status-pill ${r.enabled ? "tone-success" : "tone-neutral"}`}>
                {r.enabled ? "사용 중" : "중지됨"}
              </span>
            </div>
            <div className="mobile-recipient-channels">
              {r.emailEnabled && (
                <span>
                  <Mail size={15} />
                  이메일
                </span>
              )}
              {r.push && (
                <span>
                  <Smartphone size={15} />
                  휴대폰
                </span>
              )}
              {r.sms && <span>문자</span>}
              {r.ars && <span>전화</span>}
              {!r.emailEnabled && !r.push && !r.sms && !r.ars && <span>전송 방법 미설정</span>}
            </div>
            {r.email && (
              <p className="mobile-recipient-address">
                {presentation.demo && r.email.endsWith("@example.invalid")
                  ? "이메일 등록"
                  : r.email}
              </p>
            )}
            {r.phone && <p className="mobile-recipient-address">{r.phone}</p>}
            <div className="mobile-record-actions">
              <button className="secondary-button" onClick={() => onEdit(r)}>
                수신 설정
              </button>
              <button className="text-button" onClick={() => onTest(r)}>
                알림 확인
              </button>
              <button className="text-button" onClick={() => onDelete(r)}>
                삭제
              </button>
            </div>
          </article>
        ))}
        {!rows.length && (
          <div className="mobile-empty-card">
            <p>{emptyText}</p>
          </div>
        )}
      </div>
      <table className="desktop-only w-full text-left text-sm">
        <thead>
          <tr className="text-[10px] text-muted border-b border-border bg-background/30 font-mono">
            <th className="p-3 font-medium uppercase">이름</th>
            <th className="p-3 font-medium uppercase">관계</th>
            <th className="p-3 font-medium uppercase">연락처</th>
            <th className="p-3 font-medium uppercase">알림 방법</th>
            <th className="p-3 font-medium uppercase">수신 상태</th>
            <th className="p-3 font-medium uppercase text-right">관리</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((r) => (
            <tr key={r.id} className={`hover:bg-black/5 ${r.enabled ? "" : "opacity-50"}`}>
              <td className="p-3 font-medium">{presentation.label(r.name, "recipient")}</td>
              <td className="p-3 text-sm">{RECIPIENT_ROLE_LABEL[r.role]}</td>
              <td className="p-3 font-mono text-xs text-muted">
                <div>{r.phone || "—"}</div>
                {r.email && (
                  <div className="text-[10px]">
                    {presentation.demo && r.email.endsWith("@example.invalid")
                      ? "이메일 등록"
                      : r.email}
                  </div>
                )}
              </td>
              <td className="p-3">
                <div className="flex gap-1">
                  {r.sms && <Chip>문자</Chip>}
                  {r.emailEnabled && <Chip>이메일</Chip>}
                  {r.push && <Chip>휴대폰</Chip>}
                  {r.ars && <Chip>전화</Chip>}
                </div>
              </td>
              <td className="p-3 text-[10px] font-mono uppercase text-muted">
                {r.enabled ? "사용 중" : "중지됨"}
              </td>
              <td className="p-3 text-right space-x-2">
                <button
                  onClick={() => onTest(r)}
                  className="text-[10px] font-mono uppercase text-muted hover:text-foreground"
                >
                  알림 확인
                </button>
                <button
                  onClick={() => onEdit(r)}
                  className="text-[10px] font-mono uppercase text-muted hover:text-foreground"
                >
                  수정
                </button>
                <button
                  onClick={() => onDelete(r)}
                  className="text-[10px] font-mono uppercase text-primary"
                >
                  삭제
                </button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={6} className="p-4 text-center text-muted text-xs">
                {emptyText}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-[9px] font-mono uppercase px-1.5 py-0.5 rounded border border-border bg-background text-muted">
      {children}
    </span>
  );
}

function EditModal({
  draft,
  residents,
  isFacility,
  busy,
  onClose,
  onSave,
}: {
  draft: RecipientDraft;
  residents: Resident[];
  isFacility: boolean;
  busy: boolean;
  onClose: () => void;
  onSave: (r: RecipientDraft) => void;
}) {
  const [r, setR] = useState<RecipientDraft>(draft);
  return (
    <div className="fixed inset-0 bg-black/70 z-40 flex items-center justify-center p-6">
      <div className="bg-surface border border-border rounded-lg max-w-md w-full">
        <div className="p-4 border-b border-border">
          <h3 className="text-sm font-mono uppercase tracking-widest">수신자 정보</h3>
        </div>
        <div className="p-6 space-y-4">
          {isFacility && (
            <F label="담당 입소자">
              <select
                value={r.residentId ?? ""}
                onChange={(e) => setR({ ...r, residentId: e.target.value || null })}
                className={cls}
              >
                <option value="">공용 (전체 낙상 알림)</option>
                {residents.map((res) => (
                  <option key={res.id} value={res.id}>
                    {res.room}호 · {res.name}
                  </option>
                ))}
              </select>
            </F>
          )}
          <F label="이름">
            <input
              value={r.name}
              onChange={(e) => setR({ ...r, name: e.target.value })}
              className={cls}
            />
          </F>
          <F label="역할">
            <select
              value={r.role}
              onChange={(e) => setR({ ...r, role: e.target.value as RecipientRole })}
              className={cls}
            >
              {RECIPIENT_ROLES.map((role) => (
                <option key={role} value={role}>
                  {RECIPIENT_ROLE_LABEL[role]}
                </option>
              ))}
            </select>
          </F>
          <F label="전화번호">
            <input
              value={r.phone}
              onChange={(e) => setR({ ...r, phone: e.target.value })}
              placeholder="010-0000-0000"
              className={cls}
            />
          </F>
          <F label="이메일">
            <input
              type="email"
              value={r.email}
              onChange={(e) => setR({ ...r, email: e.target.value })}
              placeholder="caregiver@example.com"
              className={cls}
            />
          </F>
          <F label="채널">
            <div className="flex flex-wrap gap-4 text-sm">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={r.emailEnabled}
                  onChange={(e) => setR({ ...r, emailEnabled: e.target.checked })}
                />
                이메일
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={r.push}
                  onChange={(e) => setR({ ...r, push: e.target.checked })}
                />
                휴대폰 알림
              </label>
            </div>
          </F>
          {r.push && (
            <>
              <F label="휴대폰 알림 구독 코드">
                <input
                  value={r.ntfyTopic}
                  onChange={(e) => setR({ ...r, ntfyTopic: e.target.value })}
                  placeholder="추측 불가능한 임의의 문자열"
                  className={cls}
                />
              </F>
              <F label="알림 서버 주소">
                <input
                  value={r.ntfyServer}
                  onChange={(e) => setR({ ...r, ntfyServer: e.target.value })}
                  className={cls}
                />
              </F>
            </>
          )}
          <F label="설정">
            <label className="flex items-center gap-2 text-sm font-mono text-muted">
              <input
                type="checkbox"
                checked={r.enabled}
                onChange={(e) => setR({ ...r, enabled: e.target.checked })}
                className="accent-primary"
              />
              알림 수신 사용
            </label>
          </F>
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
            disabled={
              !r.name ||
              busy ||
              (r.emailEnabled && !r.email.trim()) ||
              (r.push && !r.ntfyTopic.trim())
            }
            className="px-4 py-2 bg-primary text-primary-foreground rounded text-xs font-mono uppercase font-bold disabled:opacity-40"
          >
            저장
          </button>
        </div>
      </div>
    </div>
  );
}

const cls =
  "w-full bg-background border border-border rounded px-2 py-1.5 text-sm font-mono focus:outline-none focus:border-muted";
function F({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] font-mono text-muted uppercase mb-1">{label}</div>
      {children}
    </div>
  );
}
