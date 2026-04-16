---
name: appagent-claw
description: Share and run a self-contained macOS record-and-replay skill for fixed foreground desktop app workflows. Use when Codex needs to work from the bundled AppAgent-Claw runtime to start a recording session, inspect or re-annotate a saved flow, replay a saved flow, inspect `flow.json` or `run.json`, or package a same-machine OpenClaw desktop workflow skill for reuse. Do not use for OCR, general desktop reasoning, background-window automation, cross-machine migration, or cross-platform automation.
---

# AppAgent-Claw

## Overview

Use AppAgent-Claw for a self-contained macOS record-and-replay workflow. This skill already bundles the runtime Python and Swift files under `scripts/`, so another user can download this skill folder alone and run it without depending on the original repository layout. Recording is not considered finished when the CLI exits; the agent must read the saved flow, review `flow.json`, and write back richer semantic descriptions plus any safe text-parameterization metadata.

## Guardrails

- Keep the task inside `macOS only`, `same machine only`, `foreground app only`, and `record and replay only`.
- State the limitation directly if the user asks for OCR, semantic UI understanding, background automation, cross-machine portability, or generic agent behavior.
- Require macOS Accessibility and Screen Recording permission for the terminal or Python process before claiming recording or replay can work.
- Expect replay reliability only when the window layout, monitor arrangement, and app state stay close to the original recording.

## Setup

- Work from the extracted skill directory so the bundled `scripts/` folder is available locally.
- Store every recording under the current skill directory's `data/recordings/`; do not scatter recordings across arbitrary folders, because replay and later lookup should always be able to find prior flows in one predictable place.
- Relative paths such as `--data-root ./data` are resolved against the current skill directory, not the shell working directory and not the Python interpreter location.
- Run `bash scripts/setup_env.sh` once to create `.venv/` and install `assets/requirements.txt`.
- Prefer `.venv/bin/python` for every runtime command after setup.
- Require `swift` on `PATH` before starting a recording because `scripts/record_overlay.swift` drives the recorder overlay.
- Require macOS Accessibility and Screen Recording permission for the terminal or Python interpreter you use to run the bundled scripts.

## Primary Commands

### Start A Recording

```bash
# For this skill, `--data-root ./data` resolves to:
# ./data/recordings/20260402_153000_<flow-name>/
.venv/bin/python scripts/record.py start --name <flow-name> --data-root ./data
```

- Expect the recorder overlay to appear.
- Tell the user to start from the overlay and stop with `Esc`.
- Relative `--data-root` values are resolved by the skill bundle itself, not by the shell working directory or the Python interpreter location.
- Expect JSON output with at least `status`, `recording_dir`, `flow_path`, `step_count`, `foreground_app`, and `annotation_status`.
- The saved `recording_dir` must live under the current skill directory's `data/recordings/`, so previous recordings remain easy to find for inspection and replay.
- After the recording command returns, open the saved `flow.json` and review the saved assets before treating the flow as reusable.
- During this post-recording pass, do not add, remove, reorder, or rewrite recorded actions; only refine descriptions and decide which recorded text inputs are safe to parameterize for future runs.
- Add `--skip-annotation` only when the user explicitly wants the raw recording.

### Re-Annotate A Saved Flow

```bash
.venv/bin/python scripts/record.py annotate "<target>"
```

- Use `<target>` as a recording directory, a direct `flow.json` path, or a saved flow name.
- Expect the command to update `flow.json` in place.

### Replay A Flow

```bash
# Replay a flow without runtime overrides:
.venv/bin/python scripts/replay.py run "<target>" --data-root ./data --debug

# Replay a parameterized flow with runtime text input:
.venv/bin/python scripts/replay.py run "<target>" --data-root ./data --inputs-json '{"input_text_01":"new text"}' --debug
```

- Use `--debug` when diagnosing instability or collecting failure artifacts.
- Use `--json` when the caller needs per-step details on stdout instead of the default summary payload.
- Use the first example when the flow has no parameterized inputs, or when you want replay to reuse the recorded fallback text.
- Use the second example only when the flow declares matching `flow.inputs` and the target `type_text` step is marked `text_policy.mode = "parameterized"`.
- Pass `--inputs-json '{"input_id":"value"}'` only for flow inputs declared in `flow.inputs`.
- Refuse ad-hoc text overrides for steps that are not marked `text_policy.mode = "parameterized"`.

## Operating Workflow

1. Store and resolve recordings from the current skill directory's `data/recordings/`; do not place flows in arbitrary other directories, because historical recordings need to stay easy to locate for replay.
2. Resolve the target recording by explicit path when the user already provides one, but that path should still point inside the current skill's `data/recordings/`.
3. Prefer a saved flow name only when a path is not available; the latest exact normalized match wins.
4. Re-open `flow.json` and review the saved assets before treating a newly recorded flow as reusable.
5. Treat the recorded action sequence as fixed review input; if the captured workflow itself is wrong, re-record it instead of editing the flow structure.
6. Write or refine `flow.description` so it clearly captures the user intent, the app route, and the expected outcome of the recorded task.
7. Write or refine each `steps[].description` so a later agent can understand why that recorded UI action exists.
8. Review `type_text`, `inputs`, and `text_policy`; only mark steps as parameterized when later text changes are clearly safe and will not break downstream anchors.
9. Do not change recorded actions, coordinates, locators, timing, retries, or step order during this review pass unless the user explicitly asks for manual flow editing.
10. Save the reviewed metadata back into the same `flow.json` so a later agent can either run the same task directly or intelligently adjust freeform text inputs for similar tasks.
11. Expect click-like replay steps to try template matching first and relative coordinates only as the last fallback.
12. Inspect `data/runs/.../run.json` before claiming that a replay failure is unexplained.

## Bundled Runtime

- Use the bundled Python modules under `scripts/` as the runtime source of truth.
- Keep `scripts/record.py`, `scripts/replay.py`, and `scripts/record_overlay.swift` together when distributing the skill.
- Treat this skill folder as a distributable unit; do not rely on `../../../README.md` or other repository-relative files at runtime.

## Artifacts To Inspect

- Store and inspect all recordings only under the current skill directory's `data/recordings/`, so prior flows stay easy to find and replay.
- Treat `data/runs/` as replay logs, debug captures, and failure diagnostics.
- Inspect `flow.json`, `assets/step_*/anchor.png`, and `assets/step_*/context.png` after recording.
- Inspect `run.json`, per-step attempt logs, and saved debug captures after replay.

## Post-Recording Review

- Do not stop at the auto-generated annotation.
- Read the saved `flow.json` carefully after recording.
- Use the saved images and step order to improve `flow.description` and `steps[].description`.
- Do not modify the recorded workflow itself in this review step; no adding, deleting, reordering, or repurposing steps.
- Make the saved descriptions concrete enough that a future agent can recognize “this is the same task” and replay it without re-recording.
- Keep only truly safe `type_text` steps parameterized so future similar requests can change text input intelligently without changing anchor-sensitive content.
- If a text input is not clearly safe to change later, leave it fixed and describe the constraint in the surrounding step description instead of loosening the metadata.
- Persist the reviewed metadata back into the same `flow.json` before returning the final recording result to the user.

## Failure Triage

- Separate window preparation failures from target resolution failures, execution failures, text validation failures, and annotation mistakes.
- Explain IME or CJK text issues as Accessibility exposure problems when the focused control does not provide a stable final `AXValue`.
- Treat success through relative-coordinate fallback as weaker evidence than success through template matching.
- Avoid claiming unsupported scenarios are solved just because one run happened to pass.

## Read More

- Read `references/repository-contract.md` for dependency setup, bundled file layout, module map, and validation commands.
