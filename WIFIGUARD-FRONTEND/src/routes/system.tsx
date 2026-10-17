import { createFileRoute } from "@tanstack/react-router";
import { Header } from "@/components/MonitoringHeader";
import { Pi5LocalPanel } from "@/components/Pi5LocalPanel";
import { PcSimulationPanel } from "@/components/PcSimulationPanel";
import { DiagnosticFallControl } from "@/components/DiagnosticFallControl";
import { EventLogPanel } from "@/components/EventLogPanel";
import { useResidents } from "@/api/queries";

export const Route = createFileRoute("/system")({
  head: () => ({ meta: [{ title: "시스템 관리 · WIFI-GUARD" }] }),
  component: SystemPage,
});

function SystemPage() {
  const { data: residents = [] } = useResidents();
  return (
    <div>
      <Header title="시스템 관리" />
      <div className="p-6 space-y-5 max-w-6xl">
        <Pi5LocalPanel compact />
        <details className="technical-disclosure">
          <summary>고급 진단</summary>
          <div className="technical-content">
            <PcSimulationPanel />
            <DiagnosticFallControl residentId={residents[0]?.id} />
            <Pi5LocalPanel controls={false} />
            <EventLogPanel />
          </div>
        </details>
      </div>
    </div>
  );
}
