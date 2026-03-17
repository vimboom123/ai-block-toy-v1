import type {
  FixtureRunArtifacts,
  GoldenAssertionResult,
  NormalizedFixture,
} from "../contracts.ts";

export interface GoldenAssertInput {
  fixture: NormalizedFixture;
  actual: FixtureRunArtifacts;
}

export function assertGoldenOutput(
  input: GoldenAssertInput,
): GoldenAssertionResult {
  const failures: GoldenAssertionResult["failures"] = [];
  const terminal = input.fixture.expected.terminal;
  const projectionExpected = input.fixture.expected.projections;

  if (
    terminal.sessionStatus !== undefined &&
    input.actual.session.status !== terminal.sessionStatus
  ) {
    failures.push({
      path: "session.status",
      expected: terminal.sessionStatus,
      actual: input.actual.session.status,
      message: "Terminal session status did not match fixture expectation",
    });
  }

  if (
    terminal.completedTaskCount !== undefined &&
    input.actual.session.completed_task_count !== terminal.completedTaskCount
  ) {
    failures.push({
      path: "session.completed_task_count",
      expected: terminal.completedTaskCount,
      actual: input.actual.session.completed_task_count,
      message: "Completed task count did not match fixture expectation",
    });
  }

  if (
    terminal.publicStage !== undefined &&
    input.actual.session.public_stage !== terminal.publicStage
  ) {
    failures.push({
      path: "session.public_stage",
      expected: terminal.publicStage,
      actual: input.actual.session.public_stage,
      message: "Terminal public stage did not match fixture expectation",
    });
  }

  if (terminal.endReason !== undefined && input.actual.session.end_reason !== terminal.endReason) {
    failures.push({
      path: "session.end_reason",
      expected: terminal.endReason,
      actual: input.actual.session.end_reason,
      message: "Session end_reason did not match fixture expectation",
    });
  }

  if (terminal.helpLevelPeak !== undefined && input.actual.session.help_level_peak !== terminal.helpLevelPeak) {
    failures.push({
      path: "session.help_level_peak",
      expected: terminal.helpLevelPeak,
      actual: input.actual.session.help_level_peak,
      message: "Session help_level_peak did not match fixture expectation",
    });
  }

  if (
    terminal.reportPublishStatus !== undefined &&
    (input.actual.report?.publish_status ?? null) !== terminal.reportPublishStatus
  ) {
    failures.push({
      path: "report.publish_status",
      expected: terminal.reportPublishStatus,
      actual: input.actual.report?.publish_status ?? null,
      message: "Report publish status did not match fixture expectation",
    });
  }

  if (
    terminal.safetyNoticeLevel !== undefined &&
    (input.actual.report?.safety_notice_level ?? null) !== terminal.safetyNoticeLevel
  ) {
    failures.push({
      path: "report.safety_notice_level",
      expected: terminal.safetyNoticeLevel,
      actual: input.actual.report?.safety_notice_level ?? null,
      message: "Report safety_notice_level did not match fixture expectation",
    });
  }

  const actualEventTypes = new Set(input.actual.events.map((event) => event.event_type));
  input.fixture.expected.events.mustContain.forEach((eventType) => {
    if (!actualEventTypes.has(eventType)) {
      failures.push({
        path: `events.mustContain.${eventType}`,
        expected: true,
        actual: false,
        message: `Required event type was not emitted: ${eventType}`,
      });
    }
  });

  input.fixture.expected.events.mustNotContain?.forEach((eventType) => {
    if (actualEventTypes.has(eventType)) {
      failures.push({
        path: `events.mustNotContain.${eventType}`,
        expected: false,
        actual: true,
        message: `Unexpected event type was emitted: ${eventType}`,
      });
    }
  });

  input.fixture.expected.events.contracts?.forEach((contract, index) => {
    const matchingEvent = input.actual.events.find(
      (event) =>
        event.event_type === contract.eventType &&
        (contract.producer === undefined || event.producer === contract.producer) &&
        hasRequiredKeys(event.payload_private, contract.payloadPrivateRequiredKeys) &&
        hasRequiredKeys(event.payload_public, contract.payloadPublicRequiredKeys) &&
        matchesDeepPartial(event.payload_private, contract.payloadPrivateContains) &&
        matchesDeepPartial(event.payload_public, contract.payloadPublicContains),
    );

    if (!matchingEvent) {
      failures.push({
        path: `events.contracts.${index}.${contract.eventType}`,
        expected: true,
        actual: false,
        message: `Event contract was not satisfied for ${contract.eventType}`,
      });
    }
  });

  if (input.fixture.expected.displayStatusExpectation !== undefined) {
    const expectedDisplayStatus = input.fixture.expected.displayStatusExpectation;
    const actualFinalStatus = input.actual.liveView.header.display_status;
    if (expectedDisplayStatus === "active_then_ended") {
      const hadActive = input.actual.displayStatusHistory.includes("active");
      const finalEnded = actualFinalStatus === "ended";
      if (!(hadActive && finalEnded)) {
        failures.push({
          path: "display_status_history",
          expected: "active_then_ended",
          actual: input.actual.displayStatusHistory.join(" -> "),
          message: "Display status history did not follow active_then_ended",
        });
      }
    } else if (actualFinalStatus !== expectedDisplayStatus) {
      failures.push({
        path: "live.header.display_status",
        expected: expectedDisplayStatus,
        actual: actualFinalStatus,
        message: "Final display_status did not match fixture expectation",
      });
    }
  }

  if (projectionExpected.live) {
    if (
      projectionExpected.live.header?.public_stage !== undefined &&
      input.actual.liveView.header.public_stage !==
        projectionExpected.live.header?.public_stage
    ) {
      failures.push({
        path: "live.header.public_stage",
        expected: projectionExpected.live.header?.public_stage ?? null,
        actual: input.actual.liveView.header.public_stage,
        message: "Live header public_stage did not match fixture expectation",
      });
    }

    if (
      projectionExpected.live.header?.display_status !== undefined &&
      input.actual.liveView.header.display_status !==
        projectionExpected.live.header?.display_status
    ) {
      failures.push({
        path: "live.header.display_status",
        expected: projectionExpected.live.header?.display_status ?? null,
        actual: input.actual.liveView.header.display_status,
        message: "Live header display_status did not match fixture expectation",
      });
    }

    if (
      projectionExpected.live.progress?.completed_task_count !== undefined &&
      input.actual.liveView.progress.completed_task_count !==
        projectionExpected.live.progress?.completed_task_count
    ) {
      failures.push({
        path: "live.progress.completed_task_count",
        expected: projectionExpected.live.progress?.completed_task_count ?? null,
        actual: input.actual.liveView.progress.completed_task_count,
        message: "Live completed_task_count did not match fixture expectation",
      });
    }

    if (
      projectionExpected.live.progress?.retry_count !== undefined &&
      input.actual.liveView.progress.retry_count !==
        projectionExpected.live.progress?.retry_count
    ) {
      failures.push({
        path: "live.progress.retry_count",
        expected: projectionExpected.live.progress?.retry_count ?? null,
        actual: input.actual.liveView.progress.retry_count,
        message: "Live retry_count did not match fixture expectation",
      });
    }

    if (
      projectionExpected.live.current_task_is_null !== undefined &&
      Boolean(input.actual.liveView.current_task === null) !==
        Boolean(projectionExpected.live.current_task_is_null)
    ) {
      failures.push({
        path: "live.current_task_is_null",
        expected: Boolean(projectionExpected.live.current_task_is_null),
        actual: input.actual.liveView.current_task === null,
        message: "Live current_task nullability did not match fixture expectation",
      });
    }

    if (projectionExpected.live.current_task?.parent_note === "system_has_given_a_key_hint") {
      const hintReflected =
        Boolean(input.actual.liveView.current_task?.parent_note?.includes("关键")) ||
        Object.values(input.actual.tasks).some((task) =>
          typeof task.parent_note === "string" && task.parent_note.includes("关键"),
        );
      if (!hintReflected) {
        failures.push({
          path: "live.current_task.parent_note",
          expected: "contains:关键",
          actual: input.actual.liveView.current_task?.parent_note ?? null,
          message: "Hint guidance note was not preserved in live/task state",
        });
      }
    }

    if (projectionExpected.live.parent_note_contains !== undefined) {
      const needle = String(projectionExpected.live.parent_note_contains);
      const haystacks = [
        input.actual.liveView.current_task?.parent_note ?? null,
        ...Object.values(input.actual.tasks).map((task) => task.parent_note),
      ].filter((entry): entry is string => typeof entry === "string");
      if (!haystacks.some((entry) => entry.includes(needle))) {
        failures.push({
          path: "live.parent_note_contains",
          expected: needle,
          actual: haystacks,
          message: "Expected parent_note text was not preserved in live/task state",
        });
      }
    }

    if (
      projectionExpected.live.parent_action?.need_parent_intervention !== undefined &&
      input.actual.liveView.parent_action.need_parent_intervention !==
        projectionExpected.live.parent_action?.need_parent_intervention
    ) {
      failures.push({
        path: "live.parent_action.need_parent_intervention",
        expected:
          projectionExpected.live.parent_action?.need_parent_intervention ?? null,
        actual: input.actual.liveView.parent_action.need_parent_intervention,
        message: "Live parent intervention flag did not match fixture expectation",
      });
    }
  }

  if (projectionExpected.timeline) {
    const displayTypes = new Set(
      input.actual.timelineView.items.map((item) => item.display_type),
    );
    const sourceEventTypes = new Set(
      input.actual.timelineView.items.flatMap((item) => item.meta.source_event_types),
    );

    if (
      projectionExpected.timeline.exact_item_count !== undefined &&
      input.actual.timelineView.items.length !==
        Number(projectionExpected.timeline.exact_item_count)
    ) {
      failures.push({
        path: "timeline.exact_item_count",
        expected: projectionExpected.timeline.exact_item_count,
        actual: input.actual.timelineView.items.length,
        message: "Timeline item count did not match exactly",
      });
    }

    if (
      input.actual.timelineView.items.length <
      Number(projectionExpected.timeline.min_item_count ?? 0)
    ) {
      failures.push({
        path: "timeline.min_item_count",
        expected: projectionExpected.timeline.min_item_count ?? 0,
        actual: input.actual.timelineView.items.length,
        message: "Timeline item count was lower than expected",
      });
    }

    (projectionExpected.timeline.must_include_display_types ?? []).forEach(
      (displayType) => {
        if (!displayTypes.has(String(displayType))) {
          failures.push({
            path: `timeline.must_include_display_types.${String(displayType)}`,
            expected: true,
            actual: false,
            message: `Timeline display_type was not emitted: ${String(displayType)}`,
          });
        }
      },
    );

    (projectionExpected.timeline.must_exclude_display_types ?? []).forEach(
      (displayType) => {
        if (displayTypes.has(String(displayType))) {
          failures.push({
            path: `timeline.must_exclude_display_types.${String(displayType)}`,
            expected: false,
            actual: true,
            message: `Timeline unexpectedly emitted display_type: ${String(displayType)}`,
          });
        }
      },
    );

    (projectionExpected.timeline.must_include_event_types ?? []).forEach(
      (eventType) => {
        if (!sourceEventTypes.has(String(eventType))) {
          failures.push({
            path: `timeline.must_include_event_types.${String(eventType)}`,
            expected: true,
            actual: false,
            message: `Timeline source_event_type was not present: ${String(eventType)}`,
          });
        }
      },
    );

    (projectionExpected.timeline.must_exclude_event_types ?? []).forEach(
      (eventType) => {
        if (sourceEventTypes.has(String(eventType))) {
          failures.push({
            path: `timeline.must_exclude_event_types.${String(eventType)}`,
            expected: false,
            actual: true,
            message: `Timeline unexpectedly exposed source_event_type: ${String(eventType)}`,
          });
        }
      },
    );

    if (projectionExpected.timeline.public_payload_only) {
      const leakingItem = input.actual.timelineView.items.find((item) => {
        return item.display_text.includes("payload_private") || item.display_text.includes("rule_id");
      });

      if (leakingItem) {
        failures.push({
          path: "timeline.public_payload_only",
          expected: true,
          actual: false,
          message: `Timeline item appears to leak internal text: ${leakingItem.timeline_item_id}`,
        });
      }
    }
  }

  if (projectionExpected.report) {
    if (
      projectionExpected.report.summary?.publish_status !== undefined &&
      input.actual.reportView?.summary.publish_status !== projectionExpected.report.summary.publish_status
    ) {
      failures.push({
        path: "report.summary.publish_status",
        expected: projectionExpected.report.summary.publish_status,
        actual: input.actual.reportView?.summary.publish_status ?? null,
        message: "Report summary.publish_status did not match fixture expectation",
      });
    }

    if (
      projectionExpected.report.summary?.help_level_peak !== undefined &&
      input.actual.reportView?.summary.help_level_peak !== projectionExpected.report.summary.help_level_peak
    ) {
      failures.push({
        path: "report.summary.help_level_peak",
        expected: projectionExpected.report.summary.help_level_peak,
        actual: input.actual.reportView?.summary.help_level_peak ?? null,
        message: "Report summary.help_level_peak did not match fixture expectation",
      });
    }

    if (
      projectionExpected.report.summary?.confidence_overall !== undefined &&
      input.actual.reportView?.summary.confidence_overall !== projectionExpected.report.summary.confidence_overall
    ) {
      failures.push({
        path: "report.summary.confidence_overall",
        expected: projectionExpected.report.summary.confidence_overall,
        actual: input.actual.reportView?.summary.confidence_overall ?? null,
        message: "Report summary.confidence_overall did not match fixture expectation",
      });
    }

    if (
      projectionExpected.report.summary?.end_reason !== undefined &&
      input.actual.reportView?.summary.end_reason !== projectionExpected.report.summary.end_reason
    ) {
      failures.push({
        path: "report.summary.end_reason",
        expected: projectionExpected.report.summary.end_reason,
        actual: input.actual.reportView?.summary.end_reason ?? null,
        message: "Report summary.end_reason did not match fixture expectation",
      });
    }

    if (
      projectionExpected.report.safety?.safety_notice_level !== undefined &&
      input.actual.reportView?.safety.safety_notice_level !== projectionExpected.report.safety.safety_notice_level
    ) {
      failures.push({
        path: "report.safety.safety_notice_level",
        expected: projectionExpected.report.safety.safety_notice_level,
        actual: input.actual.reportView?.safety.safety_notice_level ?? null,
        message: "Report safety_notice_level did not match fixture expectation",
      });
    }

    (projectionExpected.report.required_text_fields ?? []).forEach((field) => {
      const value = field === "parent_summary"
        ? input.actual.reportView?.parent_text.parent_summary
        : field === "follow_up_suggestion"
          ? input.actual.reportView?.parent_text.follow_up_suggestion
          : null;
      if (!value || String(value).trim() === "") {
        failures.push({
          path: `report.required_text_fields.${String(field)}`,
          expected: "non-empty",
          actual: value ?? null,
          message: `Required report text field was empty: ${String(field)}`,
        });
      }
    });

    if (
      projectionExpected.report.achievement_must_reference_hint_completion &&
      !input.actual.reportView?.highlights.achievement_tags.includes("completed_with_hint")
    ) {
      failures.push({
        path: "report.achievement_must_reference_hint_completion",
        expected: true,
        actual: false,
        message: "Report did not mark completion_with_hint as expected",
      });
    }

    if (
      projectionExpected.report.parent_summary_must_reference_reengagement &&
      !/分心|重新/.test(input.actual.reportView?.parent_text.parent_summary ?? "")
    ) {
      failures.push({
        path: "report.parent_summary_must_reference_reengagement",
        expected: true,
        actual: input.actual.reportView?.parent_text.parent_summary ?? null,
        message: "Report parent_summary did not mention reengagement",
      });
    }

    if (projectionExpected.report.must_not_expose_raw_failure_detail) {
      const parentSummary = input.actual.reportView?.parent_text.parent_summary ?? "";
      if (/failed_confusion|payload_private|rule_id/.test(parentSummary)) {
        failures.push({
          path: "report.must_not_expose_raw_failure_detail",
          expected: true,
          actual: false,
          message: "Report parent_summary exposed internal/raw failure detail",
        });
      }
    }

    if (projectionExpected.report.achievement_contains !== undefined) {
      const needle = String(projectionExpected.report.achievement_contains);
      const haystacks = [
        ...(input.actual.reportView?.highlights.achievement_tags ?? []),
        ...(input.actual.reportView?.highlights.notable_moments ?? []),
        input.actual.reportView?.parent_text.parent_summary ?? "",
      ];
      if (!haystacks.some((entry) => String(entry).includes(needle))) {
        failures.push({
          path: "report.achievement_contains",
          expected: needle,
          actual: haystacks,
          message: "Report did not include the expected achievement text",
        });
      }
    }

    if (projectionExpected.report.summary_contains !== undefined) {
      const needle = String(projectionExpected.report.summary_contains);
      const actual = input.actual.reportView?.parent_text.parent_summary ?? null;
      if (!actual || !actual.includes(needle)) {
        failures.push({
          path: "report.summary_contains",
          expected: needle,
          actual,
          message: "Report parent_summary did not include the expected summary text",
        });
      }
    }

    if (projectionExpected.report.parent_summary_contains !== undefined) {
      const needle = String(projectionExpected.report.parent_summary_contains);
      const actual = input.actual.reportView?.parent_text.parent_summary ?? null;
      if (!actual || !actual.includes(needle)) {
        failures.push({
          path: "report.parent_summary_contains",
          expected: needle,
          actual,
          message: "Report parent_summary did not include the expected text",
        });
      }
    }

    if (projectionExpected.report.follow_up_contains !== undefined) {
      const needle = String(projectionExpected.report.follow_up_contains);
      const actual = input.actual.reportView?.parent_text.follow_up_suggestion ?? null;
      if (!actual || !actual.includes(needle)) {
        failures.push({
          path: "report.follow_up_contains",
          expected: needle,
          actual,
          message: "Report follow_up_suggestion did not include the expected text",
        });
      }
    }
  }

  if (projectionExpected.home) {
    if (
      projectionExpected.home.active_session_is_null !== undefined &&
      Boolean(input.actual.homeView.active_session === null) !==
        Boolean(projectionExpected.home.active_session_is_null)
    ) {
      failures.push({
        path: "home.active_session_is_null",
        expected: Boolean(projectionExpected.home.active_session_is_null),
        actual: input.actual.homeView.active_session === null,
        message: "Home active_session nullability did not match fixture expectation",
      });
    }

    if (
      projectionExpected.home.latest_report_expected !== undefined &&
      Boolean(input.actual.homeView.latest_report !== null) !==
        Boolean(projectionExpected.home.latest_report_expected)
    ) {
      failures.push({
        path: "home.latest_report_expected",
        expected: Boolean(projectionExpected.home.latest_report_expected),
        actual: input.actual.homeView.latest_report !== null,
        message: "Home latest_report presence did not match fixture expectation",
      });
    }

    if (
      projectionExpected.home.continue_entry_allowed !== undefined &&
      Boolean(input.actual.homeView.continue_entry !== null) !==
        Boolean(projectionExpected.home.continue_entry_allowed)
    ) {
      failures.push({
        path: "home.continue_entry_allowed",
        expected: Boolean(projectionExpected.home.continue_entry_allowed),
        actual: input.actual.homeView.continue_entry !== null,
        message: "Home continue_entry presence did not match fixture expectation",
      });
    }

    (projectionExpected.home.required_alert_types ?? []).forEach((alertType) => {
      if (!input.actual.homeView.alerts.some((alert) => alert.alert_type === alertType)) {
        failures.push({
          path: `home.required_alert_types.${String(alertType)}`,
          expected: true,
          actual: false,
          message: `Required home alert type missing: ${String(alertType)}`,
        });
      }
    });

    if (
      projectionExpected.home.must_surface_early_end_state &&
      !(
        input.actual.homeView.continue_entry ||
        input.actual.homeView.alerts.length > 0 ||
        input.actual.liveView.header.display_status === "aborted"
      )
    ) {
      failures.push({
        path: "home.must_surface_early_end_state",
        expected: true,
        actual: false,
        message: "Home did not surface early-end state in any visible way",
      });
    }
  }

  input.fixture.expected.checkpoints?.forEach((checkpoint) => {
    const snapshot = input.actual.stepSnapshots.find(
      (entry) => entry.afterStep === checkpoint.afterStep,
    );

    if (!snapshot) {
      failures.push({
        path: `checkpoints.${checkpoint.afterStep}`,
        expected: true,
        actual: false,
        message: `Missing step snapshot for after_step=${checkpoint.afterStep}`,
      });
      return;
    }

    if (
      checkpoint.awaitingChildConfirmation !== undefined &&
      Boolean(snapshot.liveView.current_task?.awaiting_child_confirmation) !==
        checkpoint.awaitingChildConfirmation
    ) {
      failures.push({
        path: `checkpoints.${checkpoint.afterStep}.awaiting_child_confirmation`,
        expected: checkpoint.awaitingChildConfirmation,
        actual: Boolean(snapshot.liveView.current_task?.awaiting_child_confirmation),
        message: "Live snapshot awaiting_child_confirmation did not match expectation",
      });
    }
  });

  return {
    ok: failures.length === 0,
    failures,
  };
}

function hasRequiredKeys(
  value: Record<string, unknown> | null,
  requiredKeys: string[] | undefined,
): boolean {
  if (!requiredKeys || requiredKeys.length === 0) {
    return true;
  }

  if (!value) {
    return false;
  }

  return requiredKeys.every((key) => key in value);
}

function matchesDeepPartial(
  actual: unknown,
  expected: unknown,
): boolean {
  if (expected === undefined) {
    return true;
  }

  if (expected === null || typeof expected !== "object") {
    return actual === expected;
  }

  if (Array.isArray(expected)) {
    if (!Array.isArray(actual) || actual.length !== expected.length) {
      return false;
    }

    return expected.every((entry, index) => matchesDeepPartial(actual[index], entry));
  }

  if (
    actual === null ||
    typeof actual !== "object" ||
    Array.isArray(actual)
  ) {
    return false;
  }

  return Object.entries(expected).every(([key, value]) =>
    matchesDeepPartial(
      (actual as Record<string, unknown>)[key],
      value,
    )
  );
}
