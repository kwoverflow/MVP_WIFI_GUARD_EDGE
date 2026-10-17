import { useLocalEdgeStatus } from "@/api/local-edge";
import { userLabel } from "@/lib/user-presentation";

export function usePresentation() {
  const local = useLocalEdgeStatus();
  const demo =
    import.meta.env.VITE_PC_SIMULATION === "1" ||
    import.meta.env.VITE_USE_MOCK === "1" ||
    local.data?.simulation?.enabled === true;
  return {
    demo,
    label: (value: string, kind: Parameters<typeof userLabel>[1]) => userLabel(value, kind, demo),
  };
}
