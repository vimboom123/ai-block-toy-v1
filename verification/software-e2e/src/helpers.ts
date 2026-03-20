import type {
  CautionLevel,
  ConfidenceLevel,
  DisplayStatus,
  EndReason,
  FixtureActor,
  HelpLevel,
  NormalizedFixture,
  PublicStage,
  SessionAggregate,
  SessionStatus,
  SupportedFixtureStepType,
  TaskAggregate,
} from "./contracts.ts";

const HELP_LEVEL_ORDER: HelpLevel[] = [
  "none",
  "light_nudge",
  "guided_hint",
  "step_by_step",
  "demo_mode",
  "parent_takeover",
];

const PUBLIC_STAGE_TEXT: Record<PublicStage, string> = {
  warming_up: "正在热身进入状态",
  doing_task: "正在完成任务",
  receiving_hint: "正在接收提示",
  celebrating: "刚完成一个小目标",
  cooling_down: "正在收尾",
  ended: "本轮已结束",
};

export const FIXTURE_ACTORS: FixtureActor[] = ["system", "child", "parent"];

export const HAPPY_PATH_SUPPORTED_STEP_TYPES: SupportedFixtureStepType[] = [
  "session.started",
  "nlu.interpreted",
  "child.intent_recognized",
  "child.answer_incorrect",
  "child.no_response_timeout",
  "help.level_changed",
  "task.activated",
  "task.failed",
  "task.completed",
  "parent.interrupt_requested",
  "parent.resume_requested",
  "parent.end_session_requested",
  "safety.checked",
  "session.ended",
  "parent_report.generated",
];

export const RUNNER_BASE_TIME_ISO = "2026-03-17T00:00:00.000Z";

export function parseLogicalOffsetMs(value: string): number {
  const trimmed = value.trim();
  const match = trimmed.match(/^(\d+)(ms|s|m|h)$/);
  if (!match) {
    throw new Error(`Unsupported logical offset: ${value}`);
  }

  const amount = Number(match[1]);
  const unit = match[2];

  switch (unit) {
    case "ms":
      return amount;
    case "s":
      return amount * 1_000;
    case "m":
      return amount * 60_000;
    case "h":
      return amount * 3_600_000;
    default:
      throw new Error(`Unsupported logical offset unit: ${unit}`);
  }
}

export function mapConfidenceLevel(
  score: number | null,
): ConfidenceLevel | null {
  if (score === null || Number.isNaN(score)) {
    return null;
  }

  if (score >= 0.9) {
    return "very_high";
  }
  if (score >= 0.75) {
    return "high";
  }
  if (score >= 0.5) {
    return "medium";
  }
  if (score >= 0.25) {
    return "low";
  }
  return "very_low";
}

export function maxHelpLevel(
  current: HelpLevel,
  incoming: HelpLevel,
): HelpLevel {
  return HELP_LEVEL_ORDER.indexOf(incoming) > HELP_LEVEL_ORDER.indexOf(current)
    ? incoming
    : current;
}

export function publicStageText(
  publicStage: PublicStage | null,
): string | null {
  if (!publicStage) {
    return null;
  }

  return PUBLIC_STAGE_TEXT[publicStage] ?? null;
}

export function deriveDisplayStatus(input: {
  status: SessionStatus;
  publicStage: PublicStage;
  endReason: EndReason | null;
}): DisplayStatus {
  if (input.status === "aborted") {
    return "aborted";
  }
  if (input.publicStage === "ended" || input.status === "ended") {
    return "ended";
  }
  if (input.status === "paused") {
    return "paused";
  }
  return "active";
}

export function buildParentSummaryShort(
  session: SessionAggregate,
  currentTask: TaskAggregate | null,
): string {
  if (currentTask) {
    return `已完成 ${session.completed_task_count} 个任务，当前任务：${
      currentTask.parent_label ?? currentTask.theme_task_key
    }`;
  }

  const stageText = publicStageText(session.public_stage);
  if (stageText) {
    return `已完成 ${session.completed_task_count} 个任务，当前${stageText}`;
  }

  return `已完成 ${session.completed_task_count} 个任务`;
}

export function sanitizeToken(value: string): string {
  return value.replace(/[^a-zA-Z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

export function createSessionId(fixtureId: string): string {
  return `ses_${sanitizeToken(fixtureId)}`;
}

export function createThemeId(themeCode: string): string {
  return `thm_${sanitizeToken(themeCode)}`;
}

export function createReportId(sessionId: string): string {
  return `rpt_${sanitizeToken(sessionId)}`;
}

export function createEventId(sessionId: string, seqNo: number): string {
  return `evt_${sanitizeToken(sessionId)}_${String(seqNo).padStart(4, "0")}`;
}

export function createCorrelationId(
  sessionId: string,
  stepNo: number,
): string {
  return `corr_${sanitizeToken(sessionId)}_${String(stepNo).padStart(3, "0")}`;
}

export function createInitialSessionAggregate(
  fixture: NormalizedFixture,
): SessionAggregate {
  return {
    id: createSessionId(fixture.id),
    theme_id: createThemeId(fixture.themeCode),
    theme_code: fixture.themeCode,
    status: fixture.bootstrap.status,
    current_state: fixture.bootstrap.initialState,
    public_stage: fixture.bootstrap.publicStage,
    current_task_id: null,
    help_level_peak: "none",
    turn_count: 0,
    completed_task_count: 0,
    retry_count: 0,
    last_understanding_confidence: null,
    started_at: null,
    ended_at: null,
    end_reason: null,
    parent_summary_short: null,
  };
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function readString(
  value: unknown,
  path: string,
  options?: { optional?: boolean },
): string {
  if (typeof value === "string") {
    return value;
  }

  if (options?.optional && value === undefined) {
    return "";
  }

  throw new Error(`Expected string at ${path}`);
}

export function readNumber(
  value: unknown,
  path: string,
  options?: { optional?: boolean },
): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (options?.optional && value === undefined) {
    return null;
  }
  throw new Error(`Expected number at ${path}`);
}

export function readBoolean(
  value: unknown,
  path: string,
  options?: { optional?: boolean },
): boolean | null {
  if (typeof value === "boolean") {
    return value;
  }
  if (options?.optional && value === undefined) {
    return null;
  }
  throw new Error(`Expected boolean at ${path}`);
}

export function asStringArray(value: unknown, path: string): string[] {
  if (!Array.isArray(value)) {
    throw new Error(`Expected string[] at ${path}`);
  }

  return value.map((entry, index) => readString(entry, `${path}[${index}]`));
}

export function isAbortedEndReason(reason: EndReason | null): boolean {
  return reason !== null && reason !== "completed";
}

export function humanizeTaskToken(token: string): string {
  const normalized = token.trim();
  const numericMatch = normalized.match(/^task[_-]?(\d+)$/i);
  if (numericMatch) {
    return `任务 ${numericMatch[1]}`;
  }

  return normalized.replace(/_/g, " ");
}

export function taskLabelFromThemeTaskKey(
  themeTaskKey?: string | null,
  fallbackTaskId?: string | null,
): string {
  if (typeof themeTaskKey === "string" && themeTaskKey.trim() !== "") {
    return humanizeTaskToken(themeTaskKey);
  }

  if (typeof fallbackTaskId === "string" && fallbackTaskId.trim() !== "") {
    return humanizeTaskToken(fallbackTaskId);
  }

  return "unknown task";
}

export function readHelpLevelTransition(
  payload: Record<string, unknown> | null | undefined,
): HelpLevel | null {
  if (!payload) {
    return null;
  }

  const candidates = [
    payload.to_level,
    payload.to,
    payload.escalation_to,
  ];

  for (const candidate of candidates) {
    if (
      typeof candidate === "string" &&
      HELP_LEVEL_ORDER.includes(candidate as HelpLevel)
    ) {
      return candidate as HelpLevel;
    }
  }

  return null;
}

export function readSafetyNoticeLevel(
  payload: Record<string, unknown> | null | undefined,
): CautionLevel | null {
  const raw = payload?.safety_notice_level;
  if (
    raw === "none" ||
    raw === "low" ||
    raw === "medium" ||
    raw === "high"
  ) {
    return raw;
  }

  return null;
}

export function maxCautionLevel(
  current: CautionLevel,
  incoming: CautionLevel,
): CautionLevel {
  const order: CautionLevel[] = ["none", "low", "medium", "high"];
  return order.indexOf(incoming) > order.indexOf(current) ? incoming : current;
}

export function isoDatePart(isoDateTime: string | null): string | null {
  return isoDateTime ? isoDateTime.slice(0, 10) : null;
}

export function durationSeconds(
  startedAt: string | null,
  endedAt: string | null,
): number {
  if (!startedAt || !endedAt) {
    return 0;
  }

  const deltaMs = Date.parse(endedAt) - Date.parse(startedAt);
  if (Number.isNaN(deltaMs) || deltaMs < 0) {
    return 0;
  }

  return Math.round(deltaMs / 1_000);
}
