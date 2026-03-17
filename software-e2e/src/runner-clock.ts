import type { LogicalClockStamp } from "./contracts.ts";
import { parseLogicalOffsetMs } from "./helpers.ts";

export interface CreateRunnerClockInput {
  sessionId: string;
  baseTimeIso: string;
}

export interface RunnerClock {
  now(): LogicalClockStamp;
  advanceTo(at: string): LogicalClockStamp;
  nextEventStamp(): LogicalClockStamp;
}

export function createRunnerClock(
  input: CreateRunnerClockInput,
): RunnerClock {
  const baseMs = Date.parse(input.baseTimeIso);
  if (Number.isNaN(baseMs)) {
    throw new Error(`Invalid runner clock base time: ${input.baseTimeIso}`);
  }

  let currentOffsetMs = 0;

  const stampAtOffset = (offsetMs: number): LogicalClockStamp => ({
    offsetMs,
    occurredAt: new Date(baseMs + offsetMs).toISOString(),
  });

  return {
    now() {
      return stampAtOffset(currentOffsetMs);
    },
    advanceTo(at: string) {
      const targetOffsetMs = parseLogicalOffsetMs(at);
      if (targetOffsetMs < currentOffsetMs) {
        throw new Error(
          `Logical clock cannot move backwards: ${at} < ${currentOffsetMs}ms`,
        );
      }

      currentOffsetMs = targetOffsetMs;
      return stampAtOffset(currentOffsetMs);
    },
    nextEventStamp() {
      return stampAtOffset(currentOffsetMs);
    },
  };
}
