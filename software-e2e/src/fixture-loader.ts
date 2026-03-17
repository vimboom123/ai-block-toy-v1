import type {
  FixtureDocument,
  FixtureEventContractExpectation,
  FixtureExpected,
  FixtureStep,
  NormalizedFixture,
  SupportedFixtureStepType,
} from "./contracts.ts";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import {
  FIXTURE_ACTORS,
  HAPPY_PATH_SUPPORTED_STEP_TYPES,
  asStringArray,
  isRecord,
  parseLogicalOffsetMs,
  readNumber,
  readString,
} from "./helpers.ts";

export interface LoadFixtureInput {
  fixturePath: string;
  strict?: boolean;
}

export async function loadFixture(
  input: LoadFixtureInput,
): Promise<NormalizedFixture> {
  const fixturePath = resolve(input.fixturePath);
  const document = parseYamlFixture(fixturePath);

  validateFixtureDocument(document);

  return normalizeFixtureDocument(document, fixturePath);
}

export function normalizeFixtureDocument(
  document: FixtureDocument,
  sourcePath?: string,
): NormalizedFixture {
  const sortedSteps: FixtureStep[] = document.steps
    .map((step, originalIndex) => ({
      originalIndex,
      normalized: {
        step_no: originalIndex + 1,
        at: step.at,
        at_ms: parseLogicalOffsetMs(step.at),
        actor: step.actor,
        type: step.type as SupportedFixtureStepType,
        payload: isRecord(step.payload) ? { ...step.payload } : {},
      },
    }))
    .sort((left, right) => {
      if (left.normalized.at_ms !== right.normalized.at_ms) {
        return left.normalized.at_ms - right.normalized.at_ms;
      }

      return left.originalIndex - right.originalIndex;
    })
    .map((entry, index) => ({
      ...entry.normalized,
      step_no: index + 1,
    }));

  return {
    id: document.id,
    category: document.category,
    themeCode: document.theme_code,
    seedProfile: isRecord(document.seed_profile)
      ? { ...document.seed_profile }
      : {},
    bootstrap: {
      publicStage: normalizeBootstrapPublicStage(
        document.session_bootstrap.public_stage,
      ),
      initialState: document.session_bootstrap.initial_state,
      status: document.session_bootstrap.status ?? "active",
    },
    steps: sortedSteps,
    expected: normalizeFixtureExpected(document.expected),
    sourcePath,
  };
}

function normalizeBootstrapPublicStage(
  publicStage: FixtureDocument["session_bootstrap"]["public_stage"],
): NormalizedFixture["bootstrap"]["publicStage"] {
  return publicStage === "active" ? "doing_task" : publicStage;
}

export function validateFixtureDocument(document: FixtureDocument): void {
  if (!isRecord(document)) {
    throw new Error("Fixture root must be a mapping");
  }

  readString(document.id, "id");
  readString(document.category, "category");
  readString(document.theme_code, "theme_code");

  if (!isRecord(document.session_bootstrap)) {
    throw new Error("session_bootstrap must be a mapping");
  }

  readString(
    document.session_bootstrap.public_stage,
    "session_bootstrap.public_stage",
  );
  readString(
    document.session_bootstrap.initial_state,
    "session_bootstrap.initial_state",
  );

  if (!Array.isArray(document.steps) || document.steps.length === 0) {
    throw new Error("steps must contain at least one entry");
  }

  document.steps.forEach((step, index) => validateFixtureStep(step, index));

  if (!isRecord(document.expected)) {
    throw new Error("expected must be a mapping");
  }

  if (isRecord(document.expected.terminal)) {
    if (document.expected.terminal.session_status !== undefined) {
      readString(
        document.expected.terminal.session_status,
        "expected.terminal.session_status",
      );
    }

    if (!isRecord(document.expected.events)) {
      throw new Error("expected.events must be a mapping");
    }

    asStringArray(
      document.expected.events.must_contain ?? [],
      "expected.events.must_contain",
    );
    if (document.expected.events.must_not_contain !== undefined) {
      asStringArray(
        document.expected.events.must_not_contain,
        "expected.events.must_not_contain",
      );
    }
    if (document.expected.events.contracts !== undefined) {
      if (!Array.isArray(document.expected.events.contracts)) {
        throw new Error("expected.events.contracts must be an array");
      }

      document.expected.events.contracts.forEach((contract, index) => {
        if (!isRecord(contract)) {
          throw new Error(`expected.events.contracts[${index}] must be a mapping`);
        }

        readString(
          contract.event_type,
          `expected.events.contracts[${index}].event_type`,
        );

        if (contract.producer !== undefined) {
          readString(
            contract.producer,
            `expected.events.contracts[${index}].producer`,
          );
        }

        if (
          contract.payload_private_contains !== undefined &&
          !isRecord(contract.payload_private_contains)
        ) {
          throw new Error(
            `expected.events.contracts[${index}].payload_private_contains must be a mapping`,
          );
        }

        if (
          contract.payload_public_contains !== undefined &&
          !isRecord(contract.payload_public_contains)
        ) {
          throw new Error(
            `expected.events.contracts[${index}].payload_public_contains must be a mapping`,
          );
        }

        if (contract.payload_private_required_keys !== undefined) {
          asStringArray(
            contract.payload_private_required_keys,
            `expected.events.contracts[${index}].payload_private_required_keys`,
          );
        }

        if (contract.payload_public_required_keys !== undefined) {
          asStringArray(
            contract.payload_public_required_keys,
            `expected.events.contracts[${index}].payload_public_required_keys`,
          );
        }
      });
    }
    return;
  }

  validateLegacyExpectedDocument(document.expected);
}

function parseYamlFixture(fixturePath: string): FixtureDocument {
  const rubyScript = [
    "require 'yaml'",
    "require 'json'",
    "document = YAML.load_file(ARGV[0])",
    "puts JSON.generate(document)",
  ].join(";");

  try {
    const json = execFileSync("ruby", ["-e", rubyScript, fixturePath], {
      encoding: "utf8",
    });

    return JSON.parse(json) as FixtureDocument;
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(`Failed to parse fixture YAML at ${fixturePath}: ${detail}`);
  }
}

function validateFixtureStep(step: unknown, index: number): void {
  if (!isRecord(step)) {
    throw new Error(`steps[${index}] must be a mapping`);
  }

  readString(step.at, `steps[${index}].at`);

  const actor = readString(step.actor, `steps[${index}].actor`);
  if (!FIXTURE_ACTORS.includes(actor as (typeof FIXTURE_ACTORS)[number])) {
    throw new Error(`Unsupported fixture actor at steps[${index}].actor: ${actor}`);
  }

  const type = readString(step.type, `steps[${index}].type`);
  if (
    !HAPPY_PATH_SUPPORTED_STEP_TYPES.includes(
      type as (typeof HAPPY_PATH_SUPPORTED_STEP_TYPES)[number],
    )
  ) {
    throw new Error(
      `Unsupported fixture step type for the first runnable slice at steps[${index}].type: ${type}`,
    );
  }

  parseLogicalOffsetMs(step.at);

  if (step.payload !== undefined && !isRecord(step.payload)) {
    throw new Error(`steps[${index}].payload must be a mapping when present`);
  }
}

function normalizeFixtureExpected(
  expected: Record<string, unknown>,
): FixtureExpected {
  if (!isRecord(expected.terminal)) {
    return normalizeLegacyFixtureExpected(expected);
  }

  const terminal = isRecord(expected.terminal) ? expected.terminal : {};
  const events = isRecord(expected.events) ? expected.events : {};
  const projections = isRecord(expected.projections) ? expected.projections : {};
  const checkpoints = Array.isArray(expected.checkpoints) ? expected.checkpoints : [];

  return {
    terminal: {
      sessionStatus: maybeReadString(
        terminal.session_status,
      ) as FixtureExpected["terminal"]["sessionStatus"],
      publicStage: maybeReadString(
        terminal.public_stage,
      ) as FixtureExpected["terminal"]["publicStage"],
      completedTaskCount: maybeReadNumber(terminal.completed_task_count),
      retryCount:
        terminal.retry_count === undefined
          ? undefined
          : Number(terminal.retry_count),
      helpLevelPeak:
        terminal.help_level_peak === undefined
          ? undefined
          : (String(terminal.help_level_peak) as FixtureExpected["terminal"]["helpLevelPeak"]),
      reportPublishStatus: readString(
        terminal.report_publish_status,
        "expected.terminal.report_publish_status",
      ) as FixtureExpected["terminal"]["reportPublishStatus"],
      endReason:
        terminal.end_reason === undefined
          ? undefined
          : (String(terminal.end_reason) as FixtureExpected["terminal"]["endReason"]),
      safetyNoticeLevel:
        terminal.safety_notice_level === undefined
          ? undefined
          : (String(
              terminal.safety_notice_level,
            ) as FixtureExpected["terminal"]["safetyNoticeLevel"]),
    },
    displayStatusExpectation:
      maybeReadString(expected.display_status) as FixtureExpected["displayStatusExpectation"],
    events: {
      mustContain: asStringArray(
        events.must_contain ?? [],
        "expected.events.must_contain",
      ),
      mustNotContain: Array.isArray(events.must_not_contain)
        ? asStringArray(
            events.must_not_contain,
            "expected.events.must_not_contain",
          )
        : undefined,
      stateTransitionChain: Array.isArray(events.state_transition_chain)
        ? asStringArray(
            events.state_transition_chain,
            "expected.events.state_transition_chain",
          )
        : undefined,
      contracts: Array.isArray(events.contracts)
        ? events.contracts
            .filter(isRecord)
            .map((contract, index) =>
              normalizeEventContract(
                contract,
                `expected.events.contracts[${index}]`,
              ),
            )
        : undefined,
    },
    projections: {
      live: isRecord(projections.live) ? projections.live : undefined,
      timeline: isRecord(projections.timeline) ? projections.timeline : undefined,
      report: isRecord(projections.report) ? projections.report : undefined,
      home: isRecord(projections.home) ? projections.home : undefined,
    },
    checkpoints: checkpoints
      .filter(isRecord)
      .map((checkpoint, index) => ({
        afterStep: readNumber(
          checkpoint.after_step,
          `expected.checkpoints[${index}].after_step`,
        ) as number,
        awaitingChildConfirmation: maybeReadBoolean(
          checkpoint.awaiting_child_confirmation,
        ),
      })),
  };
}

function normalizeLegacyFixtureExpected(
  expected: Record<string, unknown>,
): FixtureExpected {
  const projectionAssert = isRecord(expected.projection_assert)
    ? expected.projection_assert
    : {};
  const live = isRecord(projectionAssert.live) ? projectionAssert.live : {};
  const timeline = isRecord(projectionAssert.timeline)
    ? projectionAssert.timeline
    : {};
  const report = isRecord(projectionAssert.report) ? projectionAssert.report : {};
  const home = isRecord(projectionAssert.home) ? projectionAssert.home : {};

  return {
    terminal: {
      sessionStatus: maybeReadString(
        expected.terminal_session_status,
      ) as FixtureExpected["terminal"]["sessionStatus"],
      publicStage: maybeReadString(
        expected.terminal_public_stage,
      ) as FixtureExpected["terminal"]["publicStage"],
      completedTaskCount: maybeReadNumber(expected.completed_task_count),
      retryCount: maybeReadNumber(expected.retry_count),
      helpLevelPeak: maybeReadString(
        expected.help_level_peak,
      ) as FixtureExpected["terminal"]["helpLevelPeak"],
      reportPublishStatus: maybeReadString(
        expected.report_publish_status,
      ) as FixtureExpected["terminal"]["reportPublishStatus"],
      endReason: maybeReadString(
        expected.end_reason,
      ) as FixtureExpected["terminal"]["endReason"],
      safetyNoticeLevel: maybeReadString(
        expected.safety_notice_level,
      ) as FixtureExpected["terminal"]["safetyNoticeLevel"],
    },
    displayStatusExpectation:
      maybeReadString(expected.display_status) as FixtureExpected["displayStatusExpectation"],
    events: {
      mustContain: [],
    },
    projections: {
      live: {
        ...(maybeReadString(live.display_status) !== undefined
          ? {
              header: {
                display_status: maybeReadString(live.display_status),
              },
            }
          : {}),
        ...(live.current_task === null || maybeReadBoolean(live.current_task_is_null) === true
          ? { current_task_is_null: true }
          : {}),
        ...(typeof live.need_parent_intervention === "boolean"
          ? {
              parent_action: {
                need_parent_intervention: live.need_parent_intervention,
              },
            }
          : {}),
        ...(maybeReadString(live.parent_note_contains) !== undefined
          ? {
              parent_note_contains: maybeReadString(live.parent_note_contains),
            }
          : {}),
      },
      timeline: {
        ...(Array.isArray(timeline.required_events)
          ? {
              must_include_event_types: asStringArray(
                timeline.required_events,
                "expected.projection_assert.timeline.required_events",
              ),
            }
          : {}),
      },
      report: {
        ...(maybeReadString(report.publish_status) !== undefined
          ? {
              summary: {
                publish_status: maybeReadString(report.publish_status),
              },
            }
          : {}),
        ...(maybeReadString(report.safety_notice_level) !== undefined
          ? {
              safety: {
                safety_notice_level: maybeReadString(
                  report.safety_notice_level,
                ),
              },
            }
          : {}),
        ...(maybeReadString(report.achievement_contains) !== undefined
          ? {
              achievement_contains: maybeReadString(
                report.achievement_contains,
              ),
            }
          : {}),
        ...(maybeReadString(report.summary_contains) !== undefined
          ? {
              summary_contains: maybeReadString(report.summary_contains),
            }
          : {}),
        ...(maybeReadString(report.parent_summary_contains) !== undefined
          ? {
              parent_summary_contains: maybeReadString(
                report.parent_summary_contains,
              ),
            }
          : {}),
      },
      home: {
        ...(maybeReadString(home.latest_session_status) !== undefined
          ? {
              latest_session_status: maybeReadString(
                home.latest_session_status,
              ),
            }
          : {}),
        ...(home.continue_entry === null
          ? { continue_entry_allowed: false }
          : {}),
        ...(maybeReadString(home.latest_summary_contains) !== undefined
          ? {
              latest_summary_contains: maybeReadString(
                home.latest_summary_contains,
              ),
            }
          : {}),
      },
    },
  };
}

function validateLegacyExpectedDocument(expected: Record<string, unknown>): void {
  if (expected.terminal_session_status !== undefined) {
    readString(expected.terminal_session_status, "expected.terminal_session_status");
  }

  if (expected.terminal_public_stage !== undefined) {
    readString(expected.terminal_public_stage, "expected.terminal_public_stage");
  }

  if (expected.projection_assert !== undefined && !isRecord(expected.projection_assert)) {
    throw new Error("expected.projection_assert must be a mapping");
  }

  if (
    isRecord(expected.projection_assert) &&
    isRecord(expected.projection_assert.timeline) &&
    expected.projection_assert.timeline.required_events !== undefined
  ) {
    asStringArray(
      expected.projection_assert.timeline.required_events,
      "expected.projection_assert.timeline.required_events",
    );
  }

  if (expected.checkpoints !== undefined) {
    if (!Array.isArray(expected.checkpoints)) {
      throw new Error("expected.checkpoints must be an array");
    }

    expected.checkpoints.forEach((checkpoint, index) => {
      if (!isRecord(checkpoint)) {
        throw new Error(`expected.checkpoints[${index}] must be a mapping`);
      }
      readNumber(
        checkpoint.after_step,
        `expected.checkpoints[${index}].after_step`,
      );
      if (checkpoint.awaiting_child_confirmation !== undefined) {
        maybeReadBoolean(checkpoint.awaiting_child_confirmation);
      }
    });
  }
}

function maybeReadString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function maybeReadNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}

function maybeReadBoolean(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function normalizeEventContract(
  contract: Record<string, unknown>,
  path: string,
): FixtureEventContractExpectation {
  return {
    eventType: readString(contract.event_type, `${path}.event_type`),
    producer: maybeReadString(contract.producer) as FixtureEventContractExpectation["producer"],
    payloadPrivateContains: isRecord(contract.payload_private_contains)
      ? contract.payload_private_contains
      : undefined,
    payloadPrivateRequiredKeys: Array.isArray(contract.payload_private_required_keys)
      ? asStringArray(
          contract.payload_private_required_keys,
          `${path}.payload_private_required_keys`,
        )
      : undefined,
    payloadPublicContains: isRecord(contract.payload_public_contains)
      ? contract.payload_public_contains
      : undefined,
    payloadPublicRequiredKeys: Array.isArray(contract.payload_public_required_keys)
      ? asStringArray(
          contract.payload_public_required_keys,
          `${path}.payload_public_required_keys`,
        )
      : undefined,
  };
}
