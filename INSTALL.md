# Assistance — Install Guide (v0.2.0)

## Easiest way (recommended)

1. Double-click **`dist\online\Assistance-Setup.exe`** (or copy it to the target PC)
2. Follow the wizard (Next → Install)
3. Wait while it automatically downloads Python, libraries, and the speech model
4. On the last screen, leave **Launch Assistance now** and **Start when Windows starts** checked (defaults)
5. Click Finish — Assistance starts immediately in the background (system tray)

No PowerShell, no manual Python install, no commands.

### First run on a new PC

| Question | Answer |
|----------|--------|
| Do they need to run it manually first? | **No** — if they leave "Launch Assistance now" checked, it starts right after install |
| Does it start on every boot? | **Yes** — if "Start when Windows starts" is checked (default) |
| Can two copies run at once? | **No** — only one instance is allowed |

Assistance starts **hidden** (tray only). Say **"Open up"**, **"Wake up"**, or **"Jarvis, open up"** to show the widget.

### Optional during setup

- **Install smart local LLM** — large download; enables natural speech, document drafting, and memory chat
- **Start when Windows starts** — on by default; runs hidden in the system tray on login
- **Launch Assistance now** — on by default; starts right after install finishes
- **Desktop shortcut** — enabled by default

## Zip install (advanced / developers)

If you received `Assistance-Setup.zip` instead of the `.exe`:

```powershell
.\install.ps1
```

This requires Python 3.10+ on the machine. The zip includes the full `scripts\` folder and all current app modules.

Optional LLM during install:

```powershell
.\install.ps1 -IncludeLLM
```

## What downloads automatically (Setup.exe)

| Component | When |
|-----------|------|
| Private Python 3.12 runtime | During setup (~12 MB) |
| Python libraries (PySide6, Vosk, etc.) | During setup |
| Speech model (~40 MB) | During setup |
| Ollama + LLM (optional) | Only if you check the LLM box (several GB) |

## Voice usage (v0.2)

- **Hidden:** say **"Open up"**, **"Wake up"**, or **"Jarvis …"** then your command
- **Visible:** talk normally — no wake word needed
- **Summon:** "Open up", "Open", "Show up", "Wake up"
- **Hide:** "Hide", "Sleep", "Go away"
- **Quit:** "Jarvis quit", "Goodbye", "Jarvis goodbye"
- **Memory panel:** "Open memory", "Close memory"
- **Draft documents:** "Open Word and write me a text about …" (needs Ollama)
- **Privacy:** **"Stop listening"** / **"Mic off"**, then click the red circle to listen again

## Troubleshooting

- Setup log: `%LOCALAPPDATA%\Assistance\setup.log`
- Voice log: `%USERPROFILE%\.quest_assistant\logs\voice.log`
- **Smart understanding later:** run `%LOCALAPPDATA%\Assistance\install_local_llm.ps1` or reinstall with the LLM option checked
- **No voice:** run `%LOCALAPPDATA%\Assistance\download_vosk_model.ps1`

## Building installers (for developers)

Rebuild everything after code changes:

```powershell
.\build_all.ps1
```

Online installer only:

```powershell
.\build_setup.ps1
```

Output: `dist\online\Assistance-Setup.exe`
