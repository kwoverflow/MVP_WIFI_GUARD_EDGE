import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";
import { toast } from "sonner";
import { Header, SectionTitle } from "./index";
import { useAuth } from "@/api/auth";
import { useRegenerateInviteCode, useRemoveMember } from "@/api/mutations";
import { useFacility, useFacilityMembers } from "@/api/queries";

export const Route = createFileRoute("/facility-members")({
  head: () => ({ meta: [{ title: "시설 멤버 · CSI-Guard" }] }),
  component: FacilityMembersPage,
});

/** ROOT 전용. UI 가드 + 서버 403 이중화 (프론트 명세 Phase 2). */
function FacilityMembersPage() {
  const { user } = useAuth();
  const { data: facility } = useFacility();
  const { data: members = [], isLoading } = useFacilityMembers();
  const regenerate = useRegenerateInviteCode();
  const remove = useRemoveMember();
  const navigate = useNavigate();

  useEffect(() => {
    if (user && user.role !== "ROOT") navigate({ to: "/" });
  }, [user, navigate]);

  if (!user || !facility) return null;

  const copyCode = async () => {
    try {
      await navigator.clipboard.writeText(facility.inviteCode);
      toast.success("초대 코드 복사됨");
    } catch {
      /* clipboard 미지원 */
    }
  };

  const regen = async () => {
    try {
      await regenerate.mutateAsync();
      toast("코드 재발급됨 · 이전 코드는 더 이상 쓸 수 없습니다");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "재발급 실패");
    }
  };

  const removeOne = async (id: string, name: string) => {
    if (!confirm(`${name} 제거?`)) return;
    try {
      await remove.mutateAsync(id);
      toast(`${name} 제거됨`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "제거 실패");
    }
  };

  return (
    <div>
      <Header title="시설 멤버 관리 (Root)" />
      <div className="p-6 space-y-6 max-w-5xl">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight mb-1">Facility Members</h1>
          <p className="text-sm text-muted">
            시설 등록자(Root)가 요양사 등 멤버를 초대하여 함께 관리할 수 있습니다.
          </p>
        </div>

        <section className="bg-surface border border-border rounded-lg p-5">
          <SectionTitle>Facility</SectionTitle>
          <div className="flex items-end justify-between gap-4">
            <div>
              <div className="text-lg font-semibold">{facility.name}</div>
              <div className="text-[10px] font-mono text-muted">Facility ID: {facility.id}</div>
            </div>
            <div className="text-right">
              <div className="text-[10px] font-mono uppercase text-muted">초대 코드</div>
              <div className="flex items-center gap-2 mt-1">
                <span className="font-mono font-bold text-lg text-primary">
                  {facility.inviteCode}
                </span>
                <button
                  onClick={copyCode}
                  className="text-[10px] font-mono uppercase border border-border rounded px-2 py-1 text-muted hover:text-foreground"
                >
                  복사
                </button>
                <button
                  onClick={regen}
                  disabled={regenerate.isPending}
                  className="text-[10px] font-mono uppercase border border-border rounded px-2 py-1 text-muted hover:text-foreground disabled:opacity-50"
                >
                  재발급
                </button>
              </div>
            </div>
          </div>
        </section>

        <section>
          <SectionTitle>Members ({members.length})</SectionTitle>
          <div className="bg-surface border border-border rounded-lg overflow-hidden">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="text-[10px] text-muted border-b border-border bg-background/30 font-mono">
                  <th className="p-3 uppercase">Name</th>
                  <th className="p-3 uppercase">Email</th>
                  <th className="p-3 uppercase">Role</th>
                  <th className="p-3 uppercase">Onboarded</th>
                  <th className="p-3 uppercase text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {members.map((m) => (
                  <tr key={m.id} className="hover:bg-black/5">
                    <td className="p-3 font-medium">{m.name}</td>
                    <td className="p-3 font-mono text-xs">{m.email}</td>
                    <td className="p-3">
                      <span
                        className={`text-[10px] font-mono uppercase px-2 py-0.5 rounded border ${m.role === "ROOT" ? "text-primary border-primary/30 bg-primary/10" : "text-muted border-border"}`}
                      >
                        {m.role}
                      </span>
                    </td>
                    <td className="p-3 text-xs">{m.onboarded ? "✓" : "—"}</td>
                    <td className="p-3 text-right">
                      {m.role !== "ROOT" && (
                        <button
                          onClick={() => removeOne(m.id, m.name)}
                          disabled={remove.isPending}
                          className="text-[10px] font-mono uppercase text-primary hover:brightness-110 disabled:opacity-50"
                        >
                          제거
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {!isLoading && members.length === 0 && (
                  <tr>
                    <td colSpan={5} className="p-8 text-center text-muted text-xs">
                      멤버가 없습니다.
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
