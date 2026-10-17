import type { FallEvent } from "./domain";

/** Reconnects must not re-alert handled DB events. Read failures remain visible. */
export async function reconcileAlarm(
  event: FallEvent,
  read: () => Promise<FallEvent>,
  raise: (value: FallEvent) => void,
  sessionCurrent: () => boolean,
) {
  let value = event;
  try {
    value = await read();
  } catch {
    /* no proof it was handled */
  }
  if (sessionCurrent() && value.response === "PENDING") raise(value);
}
