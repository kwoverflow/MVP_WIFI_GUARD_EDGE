import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { CheckCircle2, CircleAlert, Database, Gauge, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { apiFetch, USE_MOCK } from "@/api/client";
import { Header } from "./index";

export const Route = createFileRoute("/train")({
  head: () => ({ meta: [{ title: "모델 학습 · CSI-Guard" }] }),
  component: TrainPage,
});

interface TrainingStage {
  id: string;
  label: string;
  status: "passed" | "missing";
  detail: string;
}
interface TrainingStatus {
  checkedAt: string;
  overall: "passed" | "degraded";
  mlflowHealthy: boolean;
  stages: TrainingStage[];
  metrics: {
    featureRecordings: number | null;
    featureWindows: number | null;
    generalizationValidated: boolean | null;
    trainingPeakGpuBytes: number | null;
    finetunePeakGpuBytes: number | null;
    registeredModel: string | null;
    registeredModelVersion: string | null;
    mlflowRunId: string | null;
  };
}

const mockStatus: TrainingStatus = {
  checkedAt: new Date().toISOString(),
  overall: "passed",
  mlflowHealthy: true,
  stages: [
    { id: "dataset", label: "데이터셋", status: "passed", detail: "77 recordings · 15320 windows" },
    { id: "training", label: "전체 학습", status: "passed", detail: "cuda · 1 step" },
    { id: "finetune", label: "파인튜닝", status: "passed", detail: "cuda · 1 step" },
    {
      id: "registry",
      label: "MLflow 등록",
      status: "passed",
      detail: "wifiguard-inhouse-segmentation v2",
    },
  ],
  metrics: {
    featureRecordings: 77,
    featureWindows: 15320,
    generalizationValidated: false,
    trainingPeakGpuBytes: 468119040,
    finetunePeakGpuBytes: 114327040,
    registeredModel: "wifiguard-inhouse-segmentation",
    registeredModelVersion: "2",
    mlflowRunId: "18022a5b0e954246be0827c26b41f506",
  },
};

function TrainPage() {
  const queryClient = useQueryClient();
  const status = useQuery({
    queryKey: ["training-status"],
    queryFn: () =>
      USE_MOCK ? Promise.resolve(mockStatus) : apiFetch<TrainingStatus>("/training/status"),
    refetchInterval: 30_000,
  });
  const validate = useMutation({
    mutationFn: () =>
      USE_MOCK
        ? Promise.resolve({ ...mockStatus, checkedAt: new Date().toISOString() })
        : apiFetch<TrainingStatus>("/training/validate", { method: "POST" }),
    onSuccess: (data) => {
      queryClient.setQueryData(["training-status"], data);
      toast.success(
        data.overall === "passed" ? "MLOps 파이프라인 검증 통과" : "일부 단계 확인 필요",
      );
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "검증 실패"),
  });

  const data = status.data;
  return (
    <div>
      <Header title="모델 학습" />
      <main className="p-6 max-w-6xl space-y-6">
        <section className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            <div className="font-mono text-[10px] uppercase tracking-widest text-muted mb-2">
              MLOps Control
            </div>
            <h1 className="text-2xl font-semibold tracking-tight">학습 파이프라인 운영 현황</h1>
            <p className="text-sm text-muted mt-2 max-w-2xl">
              데이터셋 등록부터 CUDA 학습·파인튜닝·MLflow 모델 등록까지 검증된 산출물을 확인합니다.
              검증 버튼은 현재 아티팩트와 모델 레지스트리 연결을 다시 점검합니다.
            </p>
          </div>
          <button
            type="button"
            onClick={() => validate.mutate()}
            disabled={validate.isPending}
            className="inline-flex items-center justify-center gap-2 rounded bg-primary px-4 py-2 text-xs font-mono font-bold uppercase text-primary-foreground disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${validate.isPending ? "animate-spin" : ""}`} />
            파이프라인 검증
          </button>
        </section>

        {status.isLoading && <Panel>학습 상태를 불러오는 중입니다.</Panel>}
        {status.isError && <Panel tone="error">학습 상태 API에 연결할 수 없습니다.</Panel>}

        {data && (
          <>
            <section className="grid gap-4 md:grid-cols-4">
              {data.stages.map((stage) => (
                <StageCard key={stage.id} stage={stage} />
              ))}
            </section>

            <section className="grid gap-4 lg:grid-cols-3">
              <MetricCard icon={<Database className="h-4 w-4" />} title="학습 데이터">
                <Metric label="레코딩" value={formatNumber(data.metrics.featureRecordings)} />
                <Metric label="윈도우" value={formatNumber(data.metrics.featureWindows)} />
              </MetricCard>
              <MetricCard icon={<Gauge className="h-4 w-4" />} title="GPU 피크 메모리">
                <Metric label="전체 학습" value={formatBytes(data.metrics.trainingPeakGpuBytes)} />
                <Metric label="파인튜닝" value={formatBytes(data.metrics.finetunePeakGpuBytes)} />
              </MetricCard>
              <MetricCard icon={<CheckCircle2 className="h-4 w-4" />} title="배포 후보 모델">
                <Metric label="모델" value={data.metrics.registeredModel ?? "—"} />
                <Metric label="버전" value={data.metrics.registeredModelVersion ?? "—"} />
              </MetricCard>
            </section>

            <section className="bg-surface border border-border rounded-lg p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold">MLflow Registry</div>
                  <div className="text-xs font-mono text-muted mt-1 break-all">
                    run {data.metrics.mlflowRunId ?? "—"}
                  </div>
                </div>
                <StatusPill
                  ok={data.mlflowHealthy}
                  label={data.mlflowHealthy ? "CONNECTED" : "OFFLINE"}
                />
              </div>
            </section>

            {!data.metrics.generalizationValidated && (
              <Panel tone="warning">
                실행 경로는 검증됐지만 학습 데이터와 분리된 독립 검증셋 성능은 아직 확인되지
                않았습니다. 실제 낙상 정확도 판단에는 별도의 일반화 검증이 필요합니다.
              </Panel>
            )}

            <div className="text-[10px] font-mono uppercase text-muted">
              마지막 확인 {new Date(data.checkedAt).toLocaleString("ko-KR")}
            </div>
          </>
        )}
      </main>
    </div>
  );
}

function StageCard({ stage }: { stage: TrainingStage }) {
  const ok = stage.status === "passed";
  return (
    <div className="bg-surface border border-border rounded-lg p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-semibold">{stage.label}</div>
        <StatusPill ok={ok} label={ok ? "PASS" : "MISSING"} />
      </div>
      <div className="mt-4 text-[11px] font-mono text-muted break-words">{stage.detail}</div>
    </div>
  );
}

function MetricCard({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-surface border border-border rounded-lg p-5">
      <div className="flex items-center gap-2 text-sm font-semibold mb-4">
        {icon}
        {title}
      </div>
      <div className="space-y-3">{children}</div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 text-xs">
      <span className="text-muted">{label}</span>
      <span className="font-mono text-right break-all">{value}</span>
    </div>
  );
}

function StatusPill({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`rounded border px-2 py-0.5 text-[9px] font-mono ${ok ? "border-success/30 bg-success/10 text-success" : "border-primary/30 bg-primary/10 text-primary"}`}
    >
      {label}
    </span>
  );
}

function Panel({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: "neutral" | "warning" | "error";
}) {
  const style =
    tone === "neutral"
      ? "border-border text-muted"
      : "border-primary/30 bg-primary/5 text-foreground";
  return (
    <div className={`flex items-start gap-3 rounded-lg border p-4 text-sm ${style}`}>
      {tone !== "neutral" && <CircleAlert className="h-4 w-4 shrink-0 mt-0.5" />}
      {children}
    </div>
  );
}

function formatNumber(value: number | null): string {
  return value == null ? "—" : value.toLocaleString("ko-KR");
}

function formatBytes(value: number | null): string {
  return value == null ? "—" : `${(value / 1024 / 1024).toFixed(1)} MiB`;
}
