import { useQuery } from "@tanstack/react-query";
import { useAuth } from "./auth";
import { apiFetch, USE_MOCK } from "./client";

export interface WifiTransaction {
  id: string;
  phase: "connecting" | "awaiting_confirmation" | "committing" | "committed" | "rolling_back" | "rolled_back";
  deadline: number;
  error?: string | null;
}
export interface WifiNetwork {
  ssid: string;
  signal: number;
  security: "wpa-psk" | "sae" | "unsupported";
}
export interface WifiStatus {
  enabled: boolean;
  available: boolean;
  secure_credentials_transport: boolean;
  interfaces: { name: string; addresses: string[]; connected: boolean }[];
  ssid: string | null;
  autoconnect: boolean;
  edge_stopped: boolean;
  updated_at_unix: number | null;
  transaction: WifiTransaction | null;
}
export function useWifiStatus() {
  const { status, user } = useAuth();
  return useQuery({
    queryKey: ["pi5-wifi", user?.id],
    queryFn: () => apiFetch<WifiStatus>("/local-edge/network/status"),
    enabled: status === "authed" && !USE_MOCK && (user?.service === "HOME" || user?.role === "ROOT"),
    refetchInterval: 3000,
    refetchIntervalInBackground: false,
    retry: false,
  });
}
export const scanWifi = () => apiFetch<{ networks: WifiNetwork[] }>("/local-edge/network/scan", { method: "POST", timeoutMs: 45000 });
export const connectWifi = (body: { ssid: string; password: string; security: "wpa-psk" | "sae"; hidden: boolean }) =>
  apiFetch<{ transaction: WifiTransaction }>("/local-edge/network/connect", { method: "POST", body, timeoutMs: 45000 });
export const finishWifi = (action: "confirm" | "rollback", id: string) =>
  apiFetch<{ transaction: WifiTransaction }>(`/local-edge/network/${action}`, { method: "POST", body: { id }, timeoutMs: 45000 });
