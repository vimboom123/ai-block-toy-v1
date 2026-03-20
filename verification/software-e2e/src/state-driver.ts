import type {
  DomainEventEnvelope,
  SessionAggregate,
  TaskAggregateById,
} from "./contracts.ts";
import { readHelpLevelTransition } from "./helpers.ts";

export interface StateTransitionSideEffect {
  type:
    | "emit_event"
    | "activate_task"
    | "complete_task"
    | "fail_task"
    | "publish_report"
    | "pause_session"
    | "abort_session";
  payload: Record<string, unknown>;
}

export interface StateTransitionResult {
  nextState: string;
  transitionMetadata: {
    ruleId: string | null;
    reason: string | null;
  };
  sideEffects: StateTransitionSideEffect[];
}

export interface ApplyStateTransitionInput {
  currentState: string;
  event: DomainEventEnvelope;
  session: SessionAggregate;
  tasks: TaskAggregateById;
}

export function applyStateTransition(
  input: ApplyStateTransitionInput,
): StateTransitionResult {
  switch (input.event.event_type) {
    case "session.started":
      return {
        nextState: input.currentState,
        transitionMetadata: {
          ruleId: "rule_bootstrap_session_started",
          reason: "session_bootstrap",
        },
        sideEffects: [],
      };
    case "nlu.interpreted": {
      const intent = String(input.event.payload_private?.intent ?? "nlu_interpreted");
      if (input.event.payload_private?.requires_self_report === true) {
        return {
          nextState: "self_report_confirm",
          transitionMetadata: {
            ruleId: "rule_candidate_complete_requires_self_report",
            reason: intent,
          },
          sideEffects: [],
        };
      }

      if (intent === "ready_to_start" || intent === "ready_to_resume") {
        return {
          nextState: "ready_for_task",
          transitionMetadata: {
            ruleId: "rule_child_ready_to_start",
            reason: intent,
          },
          sideEffects: [],
        };
      }

      return {
        nextState: input.currentState,
        transitionMetadata: {
          ruleId: null,
          reason: null,
        },
        sideEffects: [],
      };
    }
    case "task.activated":
      return {
        nextState: "doing_task",
        transitionMetadata: {
          ruleId: "rule_task_activation_enters_doing_task",
          reason: "task_activated",
        },
        sideEffects: [],
      };
    case "child.no_response_timeout":
      return {
        nextState: "reengagement",
        transitionMetadata: {
          ruleId: "rule_child_no_response_timeout",
          reason: String(input.event.payload_private?.waiting_state ?? "timeout_no_input"),
        },
        sideEffects: [],
      };
    case "help.level_changed":
      {
        const nextLevel = readHelpLevelTransition(input.event.payload_private);
        return {
          nextState:
            nextLevel === "guided_hint"
              ? "receiving_guided_hint"
              : "receiving_hint",
          transitionMetadata: {
            ruleId: "rule_help_level_escalation",
            reason: String(nextLevel ?? "help_level_changed"),
          },
          sideEffects: [],
        };
      }
    case "task.failed":
      return {
        nextState: "task_failed",
        transitionMetadata: {
          ruleId: "rule_task_failed_marks_failure",
          reason: String(input.event.payload_private?.result_code ?? "task_failed"),
        },
        sideEffects: [],
      };
    case "parent.interrupt_requested":
      return {
        nextState: "parent_interrupt_hold",
        transitionMetadata: {
          ruleId: "rule_parent_interrupt_hold",
          reason: String(input.event.payload_private?.reason ?? "parent_interrupt"),
        },
        sideEffects: [],
      };
    case "parent.resume_requested":
      return {
        nextState: "resume_after_parent_interrupt",
        transitionMetadata: {
          ruleId: "rule_parent_resume_session",
          reason: "parent_resume",
        },
        sideEffects: [],
      };
    case "parent.end_session_requested":
      return {
        nextState: "abort_cleanup",
        transitionMetadata: {
          ruleId: "rule_parent_end_session_requested",
          reason: String(input.event.payload_private?.reason ?? "parent_end_session"),
        },
        sideEffects: [],
      };
    case "task.completed":
      return {
        nextState: "celebrating",
        transitionMetadata: {
          ruleId: "rule_task_completion_enters_celebration",
          reason: "task_completed",
        },
        sideEffects: [],
      };
    case "safety.checked":
      return {
        nextState:
          input.event.payload_private?.result === "stop"
            ? "safety_hold"
            : input.currentState,
        transitionMetadata: {
          ruleId:
            input.event.payload_private?.result === "stop"
              ? "rule_safety_stop"
              : null,
          reason: String(input.event.payload_private?.result ?? "safety_checked"),
        },
        sideEffects: [],
      };
    case "session.ended":
      return {
        nextState: "ended",
        transitionMetadata: {
          ruleId: "rule_session_end_terminal",
          reason: String(input.event.payload_private?.end_reason ?? "session_ended"),
        },
        sideEffects: [],
      };
    case "parent_report.generated":
      return {
        nextState: input.currentState,
        transitionMetadata: {
          ruleId: null,
          reason: null,
        },
        sideEffects: [],
      };
    default:
      // TODO: add the remaining documented branches when the first runnable
      // slice expands beyond the happy-path fixture.
      return {
        nextState: input.currentState,
        transitionMetadata: {
          ruleId: null,
          reason: null,
        },
        sideEffects: [],
      };
  }
}
