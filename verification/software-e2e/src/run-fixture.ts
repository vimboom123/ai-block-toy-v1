import type {
  FixtureRunArtifacts,
  NormalizedFixture,
} from "./contracts.ts";
import { fileURLToPath } from "node:url";
import { buildHomeSnapshotView } from "./projections/build-home.ts";
import { buildSessionLiveView } from "./projections/build-live.ts";
import { buildReportDetailView } from "./projections/build-report.ts";
import { buildSessionTimelineView } from "./projections/build-timeline.ts";
import { reduceReport } from "./reducers/report-reducer.ts";
import { reduceSession } from "./reducers/session-reducer.ts";
import { reduceTasks } from "./reducers/task-reducer.ts";
import { applyStateTransition } from "./state-driver.ts";
import { adaptFixtureStep } from "./event-adapter.ts";
import { loadFixture } from "./fixture-loader.ts";
import { createRunnerClock } from "./runner-clock.ts";
import {
  RUNNER_BASE_TIME_ISO,
  createCorrelationId,
  createEventId,
  createInitialSessionAggregate,
  deriveDisplayStatus,
} from "./helpers.ts";

export interface RunFixtureInput {
  fixturePath?: string;
  fixture?: NormalizedFixture;
  generatedAt?: string;
}

export async function runFixture(
  input: RunFixtureInput,
): Promise<FixtureRunArtifacts> {
  const fixture =
    input.fixture ??
    (await loadFixture({
      fixturePath: input.fixturePath ?? defaultFixturePath(),
    }));
  const generatedAt = input.generatedAt ?? RUNNER_BASE_TIME_ISO;
  const clock = createRunnerClock({
    sessionId: fixture.id,
    baseTimeIso: generatedAt,
  });

  let nextSeqNo = 1;
  let session = createInitialSessionAggregate(fixture);
  let tasks = {};
  let report = null;
  const events = [];
  const stepSnapshots = [];
  const displayStatusHistory = [];

  for (const step of fixture.steps) {
    const stamp = clock.advanceTo(step.at);
    const correlationId = createCorrelationId(session.id, step.step_no);
    const adapted = adaptFixtureStep({
      fixture,
      step,
      session,
      tasks,
      stamp,
      nextSeqNo,
      correlationId,
    });

    nextSeqNo = adapted.nextSeqNo;

    for (const event of adapted.events) {
      const transition = applyStateTransition({
        currentState: session.current_state,
        event,
        session,
        tasks,
      });

      events.push(event);
      session = reduceSession(session, event, tasks);
      tasks = reduceTasks(tasks, event);
      report = reduceReport(report, event, session, tasks);
      displayStatusHistory.push(
        deriveDisplayStatus({
          status: session.status,
          publicStage: session.public_stage,
          endReason: session.end_reason,
        }),
      );

      if (transition.transitionMetadata.ruleId) {
        const transitionStamp = clock.nextEventStamp();
        const transitionEvent = {
          id: createEventId(session.id, nextSeqNo),
          session_id: session.id,
          seq_no: nextSeqNo,
          event_type: "state.transition_applied",
          producer: "state_engine" as const,
          task_id: event.task_id,
          theme_id: session.theme_id,
          state_before: session.current_state,
          state_after: transition.nextState,
          caution_level: null,
          confidence_score: null,
          confidence_level: null,
          causation_event_id: event.id,
          correlation_id: correlationId,
          payload_public: null,
          payload_private: {
            rule_id: transition.transitionMetadata.ruleId,
            reason: transition.transitionMetadata.reason,
            trigger_event: event.event_type,
          },
          parent_visible: false,
          occurred_at: transitionStamp.occurredAt,
          ingested_at: transitionStamp.occurredAt,
        };
        nextSeqNo += 1;
        events.push(transitionEvent);
        session = reduceSession(session, transitionEvent, tasks);
        tasks = reduceTasks(tasks, transitionEvent);
        report = reduceReport(report, transitionEvent, session, tasks);
        displayStatusHistory.push(
          deriveDisplayStatus({
            status: session.status,
            publicStage: session.public_stage,
            endReason: session.end_reason,
          }),
        );
      }
    }

    stepSnapshots.push({
      afterStep: step.step_no,
      liveView: buildSessionLiveView({
        session,
        tasks,
        recentVisibleEvents: events.filter((event) => event.parent_visible).slice(-10),
        generatedAt,
      }),
    });
  }

  const liveView = buildSessionLiveView({
    session,
    tasks,
    recentVisibleEvents: events.filter((event) => event.parent_visible).slice(-10),
    generatedAt,
  });
  const timelineView = buildSessionTimelineView({
    session,
    tasks,
    events,
    generatedAt,
  });
  const reportView = report
    ? buildReportDetailView({
        report,
        session,
        tasks,
        generatedAt,
      })
    : null;
  const homeAlerts =
    session.end_reason === "safety_stop"
      ? [
          {
            alert_type: "safety_stop" as const,
            severity: "critical" as const,
            title: "本轮因安全原因提前结束",
            body: "建议家长先看一下当前情况，再决定要不要继续。",
            entry_cta_text: "查看提醒",
          },
        ]
      : [];

  const homeView = buildHomeSnapshotView({
    activeSessionLiveView: liveView,
    latestReportDetailView: reportView,
    continueEntry: session.status === "aborted" && session.end_reason !== "safety_stop"
      ? {
          theme_id: session.theme_id,
          theme_name: session.theme_code,
          entry_reason_text: "这一轮提前结束了，可以稍后再继续。",
          entry_cta_text: "重新开始",
        }
      : null,
    alerts: homeAlerts,
    generatedAt,
  });

  return {
    fixture,
    session,
    tasks,
    report,
    events,
    stepSnapshots,
    displayStatusHistory,
    liveView,
    timelineView,
    reportView,
    homeView,
  };
}

function defaultFixturePath(): string {
  return fileURLToPath(
    new URL("../fixtures/fx_happy_path_basic.yaml", import.meta.url),
  );
}
