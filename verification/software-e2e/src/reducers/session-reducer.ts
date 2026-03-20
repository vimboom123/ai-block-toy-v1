import type {
  DomainEventEnvelope,
  SessionAggregate,
  TaskAggregateById,
} from "../contracts.ts";
import {
  buildParentSummaryShort,
  isAbortedEndReason,
  maxHelpLevel,
  readHelpLevelTransition,
} from "../helpers.ts";

export function reduceSession(
  session: SessionAggregate | null,
  event: DomainEventEnvelope,
  tasks: TaskAggregateById,
): SessionAggregate {
  if (!session) {
    throw new Error(
      "Session aggregate must be bootstrapped before reducing events in the first runnable slice",
    );
  }

  const nextSession: SessionAggregate = {
    ...session,
  };

  switch (event.event_type) {
    case "session.started":
      nextSession.status = "active";
      nextSession.started_at = event.occurred_at;
      break;
    case "nlu.interpreted":
      nextSession.turn_count += 1;
      nextSession.last_understanding_confidence = event.confidence_score;
      break;
    case "child.no_response_timeout":
      nextSession.public_stage = "doing_task";
      {
        const nextLevel = readHelpLevelTransition(event.payload_private);
        if (nextLevel) {
          nextSession.help_level_peak = maxHelpLevel(
            nextSession.help_level_peak,
            nextLevel,
          );
        }
      }
      break;
    case "task.activated":
      if (
        nextSession.current_task_id &&
        tasks[nextSession.current_task_id]?.status === "failed"
      ) {
        nextSession.retry_count += 1;
      }
      nextSession.status = "active";
      nextSession.public_stage = "doing_task";
      nextSession.current_task_id = event.task_id;
      break;
    case "task.failed":
      nextSession.current_task_id = event.task_id ?? nextSession.current_task_id;
      nextSession.public_stage = "doing_task";
      break;
    case "parent.interrupt_requested":
      nextSession.status = "paused";
      nextSession.current_task_id = event.task_id ?? nextSession.current_task_id;
      nextSession.help_level_peak = maxHelpLevel(
        nextSession.help_level_peak,
        "parent_takeover",
      );
      break;
    case "parent.resume_requested":
      nextSession.status = "active";
      nextSession.current_task_id = event.task_id ?? nextSession.current_task_id;
      break;
    case "parent.end_session_requested":
      nextSession.status = "paused";
      break;
    case "task.completed":
      nextSession.completed_task_count += 1;
      nextSession.current_task_id = null;
      nextSession.public_stage = "celebrating";
      break;
    case "help.level_changed":
      nextSession.public_stage = "receiving_hint";
      {
        const nextLevel = readHelpLevelTransition(event.payload_private);
        if (nextLevel) {
          nextSession.help_level_peak = maxHelpLevel(
            nextSession.help_level_peak,
            nextLevel,
          );
        }
      }
      break;
    case "session.ended": {
      const endReason =
        typeof event.payload_private?.end_reason === "string"
          ? (event.payload_private.end_reason as SessionAggregate["end_reason"])
          : null;
      nextSession.end_reason = endReason;
      nextSession.status = isAbortedEndReason(endReason) ? "aborted" : "ended";
      nextSession.public_stage = "ended";
      nextSession.current_task_id = null;
      nextSession.ended_at = event.occurred_at;
      break;
    }
    case "state.transition_applied":
      if (typeof event.state_after === "string") {
        nextSession.current_state = event.state_after;
      }
      break;
    default:
      // TODO: materialize the remaining documented branches once more fixtures
      // are supported by this runner slice.
      break;
  }

  nextSession.parent_summary_short = buildParentSummaryShort(nextSession, null);

  return nextSession;
}
