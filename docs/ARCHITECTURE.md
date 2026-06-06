# Assistance architecture

## Pipeline

```
WakeWordGate (voice_listener.py)
       ↓
Speech-to-text (Vosk)
       ↓
IntentRouter (intent/router.py)     ← parser first
       ↓
Tool calls (intent/tools.py)
       ↓
QuestActionHost (ui/action_host.py) ← executes on UI thread
       ↓
QuestDB / browser / FX / TTS
```

The LLM (`local_llm.py`) only runs when `RouteKind.LLM` is chosen. Its JSON actions are converted to the same tool calls via `llm_actions_to_tool_calls()`.

## Modules

| Path | Role |
|------|------|
| `core/` | `SessionContext`, `ToolCall`, `RouteDecision` |
| `intent/` | Router, parser → tools, LLM → tools |
| `ui/action_host.py` | Runs tools against the widget |
| `monitor/logger.py` | `~/.quest_assistant/logs/{voice,intent,actions,errors}.log` |
| `parser.py` | Deterministic phrase matching (unchanged API) |
| `ui_widget.py` | Thin orchestration: route → execute → refresh |

## Tool names

- `create_task`, `delete_task`, `complete_task`, `edit_task`
- `start_add_mode`, `stop_add_mode`, `start_delete_mode`
- `set_fx`, `listen_off`, `show_window`, `hide_window`, `quit_app`
- `open_browser`, `web_search`, `speak`, `set_footer`

## Memory (Phase 3)

SQLite tables in the same `quests.db` file:

| Table | Purpose |
|-------|---------|
| `memory_prefs` | Settings (browser, search engine, how to address user) |
| `memory_facts` | Stable facts (names, relationships, notes) |
| `memory_episodic` | Recent actions (last add/delete/complete, interactions) |

The **parser/router** does not use memory for quests/FX/mic. Memory is injected into **LLM prompts only** (`format_for_llm`).

**Remember commands** (parser-side, no LLM):

- "Remember my browser is Firefox"
- "My name is Alex"
- "Call me captain"

**Auto episodic:** quest add/delete/complete and each utterance summary.

## Voice pipeline (Phase 2)

```
Microphone → UtteranceDetector (VAD) → STT backend → WakeWordGate → IntentRouter
```

| Variable | Default | Meaning |
|----------|---------|---------|
| `JARVIS_STT_BACKEND` | `vosk` | `vosk`, `whisper`, or `ab` (log both) |
| `JARVIS_VAD_SILENCE_MS` | `700` | Trailing silence before end-of-utterance |
| `JARVIS_VAD_MIN_SPEECH_MS` | `250` | Ignore shorter blips |
| `JARVIS_VAD_ENERGY_THRESHOLD` | `380` | RMS threshold (int16); raise in noisy rooms |
| `JARVIS_STT_WHISPER_MODEL` | `tiny.en` | faster-whisper model id |
| `JARVIS_STT_WHISPER_COMPUTE` | `int8` | Quantization for CPU |

Whisper: `pip install -r requirements-whisper.txt`

Logs: `~/.quest_assistant/logs/voice.log`

## Phase 4 — Permissions

| Risk | Examples | Policy |
|------|----------|--------|
| Low | open browser, search, add quest | run immediately |
| Medium | delete quest, edit quest | confirm once per session |
| High | quit app | confirm every time |

- Each `ToolCall` carries `risk` (see `intent/tools.py`, `parser_route.py`).
- Defaults in `permissions/policy.py` (`TOOL_RISK`) when risk is omitted.
- `QuestActionHost.execute` partitions calls; `PermissionConfirmDialog` lists pending actions.
- Session flag `PermissionSession.medium_confirmed` set after the user approves any medium batch.

## Phase 5 — Planner, events, vision

### Event bus

```
BatteryMonitor / DownloadWatcher / TimerScheduler
        ↓ post(AssistantEvent)
   EventBus (Qt signal)
        ↓
   QuestWidget._on_assistant_event → TTS + footer
```

| Source | Trigger |
|--------|---------|
| Battery | Crosses 20% / 10% (Windows) |
| Downloads | New file in `~/Downloads` |
| Timers | `schedule_timer` tool / "timer in 5 minutes" |

Env: `JARVIS_EVENTS_BATTERY`, `JARVIS_EVENTS_DOWNLOADS` (default on).

Logs: `~/.quest_assistant/logs/events.log`

### Planner

Utterances like **"research laptops under $1000"** → `RouteKind.PLAN`:

1. `web_search` (opens browser)
2. Local LLM `summarize_research` → summary + quest title
3. `create_task` when `add_quest` is inferred

Requires Ollama (`may_use_ollama`). Parser still wins for delete/FX/mic.

### Vision (optional, heavy)

- `JARVIS_VISION=1` enables "what's on my screen"
- `JARVIS_VISION_MODEL` (default `llava`) via Ollama `/api/chat` + image
- Screenshot via Qt primary screen grab

## Next phases

1. **Planner v2** — fetch snippets / multi-step chains with checkpoints
2. **Events** — calendar, focus mode, custom hooks
