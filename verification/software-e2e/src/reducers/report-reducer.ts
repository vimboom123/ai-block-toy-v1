import type {
  ConfidenceLevel,
  DomainEventEnvelope,
  ParentReportAggregate,
  SessionAggregate,
  TaskAggregate,
  TaskAggregateById,
} from "../contracts.ts";
import {
  createReportId,
  durationSeconds,
  isoDatePart,
  maxCautionLevel,
  readSafetyNoticeLevel,
} from "../helpers.ts";

export function reduceReport(
  report: ParentReportAggregate | null,
  event: DomainEventEnvelope,
  session: SessionAggregate,
  tasks: TaskAggregateById,
): ParentReportAggregate {
  const nextReport =
    report ??
    {
      id: createReportId(session.id),
      session_id: session.id,
      report_date: null,
      generated_at: null,
      theme_name_snapshot: session.theme_code,
      duration_sec: 0,
      completed_task_count: 0,
      task_completion_rate: 0,
      help_level_peak: session.help_level_peak,
      confidence_overall: "high" as const,
      achievement_tags: [],
      notable_moments: [],
      parent_summary: null,
      follow_up_suggestion: null,
      safety_notice_level: "none",
      source_event_from_seq: null,
      source_event_to_seq: null,
      publish_status: "draft",
    };

  if (event.event_type === "nlu.interpreted") {
    let updatedReport = nextReport;

    if (shouldTrackConfidence(event)) {
      updatedReport = {
        ...updatedReport,
        confidence_overall: mergeConfidenceOverall(
          updatedReport.confidence_overall,
          event.confidence_level,
        ),
      };
    }

    if (event.payload_private?.requires_self_report === true) {
      updatedReport = withSourceRange(
        {
          ...updatedReport,
          notable_moments: appendUnique(
            updatedReport.notable_moments,
            "完成前做了一次口头确认",
          ),
        },
        event.seq_no,
      );
    }

    if (event.payload_private?.intent === "confirm_done") {
      updatedReport = withSourceRange(
        {
          ...updatedReport,
          notable_moments: appendUnique(
            updatedReport.notable_moments,
            "孩子自己确认已经完成",
          ),
        },
        event.seq_no,
      );
    }

    return updatedReport;
  }

  if (event.event_type === "child.no_response_timeout") {
    return withSourceRange(
      {
        ...nextReport,
        notable_moments: appendUnique(
          nextReport.notable_moments,
          session.current_state === "self_report_confirm"
            ? "确认时停顿后又在提示下继续"
            : "中途分心后又被拉回",
        ),
      },
      event.seq_no,
    );
  }

  if (event.event_type === "parent.resume_requested") {
    return withSourceRange(
      {
        ...nextReport,
        notable_moments: appendUnique(
          nextReport.notable_moments,
          "家长接手后又顺利回到任务",
        ),
      },
      event.seq_no,
    );
  }

  if (event.event_type === "safety.checked") {
    const incomingLevel =
      readSafetyNoticeLevel(event.payload_private) ??
      (event.payload_private?.result === "stop" ? "high" : null);
    if (!incomingLevel) {
      return nextReport;
    }

    return withSourceRange(
      {
        ...nextReport,
        safety_notice_level: maxCautionLevel(
          nextReport.safety_notice_level,
          incomingLevel,
        ),
      },
      event.seq_no,
    );
  }

  if (event.event_type === "task.completed") {
    const task = event.task_id ? tasks[event.task_id] : null;
    const completedWithHint =
      task?.help_level_peak === "guided_hint" ||
      task?.help_level_peak === "light_nudge" ||
      task?.help_level_peak === "parent_takeover";
    const notableMomentText =
      task?.help_level_peak === "parent_takeover"
        ? "家长介入后恢复并完成一个任务"
        : task?.help_level_peak === "light_nudge"
          ? "中途分心后重新投入并完成一个任务"
          : completedWithHint
            ? "在提示下完成一个任务"
            : "完成一个任务";
    const achievementTag = completedWithHint ? "completed_with_hint" : "task_completed";
    return withSourceRange(
      {
        ...nextReport,
        notable_moments: appendUnique(nextReport.notable_moments, notableMomentText),
        achievement_tags: appendUnique(nextReport.achievement_tags, achievementTag),
        theme_name_snapshot: session.theme_code,
        completed_task_count: session.completed_task_count,
        help_level_peak: session.help_level_peak,
      },
      event.seq_no,
    );
  }

  if (event.event_type === "session.ended") {
    return {
      ...nextReport,
      help_level_peak: session.help_level_peak,
      safety_notice_level:
        session.end_reason === "safety_stop" ? "high" : nextReport.safety_notice_level,
    };
  }

  if (event.event_type === "parent_report.generated") {
    const completedTaskCount = session.completed_task_count;
    const totalTasks = Object.keys(tasks).length || completedTaskCount || 1;
    const requestedPublishStatus =
      typeof event.payload_private?.publish_status === "string"
        ? event.payload_private.publish_status
        : null;
    const sourceEventRange =
      typeof event.payload_private?.source_event_range === "object" &&
        event.payload_private.source_event_range !== null
        ? event.payload_private.source_event_range as {
            from_seq?: unknown;
            to_seq?: unknown;
          }
        : null;
    const partial = requestedPublishStatus
      ? requestedPublishStatus === "partial"
      : session.status !== "ended";
    const publishStatus =
      requestedPublishStatus === "partial" || requestedPublishStatus === "published"
        ? requestedPublishStatus
        : partial
          ? "partial"
          : "published";

    const finalizedReport: ParentReportAggregate = {
      ...nextReport,
      id:
        typeof event.payload_private?.report_id === "string"
          ? event.payload_private.report_id
          : nextReport.id,
      report_date: isoDatePart(event.occurred_at),
      generated_at: event.occurred_at,
      duration_sec: durationSeconds(session.started_at, session.ended_at),
      completed_task_count: completedTaskCount,
      task_completion_rate: completedTaskCount / totalTasks,
      help_level_peak: session.help_level_peak,
      safety_notice_level:
        session.end_reason === "safety_stop" ? "high" : nextReport.safety_notice_level,
      source_event_from_seq:
        typeof sourceEventRange?.from_seq === "number"
          ? sourceEventRange.from_seq
          : (nextReport.source_event_from_seq ?? 1),
      source_event_to_seq:
        typeof sourceEventRange?.to_seq === "number"
          ? sourceEventRange.to_seq
          : event.seq_no,
      publish_status: publishStatus,
    };

    return {
      ...finalizedReport,
      parent_summary: buildParentSummary(finalizedReport, session, tasks, partial),
      follow_up_suggestion: buildFollowUpSuggestion(
        finalizedReport,
        session,
        tasks,
        partial,
      ),
    };
  }

  return nextReport;
}

function appendUnique(values: string[], value: string): string[] {
  return values.includes(value) ? values : [...values, value];
}

function withSourceRange(
  report: ParentReportAggregate,
  seqNo: number,
): ParentReportAggregate {
  return {
    ...report,
    source_event_from_seq: report.source_event_from_seq ?? seqNo,
    source_event_to_seq: seqNo,
  };
}

function shouldTrackConfidence(event: DomainEventEnvelope): boolean {
  if (event.event_type !== "nlu.interpreted" || !event.task_id) {
    return false;
  }

  const intent = typeof event.payload_private?.intent === "string"
    ? event.payload_private.intent
    : null;

  return intent !== "ready_to_start" && intent !== "ready_to_resume";
}

function mergeConfidenceOverall(
  current: ParentReportAggregate["confidence_overall"],
  incoming: ConfidenceLevel | null,
): ParentReportAggregate["confidence_overall"] {
  const normalizedIncoming = normalizeConfidence(incoming);
  if (!normalizedIncoming) {
    return current;
  }

  const normalizedCurrent = normalizeConfidence(current);
  const order: ConfidenceLevel[] = ["very_low", "low", "medium", "high"];
  return order.indexOf(normalizedIncoming) < order.indexOf(normalizedCurrent)
    ? normalizedIncoming
    : normalizedCurrent;
}

function normalizeConfidence(
  value: ConfidenceLevel | null,
): ParentReportAggregate["confidence_overall"] | null {
  if (!value) {
    return null;
  }

  return value === "very_high" ? "high" : value;
}

function buildParentSummary(
  report: ParentReportAggregate,
  session: SessionAggregate,
  tasks: TaskAggregateById,
  partial: boolean,
): string {
  const completedTaskCount = report.completed_task_count;

  if (partial) {
    if (session.end_reason === "safety_stop") {
      return "本轮因安全原因提前结束，以下是目前能确认的部分总结。";
    }

    if (completedTaskCount > 0) {
      return `本轮提前结束前，已经完成了 ${completedTaskCount} 个任务，以下是目前能确认的部分总结。`;
    }

    return "本轮已提前结束，以下是目前能确认的部分总结。";
  }

  if (hasMoment(report, "家长接手后又顺利回到任务")) {
    return `家长介入后恢复，孩子又重新跟上了任务节奏，本轮完成了 ${completedTaskCount} 个任务。`;
  }

  if (
    hasMoment(report, "完成前做了一次口头确认") ||
    hasMoment(report, "孩子自己确认已经完成")
  ) {
    return `这轮在口头确认后完成了 ${completedTaskCount} 个任务。`;
  }

  if (hasMoment(report, "中途分心后重新投入并完成一个任务")) {
    return `本轮完成了 ${completedTaskCount} 个任务，孩子中途分心后重新投入，并把任务继续做完了。`;
  }

  if (hasHintCompletion(tasks)) {
    return `本轮完成了 ${completedTaskCount} 个任务，孩子在提示下把关键步骤做出来了。`;
  }

  return `本轮完成了 ${completedTaskCount} 个任务，整体已结束。`;
}

function buildFollowUpSuggestion(
  report: ParentReportAggregate,
  session: SessionAggregate,
  tasks: TaskAggregateById,
  partial: boolean,
): string {
  if (partial) {
    return "先确认孩子状态，再决定要不要稍后继续。";
  }

  if (
    session.help_level_peak === "parent_takeover" ||
    hasMoment(report, "家长接手后又顺利回到任务")
  ) {
    return "可以和孩子一起回顾，在家长帮一下之后他是怎么继续完成的。";
  }

  if (hasMoment(report, "孩子自己确认已经完成")) {
    return "可以让孩子再说一遍他刚刚是怎么确认完成的。";
  }

  const latestCompletedTask = selectLatestCompletedTask(tasks);
  if (latestCompletedTask?.parent_label) {
    return `可以和孩子一起回顾${latestCompletedTask.parent_label}。`;
  }

  return "可以让孩子复述刚才完成的关键步骤。";
}

function hasMoment(
  report: ParentReportAggregate,
  keyword: string,
): boolean {
  return report.notable_moments.some((moment) => moment.includes(keyword));
}

function hasHintCompletion(tasks: TaskAggregateById): boolean {
  return Object.values(tasks).some((task) =>
    task.status === "completed" &&
    (
      task.help_level_peak === "light_nudge" ||
      task.help_level_peak === "guided_hint" ||
      task.help_level_peak === "step_by_step" ||
      task.help_level_peak === "demo_mode"
    )
  );
}

function selectLatestCompletedTask(
  tasks: TaskAggregateById,
): TaskAggregate | null {
  return Object.values(tasks)
    .filter((task) => task.status === "completed")
    .sort((left, right) => {
      const leftFinishedAt = Date.parse(left.finished_at ?? "");
      const rightFinishedAt = Date.parse(right.finished_at ?? "");

      if (!Number.isNaN(leftFinishedAt) || !Number.isNaN(rightFinishedAt)) {
        if (Number.isNaN(leftFinishedAt)) {
          return 1;
        }
        if (Number.isNaN(rightFinishedAt)) {
          return -1;
        }
        if (leftFinishedAt !== rightFinishedAt) {
          return rightFinishedAt - leftFinishedAt;
        }
      }

      return right.sequence_no - left.sequence_no;
    })
    .at(0) ?? null;
}
