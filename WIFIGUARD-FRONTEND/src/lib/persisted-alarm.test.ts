import { expect, it, vi } from "vitest";
import { reconcileAlarm } from "./persisted-alarm";
import type { FallEvent } from "./domain";
const pending = { id: "saved-id", response: "PENDING" } as FallEvent;
it("does not reopen a handled fall on reconnect", async () => {
  const raise = vi.fn();
  await reconcileAlarm(
    pending,
    async () => ({ ...pending, response: "ACKNOWLEDGED" }),
    raise,
    () => true,
  );
  expect(raise).not.toHaveBeenCalled();
});
it("raises a pending event and preserves alert on read failure", async () => {
  const raise = vi.fn();
  await reconcileAlarm(
    pending,
    async () => pending,
    raise,
    () => true,
  );
  await reconcileAlarm(
    pending,
    async () => {
      throw new Error("read failed");
    },
    raise,
    () => true,
  );
  expect(raise).toHaveBeenCalledTimes(2);
});
it("never leaks an old account alert after a session change", async () => {
  const raise = vi.fn();
  await reconcileAlarm(
    pending,
    async () => pending,
    raise,
    () => false,
  );
  expect(raise).not.toHaveBeenCalled();
});
