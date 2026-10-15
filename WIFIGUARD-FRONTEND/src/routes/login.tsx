import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { toast } from "sonner";
import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { BrandLogo } from "@/components/BrandLogo";

export const Route = createFileRoute("/login")({
  head: () => ({ meta: [{ title: "로그인 · WIFI-GUARD" }] }),
  component: LoginPage,
});

function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();
  const pc = import.meta.env.VITE_PC_SIMULATION === "1";
  const local = import.meta.env.VITE_PI5_LOCAL === "1";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await login(email, password);
      toast.success("환영합니다");
      navigate({ to: "/" });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "서버에 연결할 수 없습니다");
    } finally {
      setBusy(false);
    }
  };

  const fill = (e: string) => {
    setEmail(e);
    setPassword("demo");
  };

  return (
    <div className="user-login-page min-h-screen flex items-center justify-center p-6">
      <div className="user-login-card w-full max-w-md">
        <div className="mb-8 text-center">
          <span className="login-brand-icon">
            <BrandLogo />
          </span>
          <div className="font-bold text-2xl tracking-tight">WIFI-GUARD</div>
          <p className="text-sm text-muted mt-2">비접촉 돌봄 모니터링</p>
          {(pc || import.meta.env.VITE_USE_MOCK === "1") && (
            <span className="demo-mode-badge mt-3">데모 모드</span>
          )}
        </div>
        <form
          onSubmit={submit}
          className="bg-surface border border-border rounded-xl p-7 space-y-5"
        >
          <div>
            <h1 className="text-xl font-semibold">로그인</h1>
            <p className="text-xs text-muted mt-2">내 공간의 상태와 알림을 확인하세요.</p>
          </div>
          <div>
            <label htmlFor="login-email" className="text-xs text-muted">
              이메일
            </label>
            <input
              id="login-email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              type="email"
              required
              autoComplete="email"
              className="w-full mt-1 bg-background border border-border rounded px-3 py-2 text-sm font-mono focus:outline-none focus:border-muted"
            />
          </div>
          <div>
            <label htmlFor="login-password" className="text-xs text-muted">
              비밀번호
            </label>
            <input
              id="login-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              type="password"
              required
              autoComplete="current-password"
              className="w-full mt-1 bg-background border border-border rounded px-3 py-2 text-sm font-mono focus:outline-none focus:border-muted"
            />
          </div>
          <button
            type="submit"
            disabled={busy}
            className="login-submit w-full py-3 text-white rounded-lg text-sm font-semibold hover:brightness-110 disabled:opacity-60"
          >
            {busy ? "로그인 중…" : "로그인"}
          </button>
          <div className="text-center text-xs text-muted">
            계정이 없으신가요?{" "}
            <Link to="/signup" className="text-primary font-medium">
              회원가입
            </Link>
          </div>
        </form>
        {pc && (
          <div className="mt-4 text-center text-sm">
            <button
              type="button"
              className="text-primary underline"
              onClick={() => {
                setEmail("pi5-demo@example.invalid");
                setPassword("Pi5-Local-Demo-2026!");
              }}
            >
              데모 계정 사용
            </button>
          </div>
        )}
        {!local && import.meta.env.VITE_USE_MOCK === "1" && (
          <div className="mt-4 bg-surface border border-dashed border-border rounded p-3 text-[10px] font-mono text-muted space-y-1">
            <div className="uppercase tracking-widest text-foreground/70">데모 계정 선택</div>
            <button
              type="button"
              onClick={() => fill("root@demo.io")}
              className="block hover:text-foreground"
            >
              시설 관리자
            </button>
            <button
              type="button"
              onClick={() => fill("member@demo.io")}
              className="block hover:text-foreground"
            >
              시설 직원
            </button>
            <button
              type="button"
              onClick={() => fill("home@demo.io")}
              className="block hover:text-foreground"
            >
              가정 사용자
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
