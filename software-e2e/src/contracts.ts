export type SessionStatus = "active" | "paused" | "ended" | "aborted";

export type PublicStage =
  | "warming_up"
  | "doing_task"
  | "receiving_hint"
  | "celebrating"
  | "cooling_down"
  | "ended";

export type HelpLevel =
  | "none"
  | "light_nudge"
  | "guided_hint"
  | "step_by_step"
  | "demo_mode"
  | "parent_takeover";

export type ConfidenceLevel =
  | "very_low"
  | "low"
  | "medium"
  | "high"
  | "very_high";

export type EndReason =
  | "completed"
  | "child_quit"
  | "timeout_no_input"
  | "network_error"
  | "asr_fail_exhausted"
  | "safety_stop"
  | "parent_interrupted"
  | "device_shutdown"
  | "theme_switched"
  | "system_abort";

export type TaskStatus =
  | "pending"
  | "active"
  | "completed"
  | "failed"
  | "skipped";

export type TaskResultCode =
  | "correct"
  | "completed_with_hint"
  | "demo_followed"
  | "skipped"
  | "failed_confusion"
  | "failed_timeout";

export type CautionLevel = "none" | "low" | "medium" | "high";

export type EventProducer =
  | "device"
  | "asr"
  | "nlu"
  | "state_engine"
  | "llm"
  | "tts"
  | "system";

export type FixtureActor = "system" | "child" | "parent";

export type ReportStatus = "draft" | "published" | "partial" | "withdrawn";

export type DisplayStatus = "active" | "paused" | "ended" | "aborted";

export type CanonicalEventType =
  | "session.started"
  | "task.activated"
  | "device.signal_received"
  | "child.audio_captured"
  | "nlu.interpreted"
  | "safety.checked"
  | "child.no_response_timeout"
  | "help.level_changed"
  | "task.completed"
  | "task.failed"
  | "parent.interrupt_requested"
  | "parent.resume_requested"
  | "parent.end_session_requested"
  | "state.transition_applied"
  | "session.ended"
  | "parent_report.generated";

export type SupportedFixtureStepType =
  | "session.started"
  | "nlu.interpreted"
  | "child.intent_recognized"
  | "child.answer_incorrect"
  | "child.no_response_timeout"
  | "help.level_changed"
  | "task.activated"
  | "task.failed"
  | "task.completed"
  | "parent.interrupt_requested"
  | "parent.resume_requested"
  | "parent.end_session_requested"
  | "safety.checked"
  | "session.ended"
  | "parent_report.generated";

export type TimelineDisplayType =
  | "session_started"
  | "task_progress"
  | "hint_given"
  | "task_completed"
  | "task_failed"
  | "paused_for_parent"
  | "session_resumed"
  | "session_ended"
  | "safety_alert";

export type TimelineSeverity = "info" | "warning" | "critical";

export interface FixtureStepDocument {
  at: string;
  actor: FixtureActor;
  type: string;
  payload?: Record<string, unknown>;
}

export interface FixtureDocument {
  id: string;
  category: string;
  theme_code: string;
  seed_profile?: Record<string, unknown>;
  session_bootstrap: {
    public_stage: PublicStage;
    initial_state: string;
    status?: SessionStatus;
  };
  steps: FixtureStepDocument[];
  expected: Record<string, unknown>;
}

export interface SessionBootstrap {
  publicStage: PublicStage;
  initialState: string;
  status: SessionStatus;
}

export interface FixtureStep {
  step_no: number;
  at: string;
  at_ms: number;
  actor: FixtureActor;
  type: SupportedFixtureStepType;
  payload: Record<string, unknown>;
}

export interface FixtureTerminalExpectation {
  sessionStatus?: SessionStatus;
  publicStage?: PublicStage;
  completedTaskCount?: number;
  retryCount?: number;
  helpLevelPeak?: HelpLevel;
  reportPublishStatus?: ReportStatus;
  endReason?: EndReason;
  safetyNoticeLevel?: CautionLevel;
}

export interface FixtureCheckpointExpectation {
  afterStep: number;
  awaitingChildConfirmation?: boolean;
}

export interface FixtureEventContractExpectation {
  eventType: string;
  producer?: EventProducer;
  payloadPrivateContains?: Record<string, unknown>;
  payloadPrivateRequiredKeys?: string[];
  payloadPublicContains?: Record<string, unknown>;
  payloadPublicRequiredKeys?: string[];
}

export interface FixtureExpected {
  terminal: FixtureTerminalExpectation;
  displayStatusExpectation?: DisplayStatus | "active_then_ended";
  events: {
    mustContain: string[];
    mustNotContain?: string[];
    stateTransitionChain?: string[];
    contracts?: FixtureEventContractExpectation[];
  };
  projections: {
    live?: Record<string, unknown>;
    timeline?: Record<string, unknown>;
    report?: Record<string, unknown>;
    home?: Record<string, unknown>;
  };
  checkpoints?: FixtureCheckpointExpectation[];
}

export interface NormalizedFixture {
  id: string;
  category: string;
  themeCode: string;
  seedProfile: Record<string, unknown>;
  bootstrap: SessionBootstrap;
  steps: FixtureStep[];
  expected: FixtureExpected;
  sourcePath?: string;
}

export interface LogicalClockStamp {
  offsetMs: number;
  occurredAt: string;
}

export interface DomainEventEnvelope {
  id: string;
  session_id: string;
  seq_no: number;
  event_type: CanonicalEventType;
  producer: EventProducer;
  task_id: string | null;
  theme_id: string | null;
  state_before: string | null;
  state_after: string | null;
  caution_level: CautionLevel | null;
  confidence_score: number | null;
  confidence_level: ConfidenceLevel | null;
  causation_event_id: string | null;
  correlation_id: string | null;
  payload_public: Record<string, unknown> | null;
  payload_private: Record<string, unknown> | null;
  parent_visible: boolean;
  occurred_at: string;
  ingested_at: string;
}

export interface SessionAggregate {
  id: string;
  theme_id: string;
  theme_code: string;
  status: SessionStatus;
  current_state: string;
  public_stage: PublicStage;
  current_task_id: string | null;
  help_level_peak: HelpLevel;
  turn_count: number;
  completed_task_count: number;
  retry_count: number;
  last_understanding_confidence: number | null;
  started_at: string | null;
  ended_at: string | null;
  end_reason: EndReason | null;
  parent_summary_short: string | null;
}

export interface TaskAggregate {
  id: string;
  session_id: string;
  theme_task_key: string;
  node_key: string;
  sequence_no: number;
  task_type: string;
  title: string;
  parent_label: string | null;
  status: TaskStatus;
  attempt_count: number;
  max_attempts: number;
  help_level_current: HelpLevel;
  help_level_peak: HelpLevel;
  result_code: TaskResultCode | null;
  activated_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  parent_note: string | null;
}

export type TaskAggregateById = Record<string, TaskAggregate>;

export interface ParentReportAggregate {
  id: string;
  session_id: string;
  report_date: string | null;
  generated_at: string | null;
  theme_name_snapshot: string | null;
  duration_sec: number;
  completed_task_count: number;
  task_completion_rate: number;
  help_level_peak: HelpLevel;
  confidence_overall: ConfidenceLevel;
  achievement_tags: string[];
  notable_moments: string[];
  parent_summary: string | null;
  follow_up_suggestion: string | null;
  safety_notice_level: CautionLevel;
  source_event_from_seq: number | null;
  source_event_to_seq: number | null;
  publish_status: ReportStatus;
}

export interface ProjectionMeta {
  projection_version: "v1";
  generated_at: string;
}

export interface SessionLiveView {
  session_id: string;
  header: {
    public_stage: PublicStage | null;
    public_stage_text: string | null;
    display_status: DisplayStatus;
    started_at: string | null;
    ended_at: string | null;
  };
  progress: {
    turn_count: number;
    completed_task_count: number;
    retry_count: number;
  };
  current_task: {
    parent_label: string | null;
    help_level_current: HelpLevel;
    parent_note: string | null;
    awaiting_child_confirmation: boolean;
  } | null;
  session_summary: {
    parent_summary_short: string | null;
  };
  parent_action: {
    need_parent_intervention: boolean;
    intervention_reason_text: string | null;
    suggested_action_text: string | null;
  };
  meta: ProjectionMeta;
}

export interface SessionTimelineItem {
  timeline_item_id: string;
  occurred_at: string;
  display_type: TimelineDisplayType;
  display_text: string;
  severity: TimelineSeverity;
  related_task: {
    task_id: string;
    parent_label: string | null;
  } | null;
  meta: {
    source_event_count: number;
    source_event_types: string[];
  };
}

export interface SessionTimelineView {
  session_id: string;
  items: SessionTimelineItem[];
  meta: ProjectionMeta & {
    events_until: string | null;
    has_earlier_items: boolean;
  };
}

export interface ReportDetailView {
  report_id: string;
  summary: {
    theme_name_snapshot: string | null;
    report_date: string | null;
    duration_sec: number;
    completed_task_count: number;
    task_completion_rate: number;
    help_level_peak: HelpLevel;
    confidence_overall: ConfidenceLevel;
    end_reason: EndReason | null;
    publish_status: ReportStatus;
  };
  highlights: {
    achievement_tags: string[];
    notable_moments: string[];
  };
  parent_text: {
    parent_summary: string;
    follow_up_suggestion: string;
  };
  safety: {
    safety_notice_level: CautionLevel;
  };
  task_breakdown: Array<{
    task_id: string;
    parent_label: string;
    status: TaskStatus;
    result_code: TaskResultCode | null;
    attempt_count: number;
    help_level_peak: HelpLevel;
    parent_note: string | null;
  }>;
  meta: ProjectionMeta;
}

export interface HomeAlert {
  alert_type: "device_offline" | "safety_stop";
  severity: TimelineSeverity;
  title: string;
  body: string;
  entry_cta_text: string;
}

export interface ContinueEntryCandidate {
  theme_id: string;
  theme_name: string;
  entry_reason_text: string;
  entry_cta_text: string;
}

export interface HomeSnapshotView {
  active_session: {
    session_id: string;
    public_stage: PublicStage | null;
    public_stage_text: string | null;
    display_status: DisplayStatus;
    started_at: string | null;
    completed_task_count: number;
    retry_count: number;
    parent_summary_short: string | null;
    entry_cta_text: string;
  } | null;
  latest_report: {
    report_id: string;
    theme_name_snapshot: string | null;
    report_date: string | null;
    achievement_tags: string[];
    parent_summary: string;
    entry_cta_text: string;
  } | null;
  continue_entry: ContinueEntryCandidate | null;
  alerts: HomeAlert[];
  meta: ProjectionMeta;
}

export interface FixtureRunArtifacts {
  fixture: NormalizedFixture;
  session: SessionAggregate;
  tasks: TaskAggregateById;
  report: ParentReportAggregate | null;
  events: DomainEventEnvelope[];
  stepSnapshots: Array<{
    afterStep: number;
    liveView: SessionLiveView;
  }>;
  displayStatusHistory: DisplayStatus[];
  liveView: SessionLiveView;
  timelineView: SessionTimelineView;
  reportView: ReportDetailView | null;
  homeView: HomeSnapshotView;
}

export interface GoldenAssertionFailure {
  path: string;
  expected: unknown;
  actual: unknown;
  message: string;
}

export interface GoldenAssertionResult {
  ok: boolean;
  failures: GoldenAssertionFailure[];
}
