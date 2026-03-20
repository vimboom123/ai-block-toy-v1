import { createHash } from "node:crypto";
import type {
  DomainEventEnvelope,
  EndReason,
  SessionAggregate,
  SessionTimelineItem,
  SessionTimelineView,
  TaskAggregateById,
  TimelineSeverity,
} from "../contracts.ts";

const RULE_A_WINDOW_MS = 5_000;
const RULE_B_WINDOW_MS = 30_000;
const RULE_C_WINDOW_MS = 15_000;

const EMITTABLE_TIMELINE_EVENT_TYPES = new Set<DomainEventEnvelope["event_type"]>([
  "session.started",
  "task.activated",
  "help.level_changed",
  "task.completed",
  "task.failed",
  "parent.interrupt_requested",
  "parent.resume_requested",
  "session.ended",
]);

const TIMELINE_CONTEXT_EVENT_TYPES = new Set<DomainEventEnvelope["event_type"]>([
  ...EMITTABLE_TIMELINE_EVENT_TYPES,
  "state.transition_applied",
]);

const HINT_BLOCKING_EVENT_TYPES = new Set<DomainEventEnvelope["event_type"]>([
  "task.completed",
  "task.failed",
  "parent.interrupt_requested",
  "parent.resume_requested",
  "session.ended",
]);

const PAUSE_RESUME_BLOCKING_EVENT_TYPES = new Set<DomainEventEnvelope["event_type"]>([
  "task.failed",
  "task.completed",
  "session.ended",
]);

export interface BuildTimelineInput {
  session: SessionAggregate;
  tasks: TaskAggregateById;
  events: DomainEventEnvelope[];
  generatedAt: string;
}

export function buildSessionTimelineView(
  input: BuildTimelineInput,
): SessionTimelineView {
  const events = sortEvents(input.events);
  const consumedSeqNos = new Set<number>();
  const items: SessionTimelineItem[] = [];
  let suppressHintsUntilResume = false;

  for (let index = 0; index < events.length; index += 1) {
    const event = events[index];
    if (consumedSeqNos.has(event.seq_no)) {
      continue;
    }

    if (event.event_type === "parent.resume_requested") {
      suppressHintsUntilResume = false;
    }

    if (event.event_type === "state.transition_applied") {
      continue;
    }

    if (!isTimelineBaseEvent(event)) {
      continue;
    }

    if (event.event_type === "task.activated") {
      const bucket = collectTaskStartBucket(events, index);
      markConsumed(consumedSeqNos, bucket.sourceEvents.slice(1));
      items.push(buildTimelineItem(input, bucket.sourceEvents, "task_progress", event.occurred_at));
      continue;
    }

    if (event.event_type === "help.level_changed") {
      if (suppressHintsUntilResume) {
        consumedSeqNos.add(event.seq_no);
        continue;
      }

      const bucket = collectHintBucket(events, index);
      markConsumed(consumedSeqNos, bucket.consumedEvents);
      if (bucket.displayType === "paused_for_parent") {
        suppressHintsUntilResume = true;
      }
      items.push(
        buildTimelineItem(
          input,
          bucket.sourceEvents,
          bucket.displayType,
          bucket.occurredAt,
        ),
      );
      continue;
    }

    if (event.event_type === "parent.interrupt_requested") {
      const resumeEvent = findDebouncedResume(events, index);
      if (resumeEvent) {
        consumedSeqNos.add(event.seq_no);
        consumedSeqNos.add(resumeEvent.seq_no);
        continue;
      }
    }

    items.push(
      buildTimelineItem(
        input,
        [event],
        mapSingleEventDisplayType(event, input.session),
        event.occurred_at,
      ),
    );
  }

  const eventsUntil = events
    .filter(isTimelineContextEvent)
    .at(-1)?.occurred_at ?? null;
  const limitedItems = keepSessionStartedAndTail(items, 100);

  return {
    session_id: input.session.id,
    items: limitedItems,
    meta: {
      projection_version: "v1",
      generated_at: input.generatedAt,
      events_until: eventsUntil,
      has_earlier_items: limitedItems.length < items.length,
    },
  };
}

function sortEvents(events: DomainEventEnvelope[]): DomainEventEnvelope[] {
  return [...events].sort((left, right) => {
    const leftTs = Date.parse(left.occurred_at);
    const rightTs = Date.parse(right.occurred_at);
    if (leftTs !== rightTs) {
      return leftTs - rightTs;
    }
    return left.seq_no - right.seq_no;
  });
}

function collectTaskStartBucket(
  events: DomainEventEnvelope[],
  startIndex: number,
): { sourceEvents: DomainEventEnvelope[] } {
  const taskActivated = events[startIndex];
  const sourceEvents = [taskActivated];
  const startTs = Date.parse(taskActivated.occurred_at);

  for (let index = startIndex + 1; index < events.length; index += 1) {
    const event = events[index];
    const deltaMs = Date.parse(event.occurred_at) - startTs;
    if (deltaMs > RULE_A_WINDOW_MS) {
      break;
    }

    if (
      event.event_type === "state.transition_applied" &&
      event.task_id &&
      event.task_id === taskActivated.task_id
    ) {
      sourceEvents.push(event);
      break;
    }
  }

  return { sourceEvents };
}

function collectHintBucket(
  events: DomainEventEnvelope[],
  startIndex: number,
): {
  sourceEvents: DomainEventEnvelope[];
  consumedEvents: DomainEventEnvelope[];
  displayType: SessionTimelineItem["display_type"];
  occurredAt: string;
} {
  const sourceEvents: DomainEventEnvelope[] = [];
  const consumedEvents: DomainEventEnvelope[] = [];
  let lastHint = events[startIndex];
  let takeoverEvent: DomainEventEnvelope | null = isParentTakeoverHint(lastHint)
    ? lastHint
    : null;

  for (let index = startIndex; index < events.length; index += 1) {
    const event = events[index];
    if (event.event_type === "state.transition_applied") {
      continue;
    }

    if (index === startIndex) {
      sourceEvents.push(event);
      consumedEvents.push(event);
      continue;
    }

    if (event.event_type === "help.level_changed") {
      const sameTask = event.task_id === lastHint.task_id;
      const withinWindow =
        Date.parse(event.occurred_at) - Date.parse(lastHint.occurred_at) <=
        RULE_B_WINDOW_MS;
      if (!sameTask || !withinWindow) {
        break;
      }

      consumedEvents.push(event);
      lastHint = event;
      if (isParentTakeoverHint(event) && !takeoverEvent) {
        takeoverEvent = event;
      }
      if (!takeoverEvent || isParentTakeoverHint(event)) {
        sourceEvents.push(event);
      }
      continue;
    }

    if (HINT_BLOCKING_EVENT_TYPES.has(event.event_type)) {
      break;
    }
  }

  return {
    sourceEvents,
    consumedEvents,
    displayType: takeoverEvent ? "paused_for_parent" : "hint_given",
    occurredAt: takeoverEvent?.occurred_at ?? lastHint.occurred_at,
  };
}

function findDebouncedResume(
  events: DomainEventEnvelope[],
  interruptIndex: number,
): DomainEventEnvelope | null {
  const interruptEvent = events[interruptIndex];
  const interruptTs = Date.parse(interruptEvent.occurred_at);

  for (let index = interruptIndex + 1; index < events.length; index += 1) {
    const event = events[index];
    const deltaMs = Date.parse(event.occurred_at) - interruptTs;
    if (deltaMs > RULE_C_WINDOW_MS) {
      return null;
    }

    if (PAUSE_RESUME_BLOCKING_EVENT_TYPES.has(event.event_type)) {
      return null;
    }

    if (event.event_type === "parent.resume_requested") {
      return event;
    }

    if (event.event_type === "parent.interrupt_requested") {
      return null;
    }
  }

  return null;
}

function buildTimelineItem(
  input: BuildTimelineInput,
  sourceEvents: DomainEventEnvelope[],
  displayType: SessionTimelineItem["display_type"],
  occurredAt: string,
): SessionTimelineItem {
  const primaryEvent = sourceEvents[0];
  const relatedTaskId =
    sourceEvents.find((event) => event.task_id)?.task_id ?? null;
  const endReason =
    displayType === "session_ended" || displayType === "safety_alert"
      ? resolveTerminalEndReason(input.session)
      : null;

  return {
    timeline_item_id: buildTimelineItemId(
      input.session.id,
      sourceEvents.map((event) => event.id),
    ),
    occurred_at: occurredAt,
    display_type: displayType,
    display_text: buildDisplayText(displayType, primaryEvent, input.tasks, endReason),
    severity: mapTimelineSeverity(displayType, endReason),
    related_task: relatedTaskId
      ? {
          task_id: relatedTaskId,
          parent_label: input.tasks[relatedTaskId]?.parent_label ?? null,
        }
      : null,
    meta: {
      source_event_count: sourceEvents.length,
      source_event_types: uniqueSourceEventTypes(sourceEvents),
    },
  };
}

function buildTimelineItemId(sessionId: string, eventIds: string[]): string {
  const canonical = [...eventIds].sort().join(",");
  const token = createHash("sha256").update(canonical).digest("hex").slice(0, 12);
  return `tl_${sessionId}_${token}`;
}

function uniqueSourceEventTypes(sourceEvents: DomainEventEnvelope[]): string[] {
  return [...new Set(sourceEvents.map((event) => event.event_type))];
}

function buildDisplayText(
  displayType: SessionTimelineItem["display_type"],
  event: DomainEventEnvelope,
  tasks: TaskAggregateById,
  endReason: EndReason | null,
): string {
  const parentLabel = event.task_id ? tasks[event.task_id]?.parent_label ?? null : null;
  switch (displayType) {
    case "session_started":
      return "这一轮开始了。";
    case "task_progress":
      return parentLabel
        ? `孩子开始尝试“${parentLabel}”。`
        : "孩子开始进行新的一个步骤。";
    case "hint_given":
      return "系统给了一个提示，孩子继续尝试中。";
    case "task_completed":
      return parentLabel
        ? `孩子完成了“${parentLabel}”。`
        : "孩子完成了当前任务。";
    case "task_failed":
      return parentLabel
        ? `“${parentLabel}”暂时没完成，系统准备换个方式继续。`
        : "这个步骤暂时没完成，系统准备换个方式继续。";
    case "paused_for_parent":
      return "这一轮已暂停，等家长处理。";
    case "session_resumed":
      return "这一轮继续了。";
    case "safety_alert":
      return "本轮因安全原因提前结束，建议家长看一下当前情况。";
    case "session_ended":
      switch (endReason) {
        case "parent_interrupted":
          return "这一轮已由家长结束。";
        case "child_quit":
          return "孩子这轮不想继续了，这一轮先结束。";
        case "timeout_no_input":
          return "这轮因为长时间没有继续互动，先结束了。";
        case "network_error":
        case "asr_fail_exhausted":
        case "device_shutdown":
        case "theme_switched":
        case "system_abort":
          return "这一轮提前结束了。";
        default:
          return "这一轮结束了。";
      }
  }
}

function mapTimelineSeverity(
  displayType: SessionTimelineItem["display_type"],
  endReason: EndReason | null,
): TimelineSeverity {
  if (displayType === "safety_alert") {
    return "critical";
  }
  if (displayType === "task_failed" || displayType === "paused_for_parent") {
    return "warning";
  }
  if (displayType === "session_ended") {
    return endReason && endReason !== "completed" ? "warning" : "info";
  }
  return "info";
}

function mapSingleEventDisplayType(
  event: DomainEventEnvelope,
  session: SessionAggregate,
): SessionTimelineItem["display_type"] {
  switch (event.event_type) {
    case "session.started":
      return "session_started";
    case "task.activated":
      return "task_progress";
    case "task.failed":
      return "task_failed";
    case "help.level_changed":
      return isParentTakeoverHint(event) ? "paused_for_parent" : "hint_given";
    case "parent.interrupt_requested":
      return "paused_for_parent";
    case "parent.resume_requested":
      return "session_resumed";
    case "task.completed":
      return "task_completed";
    case "session.ended":
      return resolveTerminalEndReason(session) === "safety_stop"
        ? "safety_alert"
        : "session_ended";
    default:
      return "task_progress";
  }
}

function isTimelineBaseEvent(event: DomainEventEnvelope): boolean {
  if (!EMITTABLE_TIMELINE_EVENT_TYPES.has(event.event_type)) {
    return false;
  }

  return event.parent_visible;
}

function isTimelineContextEvent(event: DomainEventEnvelope): boolean {
  if (!TIMELINE_CONTEXT_EVENT_TYPES.has(event.event_type)) {
    return false;
  }

  if (event.event_type === "state.transition_applied") {
    return typeof event.task_id === "string";
  }

  return event.parent_visible;
}

function isParentTakeoverHint(event: DomainEventEnvelope): boolean {
  return event.payload_private?.to_level === "parent_takeover" ||
    event.payload_private?.to === "parent_takeover";
}

function markConsumed(consumedSeqNos: Set<number>, events: DomainEventEnvelope[]): void {
  events.forEach((event) => consumedSeqNos.add(event.seq_no));
}

function keepSessionStartedAndTail(
  items: SessionTimelineItem[],
  limit: number,
): SessionTimelineItem[] {
  if (items.length <= limit) {
    return items;
  }

  const tail = items.slice(-limit);
  if (tail.some((item) => item.display_type === "session_started")) {
    return tail;
  }

  const sessionStarted = items.find((item) => item.display_type === "session_started");
  if (!sessionStarted) {
    return tail;
  }

  return [sessionStarted, ...items.slice(-(limit - 1))];
}

function resolveTerminalEndReason(session: SessionAggregate): EndReason | null {
  return session.end_reason;
}
