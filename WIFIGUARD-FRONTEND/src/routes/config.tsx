import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Header, SectionTitle } from "./index";
import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useUpdateConfig } from "@/api/mutations";
import { useConfig } from "@/api/queries";
import type { TenantConfigInput } from "@/lib/domain";
import { useLocalEdgeStatus } from "@/api/local-edge";

export const Route = createFileRoute("/config")({
  head: () => ({ meta: [{ title: "알고리즘 설정 · CSI-Guard" }] }),
  component: ConfigPage,
});

interface FieldMeta {
  key: keyof TenantConfigInput;
  label: string;
  unit: string;
  min: number;
  max: number;
  step: number;
  desc: string;
}

/**
 * 세 축의 임계값 (README '세 축의 임계값 — 절대 섞지 말 것'):
 *  - presenceMvThreshold / wanderRatioThreshold / presenceTimeoutS : 재실(엣지) 파라미터
 *  - threshold : 원시 확률(IDLE/SUSPECT) 임계값. Pi5 A/B 확정 규칙과는 별개
 *  - cooldownSeconds : 백엔드 확정 알림의 다음 쿨다운 길이
 * wanderRatioThreshold 는 baseline 대비 **배율**(1~5)이다. 예전 목업의 절대값(0~1)과 다르다.
 */
const FIELDS: FieldMeta[] = [
  {
    key: "presenceMvThreshold",
    label: "움직임 감지 임계값",
    unit: "",
    min: 0.1,
    max: 10,
    step: 0.05,
    desc: "작은 값일수록 작은 움직임에도 반응합니다.",
  },
  {
    key: "wanderRatioThreshold",
    label: "미세 움직임 감지 기준",
    unit: "×",
    min: 1.0,
    max: 5,
    step: 0.1,
    desc: "빈방 기준 대비 미세 움직임의 변화 배율입니다.",
  },
  {
    key: "threshold",
    label: "낙상 의심 표시 기준",
    unit: "",
    min: 0,
    max: 1,
    step: 0.01,
    desc: "낙상 의심을 표시하는 확률 기준입니다. 최종 낙상 확정 조건은 별도로 적용됩니다.",
  },
  {
    key: "cooldownSeconds",
    label: "낙상 재감지 대기시간",
    unit: "초",
    min: 0,
    max: 60,
    step: 0.5,
    desc: "같은 상황에서 알림이 반복되지 않도록 기다리는 시간입니다.",
  },
  {
    key: "presenceTimeoutS",
    label: "퇴실 판단 대기 시간",
    unit: "초",
    min: 1,
    max: 60,
    step: 1,
    desc: "활동 없음이 이 시간 이상 지속되면 퇴실 판정",
  },
];

function pick(c: TenantConfigInput | Record<string, unknown>): TenantConfigInput {
  return {
    presenceMvThreshold: Number(c.presenceMvThreshold),
    wanderRatioThreshold: Number(c.wanderRatioThreshold),
    presenceTimeoutS: Number(c.presenceTimeoutS),
    threshold: Number(c.threshold),
    cooldownSeconds: Number(c.cooldownSeconds),
  };
}

function ConfigPage() {
  const { user } = useAuth();
  const { data: config, isLoading, isError, error } = useConfig();
  const update = useUpdateConfig();
  const local = useLocalEdgeStatus();
  // 서비스 관리자 = HOME 사용자 본인 또는 FACILITY ROOT. MEMBER 는 읽기 전용 (서버도 403).
  const canEdit = !!user && (user.service === "HOME" || user.role === "ROOT");
  const [draft, setDraft] = useState<TenantConfigInput | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  useEffect(() => {
    if (config && draft === null) setDraft(pick(config));
  }, [config, draft]);

  const serverValues = config ? pick(config) : null;
  const dirty = !!draft && !!serverValues && JSON.stringify(draft) !== JSON.stringify(serverValues);

  const confirmApply = async () => {
    if (!draft) return;
    setConfirmOpen(false);
    try {
      const saved = await update.mutateAsync(draft);
      setDraft(pick(saved));
      toast.success("설정을 저장했습니다", {
        description: "장치에 적용될 때까지 잠시 기다려 주세요.",
      });
    } catch (err) {
      toast.error(
        `적용 실패: ${err instanceof ApiError || err instanceof Error ? err.message : String(err)}`,
      );
    }
  };
  const reset = () => {
    if (serverValues) setDraft(serverValues);
    toast("저장된 값으로 되돌렸습니다");
  };

  return (
    <div>
      <Header title="감지 설정" />
      <div className="p-6 space-y-6 max-w-6xl">
        {!canEdit && user && (
          <div className="bg-primary/10 border border-primary/40 rounded-lg p-4 flex gap-3">
            <div className="text-primary text-xl leading-none">⚠</div>
            <div>
              <div className="text-sm font-semibold text-primary mb-1">
                서비스 관리자 권한이 아닙니다
              </div>
              <p className="text-xs text-foreground/70 leading-relaxed">
                현재 계정{" "}
                <span className="font-mono text-foreground">
                  {user.name} ({user.role})
                </span>{" "}
                은 조회만 가능합니다. 설정 변경은 시설 관리자에게 요청해 주세요.
              </p>
            </div>
          </div>
        )}

        <div className="flex justify-between items-start">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight mb-1">감지 기준</h1>
            <p className="text-sm text-muted">
              {user?.service === "FACILITY" ? "시설" : "이 계정"} 단위로 저장되는 감지 설정입니다.
              {config?.updatedAt &&
                ` 마지막 저장: ${new Date(config.updatedAt).toLocaleString("ko-KR", { hour12: false })}`}
            </p>
            {local.data?.enabled && local.data.desired_revision && (
              <p className="text-xs text-muted mt-2" role="status">
                장치 설정{" "}
                {local.data.runtime_current &&
                local.data.runtime?.pi5_services?.runtime_configuration?.applied_revision ===
                  local.data.desired_revision
                  ? "적용됨"
                  : "적용 대기"}
              </p>
            )}
          </div>
          <div className="flex gap-2">
            <button
              onClick={reset}
              disabled={!dirty}
              className="px-4 py-2 border border-border rounded text-xs font-mono uppercase text-muted hover:text-foreground disabled:opacity-40"
            >
              변경 취소
            </button>
            <button
              onClick={() => setConfirmOpen(true)}
              disabled={!dirty || !canEdit || update.isPending}
              title={canEdit ? undefined : "ROOT 또는 HOME 사용자만 변경할 수 있습니다"}
              className="px-4 py-2 bg-primary text-primary-foreground rounded text-xs font-mono uppercase font-bold disabled:opacity-40"
            >
              설정 적용
            </button>
          </div>
        </div>

        {isError && (
          <div className="bg-surface border border-primary/40 rounded p-4 text-sm text-primary">
            설정을 불러오지 못했습니다: {error instanceof Error ? error.message : String(error)}
          </div>
        )}
        {isLoading && !draft && <div className="text-sm text-muted">설정을 불러오는 중…</div>}

        {draft && (
          <section>
            <SectionTitle>탐지 설정값</SectionTitle>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {FIELDS.map((f) => (
                <ParamRow
                  key={f.key}
                  field={f}
                  value={draft[f.key]}
                  disabled={!canEdit}
                  onChange={(v) => setDraft({ ...draft, [f.key]: v })}
                />
              ))}
            </div>
          </section>
        )}

        <section className="bg-surface border border-border rounded-lg p-4">
          <SectionTitle>설정 저장 안내</SectionTitle>
          <p className="text-xs text-muted">
            설정은 저장 후 연결된 장치에 적용됩니다. 장치 연결이 끊겨 있으면 적용이 대기 상태로
            유지됩니다.
          </p>
        </section>

        {confirmOpen && (
          <ConfirmModal
            onCancel={() => setConfirmOpen(false)}
            onConfirm={() => void confirmApply()}
          />
        )}
      </div>
    </div>
  );
}

function ConfirmModal({ onCancel, onConfirm }: { onCancel: () => void; onConfirm: () => void }) {
  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4"
      onClick={onCancel}
    >
      <div
        className="bg-background border-2 border-primary/60 rounded-lg max-w-md w-full p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 mb-3">
          <div className="text-primary text-2xl">⚠</div>
          <h2 className="text-lg font-semibold text-foreground">감지 설정을 변경할까요?</h2>
        </div>
        <p className="text-sm text-foreground/80 leading-relaxed mb-2">
          감지 기준을 바꾸면{" "}
          <span className="text-primary font-semibold">오탐/미탐률에 직접적인 영향</span>을 줍니다.
        </p>
        <p className="text-xs text-foreground/60 font-mono mb-4">
          계속 진행하려면 [적용]을 눌러주세요.
        </p>
        <div className="flex gap-2 justify-end">
          <button
            onClick={onCancel}
            className="px-4 py-2 border border-border rounded text-xs font-mono uppercase text-foreground/70 hover:text-foreground hover:bg-surface"
          >
            취소
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 bg-primary text-primary-foreground rounded text-xs font-mono uppercase font-bold hover:bg-primary/90"
          >
            적용
          </button>
        </div>
      </div>
    </div>
  );
}

function ParamRow({
  field,
  value,
  onChange,
  disabled,
}: {
  field: FieldMeta;
  value: number;
  onChange: (v: number) => void;
  disabled?: boolean;
}) {
  return (
    <div className={`bg-surface border rounded p-4 border-border ${disabled ? "opacity-70" : ""}`}>
      <div className="flex justify-between items-start mb-2">
        <div>
          <div className="text-sm font-medium">{field.label}</div>
          <div className="text-[10px] text-muted mt-0.5">{field.desc}</div>
        </div>
        <div className="text-right">
          <input
            type="number"
            value={value}
            step={field.step}
            min={field.min}
            max={field.max}
            disabled={disabled}
            onChange={(e) => onChange(Number(e.target.value))}
            className="w-20 bg-background border border-border rounded px-2 py-1 text-xs font-mono text-right focus:outline-none focus:border-muted disabled:opacity-50"
          />
          <div className="text-[9px] text-muted font-mono mt-0.5">{field.unit}</div>
        </div>
      </div>
      <input
        type="range"
        min={field.min}
        max={field.max}
        step={field.step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-primary h-1"
      />
      <div className="flex justify-between text-[9px] text-muted font-mono mt-1">
        <span>{field.min}</span>
        <span>{field.max}</span>
      </div>
    </div>
  );
}
