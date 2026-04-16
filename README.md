<div align="center">
  <img src="assets/readme/appagent-claw-banner.svg" alt="AppAgent-Claw banner" width="960" />
  <h1>AppAgent-Claw</h1>
  <p><strong>Turn GUI demonstrations into reusable and increasingly portable agent skills.</strong></p>
  <p>
    <a href="README.zh-CN.md">简体中文</a> ·
    <a href="https://github.com/Westlake-AGI-Lab/AppAgent-Claw/releases">Releases</a> ·
    <a href="#demos">Demos</a> ·
    <a href="#quick-start">Quick Start</a> ·
    <a href="#agent--skill-integration">Skill Integration</a>
  </p>
</div>

AppAgent-Claw is a macOS GUI skill framework for turning a demonstrated foreground desktop workflow into a reusable, inspectable, and progressively portable automation artifact.

It is intentionally narrow. AppAgent-Claw is **not** an open-domain computer-use agent. It is a practical bridge between agent platforms and GUI-only software: record a workflow once, annotate it with lightweight semantics, and replay it with layered target resolution, retries, validation, and structured diagnostics.

### TL;DR

- **Record** a foreground GUI workflow from demonstration.
- **Annotate** the saved flow with lightweight semantics and parameterized text slots.
- **Replay** it with layered matching, retries, validation, and structured diagnostics.

## Why this project exists

Modern agent platforms such as OpenClaw are powerful because they can call lightweight, reusable skills. But a large share of real software functionality is still exposed primarily through GUI interactions rather than stable APIs or CLIs. That leaves a gap: the agent can reason, but it still cannot reliably operate the software interface where the task actually lives.

AppAgent-Claw is designed to close that gap with a pragmatic engineering choice. Instead of aiming for unrestricted computer use, it focuses on **fixed foreground workflows** that can be taught once and reused many times. Inspired by AppAgentX and grounded in classic RPA ideas, it keeps the determinism and inspectability of workflow recording while adding enough semantic structure for agent-driven reuse.

## What it is / what it is not

| AppAgent-Claw is | AppAgent-Claw is not |
| --- | --- |
| A workflow reuse layer for familiar working environments | A cross-machine portability solution |
| Foreground desktop automation with explicit replay artifacts | Background window automation |
| Annotation-assisted replay | OCR-heavy desktop understanding |
| Agent-callable workflow packaging | Self-healing script synthesis |

The current version starts from stable replay in familiar working setups and uses that foundation to move toward richer semantic understanding and more portable workflow reuse.

<a id="demos"></a>
## Demos

> Inline demo videos are embedded below for direct playback on GitHub.

<table>
  <tr>
    <td align="center" width="50%">
      <strong>Netease Music · Recording</strong><br />
      <video
        src="https://github.com/user-attachments/assets/550f700b-ed6b-44f1-a0cf-489eb8cc66d0"
        width="100%"
        controls
        muted
        playsinline
        preload="metadata"
      ></video>
    </td>
    <td align="center" width="50%">
      <strong>Netease Music · Replay</strong><br />
      <video
        src="https://github.com/user-attachments/assets/7c164b1e-06e0-449a-95bb-4b97047bbaf7"
        width="100%"
        controls
        muted
        playsinline
        preload="metadata"
      ></video>
    </td>
  </tr>
</table>

This demo shows the two core loops of the project: teaching a fixed GUI procedure by demonstration, and replaying it later with window preparation, matching, fallback, and run diagnostics.

- **Netease Music** shows stable record-and-replay for a fixed media interaction flow under a similar UI state.

## How it works

1. **Record**: capture a user demonstration from the frontmost macOS app and save replay-critical assets such as `anchor.png`, `context.png`, `search_region`, action metadata, and window context.
2. **Annotate**: enrich the saved `flow.json` with `flow.description`, per-step descriptions, and safe replay-time text slots for selected `type_text` actions.
3. **Replay**: restore the app window when possible, then resolve click-like targets through a three-stage strategy:
   - local anchor matching near the recorded region
   - broader context matching on the recorded monitor
   - relative-coordinate fallback when visual matching fails
4. **Validate and diagnose**: apply retries, post-action checks, and structured `run.json` output so failures are inspectable instead of opaque.

## Why it matters

- **More practical than open-world perception alone** for repeated workflows on the same machine.
- **More semantic than a raw macro recorder** because the saved flow includes descriptions and parameterized text metadata.
- **Easy to integrate with agent systems** because the final artifact behaves like a lightweight reusable skill rather than a one-off recording.

<a id="quick-start"></a>
## Quick Start

### Requirements

- macOS
- Python `3.13+`
- `swift` on `PATH`
- macOS Accessibility permission for the terminal or Python interpreter you use
- macOS Screen Recording permission for the same process

### Install

```bash
uv sync
source .venv/bin/activate
```

If `swift` is missing, install Xcode Command Line Tools or full Xcode first.

### Record a workflow

```bash
python scripts/record.py start --name demo-flow
```

The recorder opens a small overlay. Start from the overlay, perform the workflow in the target app, then stop with `Esc`.

### Replay the workflow

```bash
python scripts/replay.py run "demo-flow" --debug
```

Replay targets can be:

- a recording directory
- a direct `flow.json` path
- a saved flow name

When replaying by name, the latest exact normalized match is used.

A new recording is saved under `data/recordings/`, and each replay run writes logs and debug artifacts under `data/runs/`.

### Run local checks

```bash
python -m py_compile scripts/*.py tests/*.py
pytest -q
```

## More commands

Re-run annotation for an existing recording:

```bash
python scripts/record.py annotate "demo-flow"
```

Replay a flow with parameterized text input:

```bash
python scripts/replay.py run "demo-flow" --inputs-json '{"input_message_body_01":"Tonight this track is so good"}' --debug
```

Only `type_text` steps marked with `text_policy.mode = "parameterized"` can be overridden at replay time.

<a id="agent--skill-integration"></a>
## Agent / Skill Integration

AppAgent-Claw is designed to be callable as a lightweight GUI skill from existing agent runtimes.

Prebuilt platform bundles are distributed through **GitHub Releases** rather than kept as committed runtime directories in the main repository.

Release page: <https://github.com/Westlake-AGI-Lab/AppAgent-Claw/releases>

| Platform | Release asset | Notes |
| --- | --- | --- |
| OpenClaw | `appagent-claw-openclaw.zip` | Packaged OpenClaw skill bundle for recording, annotation, replay, and reusable workflow packaging. |
| Codex | `appagent-claw-codex.zip` | Self-contained Codex skill bundle with the bundled runtime and workflow layout for AppAgent-Claw tasks. |
| Claude Code | `appagent-claw-claude-code.zip` | Self-contained Claude Code skill bundle with the same packaged runtime and workflow model. |

If you mainly want to use AppAgent-Claw from an agent runtime, start from the matching release bundle instead of cloning the repository first.

For repository development, use the root project with `uv sync`. For agent distribution, download the matching release asset for your runtime.

## Runtime data and examples

| Path | Purpose |
| --- | --- |
| `data/recordings/` | local recording sessions created during development and manual testing |
| `data/runs/` | replay logs, debug assets, and failure diagnostics |
| [`examples/recordings/`](examples/recordings/) | curated, commit-safe sample flows kept in the repository |

Current curated repository examples:

- [`examples/recordings/20260414_214025_netease-play-daily-recommendation`](examples/recordings/20260414_214025_netease-play-daily-recommendation)

## Repository layout

Core runtime modules live under `scripts/`:

- `record.py` — recording entrypoint
- `annotation.py` — post-recording descriptions and text-slot analysis
- `replay.py` — replay entrypoint
- `recorder.py` — event aggregation and step generation
- `capture.py` — screenshot asset generation
- `resolver.py` — replay-time target resolution
- `executor.py` — action execution
- `storage.py` — flow and run persistence
- `schema.py` — flow protocol
- `window_context.py` — frontmost window metadata and restoration helpers

## Current limitations

- CJK and IME-driven text recording is still unreliable in some apps.
- Highly dynamic UI regions may still force fallback to relative coordinates.
- Replay assumes a similar window layout, monitor arrangement, and app state to the original recording.
- The post-recording annotator is heuristic and conservative; review `flow.json` before relying on dynamic text reuse.
- The target app must be available in the active foreground desktop session.

## Roadmap

### Semantic understanding at every step

- Introduce GUI-agent reasoning into each recorded and replayed step to infer what the UI action is actually doing, not just where it clicks.
- Turn low-level interaction traces into higher-level workflow semantics so recorded procedures become easier to inspect, adapt, and reuse.
- Use step-level semantic grounding as the basis for moving from machine-specific replay toward more portable workflow execution.

### More agent involvement in recording and replay

- Increase GUI-agent participation during recording, annotation, and replay instead of limiting it to post-hoc metadata enrichment.
- Let the agent help describe intent, identify editable parameters, validate intermediate UI state, and decide when a replay step still matches the original task.
- Explore hybrid execution where deterministic replay remains the backbone, while the GUI agent provides interpretation, adjustment, and recovery when the environment changes.

### Workflow transfer beyond one machine

- Use semantic step understanding to reduce dependence on a single machine's exact layout, coordinates, and visual state.
- Build toward workflow artifacts that preserve both recorded evidence and agent-readable intent, so a demonstrated skill can be re-instantiated in a different environment with less manual re-recording.
- Provide a stronger bridge between reusable skill workflows today and more autonomous demonstration-driven GUI agents in the future.

## Citation

If you find this work helpful, please consider citing AppAgent-Claw. Our technical report will be released soon; for now, you can cite the GitHub repository:

```bibtex
@software{westlake_agi_lab_appagent_claw_2026,
  author = {{Westlake-AGI-Lab}},
  title = {AppAgent-Claw},
  url = {https://github.com/Westlake-AGI-Lab/AppAgent-Claw},
  year = {2026}
}
```

## License

[Apache License 2.0](LICENSE)

## Feedback and Contributions

This project was built under a very tight timeline, and it likely still contains many rough edges, missing cases, and implementation issues.

If you notice a bug, an unclear behavior, or a place where the workflow can be improved, please feel free to open an issue or submit a pull request. We'd love to make AppAgent-Claw better together with the community.
