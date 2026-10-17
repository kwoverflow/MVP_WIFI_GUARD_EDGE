import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { Header, SectionTitle } from "./index";
import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useChangePassword, usePatchAccount } from "@/api/mutations";
import { usePresentation } from "@/components/usePresentation";

export const Route = createFileRoute("/account")({
  head: () => ({ meta: [{ title: "계정 관리 · WIFI-GUARD" }] }),
  component: AccountPage,
});

const PASSWORD_MIN = 8;

function errMsg(err: unknown, fallback: string) {
  return err instanceof ApiError || err instanceof Error ? err.message : fallback;
}

function AccountPage() {
  const { user, facility, logout } = useAuth();
  const patchAccount = usePatchAccount();
  const presentation = usePresentation();
  const changePassword = useChangePassword();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [name, setName] = useState(user?.name ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [cur, setCur] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");

  if (!user) return null;

  const saveProfile = async () => {
    if (!name.trim() || !email.trim()) return toast.error("이름과 이메일은 필수입니다");
    try {
      await patchAccount.mutateAsync({ name: name.trim(), email: email.trim() });
      toast.success("프로필 저장됨");
    } catch (err) {
      toast.error(errMsg(err, "저장 실패"));
    }
  };
  const savePassword = async () => {
    if (pw.length < PASSWORD_MIN) return toast.error(`비밀번호는 ${PASSWORD_MIN}자 이상`);
    if (pw !== pw2) return toast.error("비밀번호 확인이 일치하지 않습니다");
    try {
      await changePassword.mutateAsync({ currentPassword: cur, newPassword: pw });
      setCur("");
      setPw("");
      setPw2("");
      toast.success("비밀번호 변경됨 · 다른 기기의 세션은 만료됩니다");
    } catch (err) {
      toast.error(errMsg(err, "비밀번호 변경 실패"));
    }
  };
  const doLogout = async () => {
    await logout();
    qc.clear();
    navigate({ to: "/login" });
  };

  return (
    <div>
      <Header title="계정 관리" />
      <div className="p-6 space-y-5 max-w-3xl">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight mb-1">내 계정</h1>
          <p className="text-sm text-muted">프로필과 비밀번호를 관리합니다.</p>
        </div>

        <div className="bg-surface border border-border rounded-lg p-5">
          <SectionTitle>계정 정보</SectionTitle>
          <div className="grid grid-cols-2 gap-3 text-xs font-mono">
            <Info label="이름" value={presentation.label(user.name, "account")} />
            <Info
              label="권한"
              value={
                user.role === "ROOT" ? "관리자" : user.role === "MEMBER" ? "시설 직원" : "사용자"
              }
            />
            <Info label="서비스" value={user.service === "HOME" ? "가정" : "시설"} />
            {facility && <Info label="시설" value={facility.name} />}
            {facility && <Info label="초대 코드" value={facility.inviteCode} wide />}
          </div>
        </div>

        <div className="bg-surface border border-border rounded-lg p-5 space-y-3">
          <SectionTitle>프로필</SectionTitle>
          {presentation.demo ? (
            <Info label="이름" value={presentation.label(name, "account")} />
          ) : (
            <Field label="이름" value={name} onChange={setName} />
          )}
          {presentation.demo ? (
            <Info label="계정" value="데모 계정" />
          ) : (
            <Field label="이메일" value={email} onChange={setEmail} />
          )}
          <div className="flex justify-end">
            <button
              onClick={saveProfile}
              disabled={patchAccount.isPending || presentation.demo}
              className="px-4 py-2 bg-primary text-primary-foreground rounded text-xs font-mono uppercase font-bold disabled:opacity-60"
            >
              저장
            </button>
          </div>
        </div>

        <div className="bg-surface border border-border rounded-lg p-5 space-y-3">
          <SectionTitle>비밀번호 변경</SectionTitle>
          <Field label="현재 비밀번호" value={cur} onChange={setCur} type="password" />
          <Field
            label={`새 비밀번호 (${PASSWORD_MIN}자 이상)`}
            value={pw}
            onChange={setPw}
            type="password"
          />
          <Field label="비밀번호 확인" value={pw2} onChange={setPw2} type="password" />
          <div className="flex justify-end">
            <button
              onClick={savePassword}
              disabled={changePassword.isPending}
              className="px-4 py-2 bg-primary text-primary-foreground rounded text-xs font-mono uppercase font-bold disabled:opacity-60"
            >
              비밀번호 변경
            </button>
          </div>
        </div>

        <div className="bg-surface border border-primary/30 rounded-lg p-5 space-y-2">
          <SectionTitle>로그아웃</SectionTitle>
          <p className="text-xs text-muted">현재 기기에서 로그아웃합니다.</p>
          <div className="flex justify-end">
            <button
              onClick={doLogout}
              className="px-4 py-2 border border-primary/50 text-primary rounded text-xs font-mono uppercase font-bold hover:bg-primary/10"
            >
              로그아웃
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
}) {
  return (
    <div>
      <label className="text-[10px] font-mono uppercase text-muted">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full mt-1 bg-background border border-border rounded px-3 py-2 text-sm font-mono"
      />
    </div>
  );
}

function Info({
  label,
  value,
  accent,
  wide,
}: {
  label: string;
  value: string;
  accent?: boolean;
  wide?: boolean;
}) {
  return (
    <div className={`bg-background border border-border rounded p-2 ${wide ? "col-span-2" : ""}`}>
      <div className="text-[9px] uppercase text-muted">{label}</div>
      <div className={`text-xs mt-0.5 truncate ${accent ? "text-primary font-bold" : ""}`}>
        {value}
      </div>
    </div>
  );
}
