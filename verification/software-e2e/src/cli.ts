#!/usr/bin/env node

import { readdirSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { assertGoldenOutput } from "./assert/golden-assert.ts";
import { runFixture } from "./run-fixture.ts";

async function main(): Promise<void> {
  const parsed = parseArgs(process.argv.slice(2));
  const fixturePaths = parsed.all
    ? collectFixturePaths(parsed.fixturesDir)
    : parsed.fixtureArgs.map(resolveFixtureArg).filter(Boolean) as string[];
  const finalFixturePaths =
    fixturePaths.length > 0
      ? fixturePaths
      : [resolveFixtureArg("fx_happy_path_basic") as string];
  let failureCount = 0;

  for (const [index, fixturePath] of finalFixturePaths.entries()) {
    const artifacts = await runFixture({ fixturePath });
    const assertion = assertGoldenOutput({
      fixture: artifacts.fixture,
      actual: artifacts,
    });

    if (index > 0) {
      console.log("");
    }

    printSummary(artifacts, assertion);
    if (!assertion.ok) {
      failureCount += 1;
    }
  }

  if (finalFixturePaths.length > 1) {
    console.log("");
    console.log(
      `Total: fixtures=${finalFixturePaths.length} failures=${failureCount}`,
    );
  }

  if (failureCount > 0) {
    process.exitCode = 1;
  }
}

function printSummary(
  artifacts: Awaited<ReturnType<typeof runFixture>>,
  assertion: ReturnType<typeof assertGoldenOutput>,
): void {
  const eventTypes = artifacts.events.map((event) => event.event_type);
  const summaryLines = [
    `Fixture: ${artifacts.fixture.id}`,
    `Session: ${artifacts.session.status} / ${artifacts.session.public_stage}`,
    `Tasks: completed=${artifacts.session.completed_task_count} retry=${artifacts.session.retry_count}`,
    `Report: ${artifacts.report?.publish_status ?? "none"}`,
    `Events: ${artifacts.events.length} (${eventTypes.join(", ")})`,
    `Assert: ${assertion.ok ? "PASS" : "FAIL"}`,
  ];

  console.log(summaryLines.join("\n"));

  if (!assertion.ok) {
    console.log("");
    assertion.failures.forEach((failure) => {
      console.log(
        `- ${failure.path}: expected=${String(failure.expected)} actual=${String(failure.actual)} (${failure.message})`,
      );
    });
  }
}

function resolveFixtureArg(rawArg?: string): string | undefined {
  if (!rawArg) {
    return undefined;
  }

  if (rawArg.endsWith(".yaml") || rawArg.endsWith(".yml") || rawArg.includes("/")) {
    return resolve(rawArg);
  }

  return fileURLToPath(
    new URL(`../fixtures/${rawArg}.yaml`, import.meta.url),
  );
}

function parseArgs(rawArgs: string[]): {
  all: boolean;
  fixturesDir: string;
  fixtureArgs: string[];
} {
  let all = false;
  let fixturesDir = fileURLToPath(new URL("../fixtures", import.meta.url));
  const fixtureArgs: string[] = [];

  for (let index = 0; index < rawArgs.length; index += 1) {
    const arg = rawArgs[index];

    if (arg === "--all") {
      all = true;
      continue;
    }

    if (arg === "--fixtures-dir") {
      const nextArg = rawArgs[index + 1];
      if (!nextArg) {
        throw new Error("--fixtures-dir requires a path");
      }
      fixturesDir = resolve(nextArg);
      index += 1;
      continue;
    }

    fixtureArgs.push(arg);
  }

  return {
    all,
    fixturesDir,
    fixtureArgs,
  };
}

function collectFixturePaths(fixturesDir: string): string[] {
  return readdirSync(fixturesDir)
    .filter((entry) => !entry.startsWith("."))
    .filter((entry) => !entry.startsWith("._"))
    .filter((entry) => entry.endsWith(".yaml") || entry.endsWith(".yml"))
    .sort()
    .map((entry) => resolve(fixturesDir, entry));
}

main().catch((error) => {
  const message = error instanceof Error ? error.stack ?? error.message : String(error);
  console.error(message);
  process.exitCode = 1;
});
