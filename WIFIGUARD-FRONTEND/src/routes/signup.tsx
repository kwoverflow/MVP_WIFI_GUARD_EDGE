import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { toast } from "sonner";
import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import type { Service } from "@/lib/domain";
import { BrandLogo } from "@/components/BrandLogo";

export const Route = createFileRoute("/signup")({
  head: () => ({ meta: [{ title: "회원가입 · WIFI-GUARD" }] }),
  component: SignupPage,
});

const PASSWORD_MIN = 8;

function SignupPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [service, setService] = useState<Service>("HOME");
  const [facilityMode, setFacilityMode] = useState<"ROOT" | "MEMBER">("ROOT");
  const [facilityName, setFacilityName] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [busy, setBusy] = useState(false);
  const { signup } = useAuth();
  const navigate = useNavigate();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await signup({
        email,
        password,
        name,
        service,
        facilityMode: service === "FACILITY" ? facilityMode : null,
        facilityName: service === "FACILITY" && facilityMode === "ROOT" ? facilityName : null,
        inviteCode: service === "FACILITY" && facilityMode === "MEMBER" ? inviteCode : null,
      });
      toast.success("가입이 완료되었습니다");
      navigate({ to: "/" });
    } catch (err) {
      if (err instanceof ApiError) {
        const first = err.fields ? Object.values(err.fields)[0] : undefined;
        toast.error(typeof first === "string" ? `${err.message} (${first})` : err.message);
      } else {
        toast.error("서버에 연결할 수 없습니다");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="user-login-page min-h-screen flex items-center justify-center p-6">
      <div className="user-login-card w-full max-w-md">
        <div className="mb-8 text-center">
          <span className="login-brand-icon">
            <BrandLogo />
          </span>
          <div className="font-mono font-bold text-2xl tracking-tighter">WIFI-GUARD</div>
          <p className="text-xs font-mono text-muted uppercase tracking-widest mt-1">회원가입</p>
        </div>
        <form
          onSubmit={submit}
          className="bg-surface border border-border rounded-lg p-6 space-y-4"
        >
          <Field label="이름">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              className={inputCls}
            />
          </Field>
          <Field label="이메일">
            <input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              type="email"
              required
              autoComplete="email"
              className={inputCls}
            />
          </Field>
          <Field label={`비밀번호 (${PASSWORD_MIN}자 이상)`}>
            <input
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              type="password"
              required
              minLength={PASSWORD_MIN}
              autoComplete="new-password"
              className={inputCls}
            />
          </Field>

          <Field label="이용 서비스">
            <div className="grid grid-cols-2 gap-2">
              <ServiceCard
                active={service === "HOME"}
                onClick={() => setService("HOME")}
                title="가정"
                desc="가정 · 1인 거주"
              />
              <ServiceCard
                active={service === "FACILITY"}
                onClick={() => setService("FACILITY")}
                title="시설"
                desc="요양원 · 다중 거주자"
              />
            </div>
          </Field>

          {service === "FACILITY" && (
            <div className="border border-border rounded p-3 space-y-3 bg-background">
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => setFacilityMode("ROOT")}
                  className={`py-2 rounded text-xs font-mono uppercase border ${facilityMode === "ROOT" ? "bg-primary text-primary-foreground border-primary" : "border-border text-muted"}`}
                >
                  시설 등록
                </button>
                <button
                  type="button"
                  onClick={() => setFacilityMode("MEMBER")}
                  className={`py-2 rounded text-xs font-mono uppercase border ${facilityMode === "MEMBER" ? "bg-primary text-primary-foreground border-primary" : "border-border text-muted"}`}
                >
                  시설 참여
                </button>
              </div>
              {facilityMode === "ROOT" ? (
                <Field label="시설명">
                  <input
                    value={facilityName}
                    onChange={(e) => setFacilityName(e.target.value)}
                    placeholder="예: 강남요양원"
                    className={inputCls}
                    required
                  />
                </Field>
              ) : (
                <Field label="초대 코드">
                  <input
                    value={inviteCode}
                    onChange={(e) => setInviteCode(e.target.value.toUpperCase())}
                    placeholder="초대 코드 입력"
                    className={inputCls}
                    required
                  />
                  <p className="text-[10px] font-mono text-muted mt-1">
                    시설 관리자에게 받은 초대 코드를 입력하세요.
                  </p>
                </Field>
              )}
            </div>
          )}

          <button
            type="submit"
            disabled={busy}
            className="w-full py-2.5 bg-primary text-primary-foreground rounded text-xs font-mono uppercase font-bold tracking-widest hover:brightness-110 disabled:opacity-60"
          >
            {busy ? "가입 중…" : "가입하기"}
          </button>
          <div className="text-center text-xs text-muted">
            이미 계정이 있으신가요?{" "}
            <Link to="/login" className="text-primary font-medium">
              로그인
            </Link>
          </div>
        </form>
      </div>
    </div>
  );
}

const inputCls =
  "w-full bg-background border border-border rounded px-3 py-2 text-sm font-mono focus:outline-none focus:border-muted";
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="text-[10px] font-mono uppercase text-muted">{label}</label>
      <div className="mt-1">{children}</div>
    </div>
  );
}
function ServiceCard({
  active,
  onClick,
  title,
  desc,
}: {
  active: boolean;
  onClick: () => void;
  title: string;
  desc: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`text-left p-3 rounded border ${active ? "border-primary bg-primary/5" : "border-border bg-background"}`}
    >
      <div className="text-xs font-mono font-bold">{title}</div>
      <div className="text-[10px] text-muted mt-1">{desc}</div>
    </button>
  );
}
