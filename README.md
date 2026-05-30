# Quest Assistant (local-only)

A small Windows desktop widget that lets you manage daily "quests" (tasks) by typing or speaking.

## Features (MVP)
- Desktop widget showing **Open** and **Done** quests
- Multi-select and delete quests from either the Open or Done section
- Add quests via text input (always works)
- Split numbered voice commands into separate short quests
- Optional free local LLM understanding through Ollama, with parser fallback if it is unavailable or too slow
- Hardware-aware LLM mode: strong CPUs or dedicated GPUs use the LLM; weaker machines fall back to the parser
- Optional **always-listening** voice capture (local speech-to-text) to add/complete quests
- Voice feedback so Jarvis confirms commands out loud, preferring a polished male neural voice when online
- Optional local sound effects for show/hide/mute/add/complete/delete events (off by default)
- Optional AI FX UI mode with randomly generated fast signal traces and subtle glow; can be turned off for lower CPU usage
- Privacy mode: say "Jarvis stop listening" to close the mic stream, then click the red button to listen again
- Local storage in SQLite (no cloud)

## Distribution (send to another PC)

### One-click installer (recommended)

Build a setup executable. Your friend only double-clicks it — no Python or PowerShell needed:

```powershell
.\build_setup.ps1
```

Creates `dist\online\Assistance-Setup.exe`. Send that file to PCs **with internet**.

During install it automatically downloads a private Python runtime, libraries, and the speech model. Optional checkbox for the local LLM (large download).

### Zip package (advanced)

Smaller zip for developers who already have Python:

```powershell
.\package_release.ps1
```

Creates `dist\online\Assistance-Setup.zip`. Recipient runs `.\install.ps1` after unzipping.

See `INSTALL.md` for the full recipient guide.

### Fully offline package (no internet on target PC)

Prepare on a machine **with internet**, then ship **`dist\offline\`** to an air-gapped PC:

```powershell
.\build_offline_package.ps1
```

Optional LLM bundle (several GB extra):

```powershell
.\build_offline_package.ps1 -IncludeLLM
```

Output in `dist\offline\`:

- `Assistance-Offline-Setup.exe` — double-click installer (recommended)
- `Assistance-Offline.zip` — unzip and run `Install-Assistance.vbs`

See `OFFLINE.md` for recipient steps.

### Folder layout

```text
dist/
  online/    → send to PCs WITH internet
  offline/   → send to PCs WITHOUT internet
  .build/    → temp build files (ignore)
```

### Rebuild everything after code changes

```powershell
.\build_all.ps1
```

This refreshes both `dist\online\` and `dist\offline\` from the latest source.

## Setup (Windows PowerShell)

Create a virtual environment and install dependencies:

```powershell
cd "C:\Users\SProductions\Desktop\AI assistance"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Run the app:

```powershell
python -m quest_assistant
```

Or use the runner script:

```powershell
.\run_quest_assistant.ps1
```

Start automatically when Windows starts:

```powershell
.\install_startup.ps1
```

Install the free local LLM (Ollama + `qwen2.5:3b`):

```powershell
.\install_local_llm.ps1
```

## Voice (local, optional)

Voice uses Vosk (offline). Download an English model, unzip it, then point the app at it using an environment variable:

```powershell
$env:VOSK_MODEL_PATH="C:\path\to\vosk-model-small-en-us-0.15"
python -m quest_assistant
```

Recommended: download the default local model location automatically:

```powershell
.\download_vosk_model.ps1
```

The app auto-detects this default model path:

```text
C:\Users\<you>\.quest_assistant\models\vosk-model-small-en-us-0.15
```

If no model is configured, the app still works with typed input.

## Voice commands

```text
Jarvis wake up
Jarvis open up
Jarvis add wash dishes
Jarvis I want to add one quest
Jarvis can you write down this mission wash dishes
Jarvis add first quest wash the dishes, second quest clean the house and wash the floor, third quest program my game project
Jarvis next quest do a workout
Jarvis done adding
Jarvis mark wash dishes done
Jarvis delete wash dishes
Jarvis turn the FX on
Jarvis open the browser
Jarvis search Google for python tutorials
Jarvis stop listening
```

When listening is stopped, the app cannot hear "Jarvis" anymore. A red always-on-top button appears so you can click it to resume listening.

Jarvis also speaks short confirmations, for example after opening, hiding, adding a quest, completing a quest, or entering privacy mode. By default, it uses Microsoft Edge neural TTS with `en-GB-RyanNeural` for a polished male assistant style and caches generated phrases for faster repeated playback.

Voice overrides:

```powershell
$env:JARVIS_EDGE_VOICE="en-US-GuyNeural" # different male neural voice
$env:JARVIS_TTS_BACKEND="edge"           # neural voice, default
$env:JARVIS_TTS_BACKEND="auto"           # instant short replies, neural longer replies
$env:JARVIS_TTS_BACKEND="sapi"           # force offline Windows voice, fastest
$env:JARVIS_SFX="1"                      # enable optional beep sound effects
```

When Ollama and the local model are available, Jarvis tries the LLM first for natural language understanding. If the LLM is missing, offline, errors, or responds too slowly for the computer, Jarvis automatically falls back to the built-in parser.

Jarvis also checks hardware before using the LLM. Dedicated GPUs like NVIDIA GeForce/GTX/RTX/Quadro, AMD Radeon/RX, or Intel Arc prefer the LLM. Strong CPUs such as Core i7/i9 or Ryzen 7/9 can use it too. Weak or unknown machines default to the parser. You can override this with:

```powershell
$env:JARVIS_LLM_MODE="force"   # always try local LLM
$env:JARVIS_LLM_MODE="parser"  # always use parser
```

