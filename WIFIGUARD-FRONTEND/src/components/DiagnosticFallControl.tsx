import { useState } from "react";
import { toast } from "sonner";
import { useAuth } from "@/api/auth";
import { useSimulateFall } from "@/api/mutations";
import { alarmStore } from "@/lib/alarm-store";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

/** Retain the original server-authorized diagnostic action outside the everyday view. */
export function DiagnosticFallControl({ residentId }: { residentId?: string }) {
  const { features } = useAuth();
  const simulation = useSimulateFall();
  const [confirm, setConfirm] = useState(false);
  if (!features.fallSimulate) return null;
  async function run() {
    try {
      const fall = await simulation.mutateAsync({ residentId: residentId ?? null });
      if (fall) alarmStore.raise(fall);
      toast.warning("[시험 이벤트] 낙상 알람·응답 경로를 확인하세요");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "시험 이벤트 요청 실패");
    }
  }
  return (
    <section className="border border-border rounded-lg p-4">
      <h2 className="text-sm font-semibold">기존 낙상 이벤트 시험</h2>
      <p className="text-xs text-muted mt-2">
        서버가 허용한 계정에만 표시됩니다. 센서나 모델의 실제 낙상 판정이 아닙니다.
      </p>
      <button
        className="secondary-button mt-3"
        disabled={simulation.isPending}
        onClick={() => setConfirm(true)}
      >
        낙상 시뮬레이션
      </button>
      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogTitle>시험 이벤트를 발생시킬까요?</AlertDialogTitle>
          <AlertDialogDescription>
            선택한 대상의 시험 낙상 이벤트를 저장하고 기존 알람·응답 경로를 실행합니다. 이 기능은
            운영 설정에 따라 알림을 발송할 수 있습니다. 수신자와 시험 환경을 확인해 주세요.
          </AlertDialogDescription>
          <AlertDialogFooter>
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction disabled={simulation.isPending} onClick={() => void run()}>
              시험 실행
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
