import type {
  CanonicalEventType,
  DomainEventEnvelope,
  FixtureStep,
  LogicalClockStamp,
  NormalizedFixture,
  SessionAggregate,
  TaskAggregateById,
} from "./contracts.ts";
import {
  createEventId,
  createReportId,
  isRecord,
  mapConfidenceLevel,
  readHelpLevelTransition,
  taskLabelFromThemeTaskKey,
} from "./helpers.ts";

export interface EventAdapterInput {
  fixture: NormalizedFixture;
  step: FixtureStep;
  session: SessionAggregate;
  tasks: TaskAggregateById;
  stamp: LogicalClockStamp;
  nextSeqNo: number;
  correlationId: string;
  causationEventId?: string | null;
}

export interface EventAdapterResult {
  events: DomainEventEnvelope[];
  nextSeqNo: number;
}

export function adaptFixtureStep(
  input: EventAdapterInput,
): EventAdapterResult {
  const eventType = canonicalizeStepType(input.step.type);
  const payloadPrivate = buildPrivatePayload(input, eventType);
  const taskId = extractTaskId(input.step, eventType, input.session, input.tasks);
  const confidenceScore = readConfidenceScore(payloadPrivate);
  const confidenceLevel = readConfidenceLevel(payloadPrivate, confidenceScore);
  const event: DomainEventEnvelope = {
    id: createEventId(input.session.id, input.nextSeqNo),
    session_id: input.session.id,
    seq_no: input.nextSeqNo,
    event_type: eventType,
    producer: mapProducer(eventType),
    task_id: taskId,
    theme_id: input.session.theme_id,
    state_before: null,
    state_after: null,
    caution_level: null,
    confidence_score: confidenceScore,
    confidence_level: confidenceLevel,
    causation_event_id: input.causationEventId ?? null,
    correlation_id: input.correlationId,
    payload_public: buildPublicPayload(
      input.fixture,
      input.step,
      input.tasks,
      eventType,
      payloadPrivate,
    ),
    payload_private: payloadPrivate,
    parent_visible: isParentVisible(eventType),
    occurred_at: input.stamp.occurredAt,
    ingested_at: input.stamp.occurredAt,
  };

  return {
    events: [event],
    nextSeqNo: input.nextSeqNo + 1,
  };
}

function canonicalizeStepType(stepType: FixtureStep["type"]): CanonicalEventType {
  switch (stepType) {
    case "child.intent_recognized":
    case "child.answer_incorrect":
      return "nlu.interpreted";
    default:
      return stepType;
  }
}

function mapProducer(
  eventType: CanonicalEventType,
): DomainEventEnvelope["producer"] {
  switch (eventType) {
    case "session.started":
    case "session.ended":
    case "parent_report.generated":
    case "safety.checked":
      return "system";
    case "nlu.interpreted":
      return "nlu";
    case "child.no_response_timeout":
    case "task.activated":
    case "task.failed":
    case "task.completed":
    case "help.level_changed":
    case "state.transition_applied":
      return "state_engine";
    case "parent.interrupt_requested":
    case "parent.resume_requested":
    case "parent.end_session_requested":
      return "system";
    case "device.signal_received":
    case "child.audio_captured":
      return "device";
    default: {
      const unreachable: never = eventType;
      throw new Error(`Unsupported event type producer mapping: ${unreachable}`);
    }
  }
}

function buildPrivatePayload(
  input: EventAdapterInput,
  eventType: CanonicalEventType,
): Record<string, unknown> | null {
  const payload = { ...input.step.payload };

  switch (input.step.type) {
    case "child.intent_recognized":
      return {
        ...payload,
        intent:
          typeof payload.intent === "string" ? payload.intent : "recognized_intent",
      };
    case "child.answer_incorrect":
      return {
        ...payload,
        intent: typeof payload.intent === "string" ? payload.intent : "wrong_answer",
      };
    case "parent_report.generated":
      return buildParentReportPayload(payload, input.session, input.nextSeqNo);
    default:
      if (eventType === "nlu.interpreted") {
        return {
          ...payload,
          intent:
            typeof payload.intent === "string" ? payload.intent : "recognized_intent",
        };
      }
      return Object.keys(payload).length > 0 ? payload : null;
  }
}

function buildParentReportPayload(
  payload: Record<string, unknown>,
  session: SessionAggregate,
  nextSeqNo: number,
): Record<string, unknown> {
  const fallbackRange = {
    from_seq: 1,
    to_seq: Math.max(nextSeqNo - 1, 1),
  };
  const sourceEventRange = isRecord(payload.source_event_range)
    ? {
        from_seq:
          typeof payload.source_event_range.from_seq === "number"
            ? payload.source_event_range.from_seq
            : fallbackRange.from_seq,
        to_seq:
          typeof payload.source_event_range.to_seq === "number"
            ? payload.source_event_range.to_seq
            : fallbackRange.to_seq,
      }
    : fallbackRange;

  return {
    ...payload,
    report_id:
      typeof payload.report_id === "string"
        ? payload.report_id
        : createReportId(session.id),
    report_version:
      typeof payload.report_version === "number" ? payload.report_version : 1,
    summary_version:
      typeof payload.summary_version === "string" ? payload.summary_version : "v1",
    publish_status:
      typeof payload.publish_status === "string"
        ? payload.publish_status
        : session.status === "ended" && session.end_reason === "completed"
          ? "published"
          : "partial",
    source_event_range: sourceEventRange,
  };
}

function buildPublicPayload(
  fixture: NormalizedFixture,
  step: FixtureStep,
  tasks: TaskAggregateById,
  eventType: CanonicalEventType,
  payloadPrivate: Record<string, unknown> | null,
): Record<string, unknown> | null {
  switch (eventType) {
    case "session.started":
      return {
        display_text: "互动开始",
        theme_code: fixture.themeCode,
      };
    case "nlu.interpreted":
      return {
        display_text:
          step.type === "child.answer_incorrect" ||
            payloadPrivate?.intent === "wrong_answer"
            ? "孩子这一步还没答对"
            : payloadPrivate?.requires_self_report === true
              ? "系统正在等孩子确认是否完成"
              : "孩子给了一个回应",
        intent: payloadPrivate?.intent ?? null,
      };
    case "child.no_response_timeout":
      return {
        display_text: "这一步等待有点久了",
        timeout_sec: payloadPrivate?.timeout_sec ?? null,
        waiting_state: payloadPrivate?.waiting_state ?? null,
      };
    case "help.level_changed": {
      const nextLevel = readHelpLevelTransition(payloadPrivate);
      return {
        display_text:
          nextLevel === "guided_hint"
            ? "系统已给出关键线索"
            : nextLevel === "parent_takeover"
              ? "系统已请求家长接手"
              : nextLevel === "light_nudge"
                ? "系统轻提醒后继续"
                : "系统已切换到提示模式",
        from_level:
          payloadPrivate?.from_level ?? payloadPrivate?.from ?? payloadPrivate?.["from"] ?? null,
        to_level:
          payloadPrivate?.to_level ??
          payloadPrivate?.to ??
          payloadPrivate?.escalation_to ??
          null,
      };
    }
    case "task.activated":
      return {
        display_text: "进入新任务",
        theme_task_key: payloadPrivate?.theme_task_key ?? null,
        parent_label: taskLabelFromThemeTaskKey(
          typeof payloadPrivate?.theme_task_key === "string"
            ? payloadPrivate.theme_task_key
            : undefined,
          typeof payloadPrivate?.task_id === "string"
            ? payloadPrivate.task_id
            : null,
        ),
      };
    case "task.failed": {
      const taskId =
        typeof payloadPrivate?.task_id === "string" ? payloadPrivate.task_id : null;
      const existingTask = taskId ? tasks[taskId] : undefined;
      return {
        display_text: "这个任务暂时没完成",
        result_code: payloadPrivate?.result_code ?? null,
        parent_label: existingTask?.parent_label ?? null,
      };
    }
    case "task.completed": {
      const taskId =
        typeof payloadPrivate?.task_id === "string" ? payloadPrivate.task_id : null;
      const existingTask = taskId ? tasks[taskId] : undefined;
      return {
        display_text: "完成一个任务",
        result_code: payloadPrivate?.result_code ?? null,
        parent_label: existingTask?.parent_label ?? null,
      };
    }
    case "parent.interrupt_requested":
      return {
        display_text: "家长已接管当前会话",
        source: payloadPrivate?.source ?? null,
        reason: payloadPrivate?.reason ?? null,
      };
    case "parent.resume_requested":
      return {
        display_text: "家长已恢复会话",
        source: payloadPrivate?.source ?? null,
      };
    case "parent.end_session_requested":
      return {
        display_text: "家长决定结束本轮会话",
        source: payloadPrivate?.source ?? null,
        reason: payloadPrivate?.reason ?? null,
      };
    case "safety.checked":
      return {
        display_text:
          payloadPrivate?.result === "stop" ? "触发安全停止" : "安全检查已通过",
        result: payloadPrivate?.result ?? null,
      };
    case "session.ended":
      return {
        display_text: "本轮已结束",
        end_reason: payloadPrivate?.end_reason ?? null,
      };
    case "parent_report.generated":
      return {
        display_text: "家长报告已生成",
        report_id: payloadPrivate?.report_id ?? null,
        publish_status: payloadPrivate?.publish_status ?? null,
      };
    case "device.signal_received":
    case "child.audio_captured":
    case "state.transition_applied":
      return null;
    default: {
      const unreachable: never = eventType;
      throw new Error(`Unsupported public payload mapping: ${unreachable}`);
    }
  }
}

function isParentVisible(eventType: CanonicalEventType): boolean {
  switch (eventType) {
    case "session.started":
    case "task.activated":
    case "task.failed":
    case "help.level_changed":
    case "parent.interrupt_requested":
    case "parent.resume_requested":
    case "parent.end_session_requested":
    case "task.completed":
    case "session.ended":
    case "parent_report.generated":
      return true;
    case "nlu.interpreted":
    case "child.no_response_timeout":
    case "safety.checked":
    case "device.signal_received":
    case "child.audio_captured":
    case "state.transition_applied":
      return false;
    default: {
      const unreachable: never = eventType;
      throw new Error(`Unsupported parent visibility mapping: ${unreachable}`);
    }
  }
}

function extractTaskId(
  step: FixtureStep,
  eventType: CanonicalEventType,
  session: SessionAggregate,
  tasks: TaskAggregateById,
): string | null {
  if (
    eventType === "session.started" ||
    eventType === "session.ended" ||
    eventType === "parent_report.generated"
  ) {
    return null;
  }

  if (typeof step.payload.task_id === "string") {
    return step.payload.task_id;
  }

  if (session.current_task_id && tasks[session.current_task_id]) {
    return session.current_task_id;
  }

  const activeTask = Object.values(tasks).find((task) => task.status === "active");
  if (activeTask) {
    return activeTask.id;
  }

  return Object.values(tasks).at(-1)?.id ?? null;
}

function readConfidenceScore(
  payload: Record<string, unknown> | null,
): number | null {
  const score = payload?.confidence_score;
  return typeof score === "number" && Number.isFinite(score) ? score : null;
}

function readConfidenceLevel(
  payload: Record<string, unknown> | null,
  confidenceScore: number | null,
): DomainEventEnvelope["confidence_level"] {
  const rawLevel = payload?.confidence_level;
  if (
    rawLevel === "very_low" ||
    rawLevel === "low" ||
    rawLevel === "medium" ||
    rawLevel === "high" ||
    rawLevel === "very_high"
  ) {
    return rawLevel;
  }

  return mapConfidenceLevel(confidenceScore);
}
