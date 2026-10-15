import { createFileRoute } from "@tanstack/react-router";
import { MonitoringDashboard } from "@/components/MonitoringDashboard";

// Keep existing route imports compatible while sharing the refined header.
export { Header, ResponseBadge, SectionTitle } from "@/components/MonitoringHeader";

export const Route = createFileRoute("/")({
  head: () => ({ meta: [{ title: "실시간 관제 · WIFI-GUARD" }] }),
  component: MonitoringDashboard,
});
