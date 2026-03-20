import type {
  ContinueEntryCandidate,
  HomeAlert,
  HomeSnapshotView,
  ReportDetailView,
  SessionLiveView,
} from "../contracts.ts";

export interface BuildHomeInput {
  activeSessionLiveView: SessionLiveView | null;
  latestReportDetailView: ReportDetailView | null;
  continueEntry: ContinueEntryCandidate | null;
  alerts: HomeAlert[];
  generatedAt: string;
}

export function buildHomeSnapshotView(
  input: BuildHomeInput,
): HomeSnapshotView {
  const latestReport =
    input.latestReportDetailView?.summary.publish_status === "published"
      ? {
          report_id: input.latestReportDetailView.report_id,
          theme_name_snapshot:
            input.latestReportDetailView.summary.theme_name_snapshot,
          report_date: input.latestReportDetailView.summary.report_date,
          achievement_tags: input.latestReportDetailView.highlights.achievement_tags,
          parent_summary: input.latestReportDetailView.parent_text.parent_summary,
          entry_cta_text: "查看报告",
        }
      : null;

  return {
    active_session:
      input.activeSessionLiveView &&
      input.activeSessionLiveView.header.display_status !== "ended" &&
      input.activeSessionLiveView.header.display_status !== "aborted"
        ? {
            session_id: input.activeSessionLiveView.session_id,
            public_stage: input.activeSessionLiveView.header.public_stage,
            public_stage_text: input.activeSessionLiveView.header.public_stage_text,
            display_status: input.activeSessionLiveView.header.display_status,
            started_at: input.activeSessionLiveView.header.started_at,
            completed_task_count:
              input.activeSessionLiveView.progress.completed_task_count,
            retry_count: input.activeSessionLiveView.progress.retry_count,
            parent_summary_short:
              input.activeSessionLiveView.session_summary.parent_summary_short,
            entry_cta_text: "进入会话",
          }
        : null,
    latest_report: latestReport,
    continue_entry: input.alerts.some((alert) => alert.alert_type === "safety_stop")
      ? null
      : input.continueEntry,
    alerts: input.alerts,
    meta: {
      projection_version: "v1",
      generated_at: input.generatedAt,
    },
  };
}
