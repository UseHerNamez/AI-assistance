# Assistance — Install Guide

## Easiest way (recommended)

1. Double-click **`Assistance-Setup.exe`**
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

Assistance starts **hidden** (tray only). Say **"Jarvis wake up"** to show the widget.

### Optional during setup

- **Install smart local LLM** — large download; makes Jarvis understand natural speech better
- **Start when Windows starts** — on by default; runs hidden in the system tray on login
- **Launch Assistance now** — on by default; starts right after install finishes
- **Desktop shortcut** — enabled by default

## Zip install (advanced / developers)

If you received `Assistance-Setup.zip` instead of the `.exe`:

```powershell
.\install.ps1
```

This still requires Python 3.10+ on the machine.

## What downloads automatically (Setup.exe)

| Component | When |
|-----------|------|
| Private Python runtime | During setup (~12 MB) |
| Python libraries | During setup |
| Speech model (~40 MB) | During setup |
| Ollama + LLM (optional) | Only if you check the LLM box (several GB) |

## Voice usage

- **Hidden:** say **"Jarvis wake up"** first
- **Visible:** talk normally — no wake word needed
- **Privacy:** **"Jarvis stop listening"**, then click the red circle to listen again

## Troubleshooting

- Setup log: `%LOCALAPPDATA%\Assistance\setup.log`
- **Smart understanding later:** run `%LOCALAPPDATA%\Assistance\install_local_llm.ps1` or reinstall with the LLM option checked
- **No voice:** run `%LOCALAPPDATA%\Assistance\download_vosk_model.ps1`

## Building Setup.exe (for developers)

```powershell
.\build_setup.ps1
```

Output: `dist\Assistance-Setup.exe`
