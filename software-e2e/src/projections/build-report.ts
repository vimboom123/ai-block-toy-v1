import type {
  ParentReportAggregate,
  ReportDetailView,
  SessionAggregate,
  TaskAggregateById,
} from "../contracts.ts";

export interface BuildReportInput {
  report: ParentReportAggregate;
  session: SessionAggregate;
  tasks: TaskAggregateById;
  generatedAt: string;
}

export function buildReportDetailView(
  input: BuildReportInput,
): ReportDetailView {
  return {
    report_id: input.report.id,
    summary: {
      theme_name_snapshot: input.report.theme_name_snapshot,
      report_date: input.report.report_date,
      duration_sec: input.report.duration_sec,
      completed_task_count: input.report.completed_task_count,
      task_completion_rate: input.report.task_completion_rate,
      help_level_peak: input.report.help_level_peak,
      confidence_overall: input.report.confidence_overall,
      end_reason: input.session.end_reason,
      publish_status: input.report.publish_status,
    },
    highlights: {
      achievement_tags: input.report.achievement_tags,
      notable_moments: input.report.notable_moments,
    },
    parent_text: {
      parent_summary: input.report.parent_summary ?? "本轮已结束。",
      follow_up_suggestion:
        input.report.follow_up_suggestion ?? "可以让孩子复述刚才完成的关键步骤。",
    },
    safety: {
      safety_notice_level: input.report.safety_notice_level,
    },
    task_breakdown: Object.values(input.tasks).map((task) => ({
      task_id: task.id,
      parent_label: task.parent_label ?? "当前任务",
      status: task.status,
      result_code: task.result_code,
      attempt_count: task.attempt_count,
      help_level_peak: task.help_level_peak,
      parent_note: task.parent_note,
    })),
    meta: {
      projection_version: "v1",
      generated_at: input.generatedAt,
    },
  };
}
