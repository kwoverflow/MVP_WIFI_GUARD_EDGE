/** TanStack Query 키 — 무효화 단위. */
export const qk = {
  me: () => ["me"] as const,
  facility: () => ["facility"] as const,
  members: () => ["facility", "members"] as const,
  devices: () => ["devices"] as const,
  device: (id: string) => ["devices", id] as const,
  residents: () => ["residents"] as const,
  resident: (id: string) => ["residents", id] as const,
  falls: (params?: Record<string, unknown>) => ["falls", params ?? {}] as const,
  fall: (id: string) => ["falls", "one", id] as const,
  eventLogs: (params?: Record<string, unknown>) => ["event-logs", params ?? {}] as const,
  recipients: (params?: Record<string, unknown>) => ["recipients", params ?? {}] as const,
  config: () => ["config"] as const,
};
