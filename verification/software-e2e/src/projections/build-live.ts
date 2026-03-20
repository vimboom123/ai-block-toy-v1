import type {
  DomainEventEnvelope,
  SessionAggregate,
  SessionLiveView,
  TaskAggregateById,
} from "../contracts.ts";
import {
  buildParentSummaryShort,
  deriveDisplayStatus,
  publicStageText,
} from "../helpers.ts";

export interface BuildLiveInput {
  session: SessionAggregate;
  tasks: TaskAggregateById;
  recentVisibleEvents: DomainEventEnvelope[];
  generatedAt: string;
}

export function buildSessionLiveView(
  input: BuildLiveInput,
): SessionLiveView {
  const currentTask =
    input.session.public_stage === "cooling_down" ||
    input.session.public_stage === "ended"
      ? null
      : selectCurrentTask(input.session, input.tasks);

  const recentVisibleEvents = input.recentVisibleEvents.slice(-20);
  const hasRecentParentInterrupt = recentVisibleEvents.some(
    (event) => event.event_type === "parent.interrupt_requested",
  );
  const hasRecentSessionEndedFallback =
    recentVisibleEvents.some((event) => event.event_type === "session.ended") &&
    input.session.status !== "ended" &&
    input.session.end_reason !== "completed";

  const needParentIntervention =
    input.session.status !== "ended" &&
    (
      currentTask?.help_level_current === "parent_takeover" ||
      input.session.status === "paused" ||
      input.session.status === "aborted" ||
      hasRecentParentInterrupt ||
      hasRecentSessionEndedFallback
    );

  const parentAction = buildParentAction(input.session, currentTask?.help_level_current, {
    needParentIntervention,
    hasRecentParentInterrupt,
  });

  return {
    session_id: input.session.id,
    header: {
      public_stage: input.session.public_stage,
      public_stage_text: publicStageText(input.session.public_stage),
      display_status: deriveDisplayStatus({
        status: input.session.status,
        publicStage: input.session.public_stage,
        endReason: input.session.end_reason,
      }),
      started_at: input.session.started_at,
      ended_at: input.session.ended_at,
    },
    progress: {
      turn_count: input.session.turn_count,
      completed_task_count: input.session.completed_task_count,
      retry_count: input.session.retry_count,
    },
    current_task: currentTask
      ? {
          parent_label: currentTask.parent_label,
          help_level_current: currentTask.help_level_current,
          parent_note:
            currentTask.help_level_current === "parent_takeover"
              ? null
              : currentTask.parent_note,
          awaiting_child_confirmation:
            input.session.current_state === "self_report_confirm",
        }
      : null,
    session_summary: {
      parent_summary_short: buildParentSummaryShort(input.session, currentTask),
    },
    parent_action: parentAction,
    meta: {
      projection_version: "v1",
      generated_at: input.generatedAt,
    },
  };
}

function buildParentAction(
  session: SessionAggregate,
  helpLevelCurrent: SessionLiveView["current_task"] extends infer T
    ? T extends { help_level_current: infer H }
      ? H
      : never
    : never,
  flags: { needParentIntervention: boolean; hasRecentParentInterrupt: boolean },
): SessionLiveView["parent_action"] {
  if (!flags.needParentIntervention) {
    return {
      need_parent_intervention: false,
      intervention_reason_text: null,
      suggested_action_text: null,
    };
  }

  if (session.status === "aborted" && session.end_reason === "safety_stop") {
    return {
      need_parent_intervention: true,
      intervention_reason_text: "本轮已因安全原因提前结束。",
      suggested_action_text: "先安抚孩子，再查看报告里的提醒。",
    };
  }

  if (helpLevelCurrent === "parent_takeover") {
    return {
      need_parent_intervention: true,
      intervention_reason_text: "这一环节需要家长接手一下。",
      suggested_action_text: "先到孩子身边看一眼，再决定继续还是结束。",
    };
  }

  if (session.status === "paused" || flags.hasRecentParentInterrupt) {
    return {
      need_parent_intervention: true,
      intervention_reason_text: "当前流程已暂停，等待家长处理。",
      suggested_action_text: "确认孩子状态后，再选择继续。",
    };
  }

  if (
    session.status === "aborted" &&
    [
      "parent_interrupted",
      "network_error",
      "asr_fail_exhausted",
      "device_shutdown",
      "theme_switched",
      "system_abort",
    ].includes(session.end_reason ?? "")
  ) {
    return {
      need_parent_intervention: true,
      intervention_reason_text: "本轮已提前结束，建议家长看一下当前情况。",
      suggested_action_text: "先确认孩子状态，再决定要不要重新开始。",
    };
  }

  return {
    need_parent_intervention: true,
    intervention_reason_text: null,
    suggested_action_text: null,
  };
}

function selectCurrentTask(
  session: SessionAggregate,
  tasks: TaskAggregateById,
) {
  if (session.current_task_id && tasks[session.current_task_id]) {
    return tasks[session.current_task_id];
  }

  return Object.values(tasks).find((task) => task.status === "active") ?? null;
}
