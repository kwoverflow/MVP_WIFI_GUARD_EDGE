import { useQuery } from "@tanstack/react-query";
import { useAuth } from "./auth";
import { apiFetch, USE_MOCK } from "./client";

export interface LocalEdgeStatus {
  enabled: boolean;
  device_id?: string | null;
  agent_available?: boolean;
  agent?: { edge_state?: string; last_error?: string | null };
  runtime_current?: boolean;
  runtime?: {
    inference_paused?: boolean;
    stride_seconds?: number;
    processing_ms?: { p95?: number | null };
    stage_ms?: Record<string, { p95?: number | null }>;
    pi5_services?: {
      inference_gate?: { reason?: string; grace_remaining_seconds?: number };
      runtime_configuration?: { applied_revision?: string | null; last_error?: string | null };
    };
  } | null;
  calibration?: {
    run_id: string;
    phase: string;
    phase_elapsed_seconds?: number;
    error?: string | null;
    presence_mv_threshold?: number | null;
    wander_baseline?: number | null;
  } | null;
  desired_revision?: string | null;
  simulation?: {
    enabled: boolean;
    scenario: "empty" | "motion" | "still" | "disconnect";
    updated_at_unix: number;
    database_engine: string;
    notification: { email_captured: number; push_http_captured: number; real_email_or_phone_delivery: false };
    ingest?: { presence_sink?: { written?: number; errors?: number }; fall_sink?: { written?: number; errors?: number } };
  } | null;
  last_command?: { action?: string; state?: string; error?: string } | null;
  resources?: {
    fresh: boolean;
    complete: boolean;
    errors: Record<string, string>;
    scope: string;
    memory_method?: string;
    groups: Record<string, { cpu_percent_host_basis: number | null; memory_mib: number | null; swap_mib: number | null; io_read_bytes_total: number; io_write_bytes_total: number }>;
    total: { cpu_percent_host_basis: number | null; memory_mib: number | null; swap_mib: number | null };
    system: { ram_available_mib: number; temperature_c: number | null; disk_used_gib: number; disk_free_gib: number };
  } | null;
}

export function setSimulationScenario(scenario: "empty" | "motion" | "still" | "disconnect") {
  return apiFetch("/local-edge/simulation/scenario", { method: "POST", body: { scenario } });
}

export function testSimulationAlert() {
  return apiFetch("/local-edge/simulation/alert-test", { method: "POST", body: { confirmed_test: true } });
}

export function useLocalEdgeStatus() {
  const { status, user } = useAuth();
  return useQuery({
    queryKey: ["local-edge-status", user?.id],
    queryFn: () => apiFetch<LocalEdgeStatus>("/local-edge/status"),
    enabled: status === "authed" && !USE_MOCK,
    refetchInterval: 2000,
    refetchIntervalInBackground: false,
    retry: false,
  });
}

export function localEdgeCommand(deviceId: string, action: "start" | "stop" | "recalibrate" | "confirm-empty-room", runId?: string) {
  return apiFetch<{ id: string; state: string; action: string }>("/local-edge/commands", {
    method: "POST",
    body: { device_id: deviceId, action, run_id: runId, empty_room_confirmed: action === "confirm-empty-room" },
  });
}

export function bindLocalEdge(deviceId: string) {
  return apiFetch("/local-edge/bind", { method: "POST", body: { device_id: deviceId } });
}
