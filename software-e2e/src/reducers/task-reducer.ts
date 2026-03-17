import type {
  DomainEventEnvelope,
  TaskAggregate,
  TaskAggregateById,
} from "../contracts.ts";
import {
  maxHelpLevel,
  readHelpLevelTransition,
  taskLabelFromThemeTaskKey,
} from "../helpers.ts";

export function reduceTasks(
  tasks: TaskAggregateById,
  event: DomainEventEnvelope,
): TaskAggregateById {
  const nextTasks: TaskAggregateById = {
    ...tasks,
  };

  switch (event.event_type) {
    case "task.activated": {
      if (!event.task_id) {
        throw new Error("task.activated requires task_id");
      }

      const themeTaskKey =
        typeof event.payload_private?.theme_task_key === "string"
          ? event.payload_private.theme_task_key
          : event.task_id ?? "unknown_task";
      const sequenceNo =
        typeof event.payload_private?.sequence_no === "number"
          ? event.payload_private.sequence_no
          : 0;

      nextTasks[event.task_id] = {
        id: event.task_id,
        session_id: event.session_id,
        theme_task_key: themeTaskKey,
        node_key: themeTaskKey,
        sequence_no: sequenceNo,
        task_type: "build",
        title: themeTaskKey,
        parent_label: taskLabelFromThemeTaskKey(themeTaskKey, event.task_id),
        status: "active",
        attempt_count: 0,
        max_attempts: 1,
        help_level_current: "none",
        help_level_peak: "none",
        result_code: null,
        activated_at: event.occurred_at,
        started_at: event.occurred_at,
        finished_at: null,
        parent_note: "当前正在进行这个任务",
      };
      break;
    }
    case "task.failed": {
      if (!event.task_id || !nextTasks[event.task_id]) {
        throw new Error("task.failed requires a previously activated task");
      }

      const task = nextTasks[event.task_id];
      nextTasks[event.task_id] = {
        ...task,
        status: "failed",
        attempt_count:
          typeof event.payload_private?.attempt_count === "number"
            ? event.payload_private.attempt_count
            : Math.max(task.attempt_count + 1, 1),
        result_code:
          typeof event.payload_private?.result_code === "string"
            ? (event.payload_private.result_code as typeof task.result_code)
            : task.result_code,
        finished_at: event.occurred_at,
        parent_note: "这个任务暂时没完成",
      };
      break;
    }
    case "task.completed": {
      if (!event.task_id || !nextTasks[event.task_id]) {
        throw new Error("task.completed requires a previously activated task");
      }

      const task = nextTasks[event.task_id];
      nextTasks[event.task_id] = {
        ...task,
        status: "completed",
        attempt_count:
          typeof event.payload_private?.attempt_count === "number"
            ? event.payload_private.attempt_count
            : Math.max(task.attempt_count, 1),
        result_code:
          typeof event.payload_private?.result_code === "string"
            ? (event.payload_private.result_code as typeof task.result_code)
            : task.result_code,
        finished_at: event.occurred_at,
        parent_note:
          task.help_level_peak === "parent_takeover"
            ? "家长介入后恢复，任务继续完成"
            : task.help_level_peak === "guided_hint"
              ? "系统已给出关键线索，任务完成"
              : task.help_level_peak === "light_nudge"
                ? "系统轻提醒后顺利完成"
                : "已顺利完成这个任务",
      };
      break;
    }
    case "help.level_changed": {
      const targetTask = selectTaskForEvent(nextTasks, event);
      const nextLevel = readHelpLevelTransition(event.payload_private);
      if (targetTask && nextLevel) {
        nextTasks[targetTask.id] = {
          ...targetTask,
          help_level_current: nextLevel,
          help_level_peak: maxHelpLevel(
            targetTask.help_level_peak,
            nextLevel,
          ),
          parent_note:
            nextLevel === "guided_hint"
              ? "系统已给出关键线索"
              : nextLevel === "parent_takeover"
                ? "等待家长介入"
              : nextLevel === "light_nudge"
                ? "系统轻提醒后继续"
                : "系统已切换到提示模式",
        };
      }
      break;
    }
    case "child.no_response_timeout": {
      const targetTask = selectTaskForEvent(nextTasks, event);
      const nextLevel = readHelpLevelTransition(event.payload_private);
      if (targetTask && nextLevel) {
        nextTasks[targetTask.id] = {
          ...targetTask,
          help_level_current: nextLevel,
          help_level_peak: maxHelpLevel(targetTask.help_level_peak, nextLevel),
          parent_note:
            nextLevel === "guided_hint"
              ? "系统已给出关键线索"
              : nextLevel === "light_nudge"
                ? "系统轻提醒后继续"
                : targetTask.parent_note,
        };
      }
      break;
    }
    case "parent.interrupt_requested": {
      const targetTask = selectTaskForEvent(nextTasks, event);
      if (targetTask) {
        nextTasks[targetTask.id] = {
          ...targetTask,
          help_level_current: "parent_takeover",
          help_level_peak: maxHelpLevel(
            targetTask.help_level_peak,
            "parent_takeover",
          ),
          parent_note: "等待家长介入",
        };
      }
      break;
    }
    case "parent.resume_requested": {
      const targetTask = selectTaskForEvent(nextTasks, event);
      if (targetTask) {
        const keepsTerminalStatus =
          targetTask.status === "completed" || targetTask.status === "failed";
        nextTasks[targetTask.id] = {
          ...targetTask,
          status: keepsTerminalStatus ? targetTask.status : "active",
          finished_at: keepsTerminalStatus ? targetTask.finished_at : null,
          help_level_current: "none",
          parent_note: "家长介入后恢复，继续尝试",
        };
      }
      break;
    }
    default:
      // TODO: add failed/skipped/retry branches once non-happy fixtures are
      // enabled for execution.
      break;
  }

  return nextTasks;
}

function selectTaskForEvent(
  tasks: TaskAggregateById,
  event: DomainEventEnvelope,
): TaskAggregate | null {
  if (event.task_id && tasks[event.task_id]) {
    return tasks[event.task_id];
  }

  return (
    Object.values(tasks).find((task) => task.status === "active") ??
    Object.values(tasks).at(-1) ??
    null
  );
}
