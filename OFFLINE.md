# Assistance — Offline Install Guide

Use this package on a **completely offline** Windows machine. Everything is bundled — no downloads during install.

## What is included

| Component | Bundled |
|-----------|---------|
| Python runtime + all libraries | Yes |
| Speech recognition model (Vosk) | Yes |
| App source | Yes |
| Local LLM (Ollama + model) | Only if built with `-IncludeLLM` |

Voice uses the **offline Windows voice** (no internet needed for speech).

## For the person who prepares the package (online machine)

Run once on a PC **with internet**:

```powershell
.\build_offline_package.ps1
```

Optional — include the local LLM (adds several GB):

```powershell
.\build_offline_package.ps1 -IncludeLLM
```

Output (in `dist\offline\`):

- **`Assistance-Offline\`** — full folder (copy to USB)
- **`Assistance-Offline.zip`** — same folder, zipped
- **`Assistance-Offline-Setup.exe`** — one-click offline installer

Ship any of these to the offline machine.

## For the offline machine (no internet)

### Option A — Setup.exe (easiest)

1. Copy `Assistance-Offline-Setup.exe` to the offline PC
2. Double-click it
3. Follow the wizard
4. Launch **Assistance** from the desktop shortcut

### Option B — Folder / zip

1. Copy `Assistance-Offline` folder (or unzip `Assistance-Offline.zip`) to the offline PC
2. Double-click **`Install-Assistance.vbs`**
3. Wait for setup to finish
4. Double-click **`launch_assistance.vbs`** or use the desktop shortcut if created

## Typical package sizes

| Package | Approx. size |
|---------|----------------|
| Without LLM | ~400–600 MB (folder), smaller as Setup.exe |
| With LLM | ~3–5 GB+ |

## Voice commands

Same as the online version:

- Hidden: **"Jarvis wake up"**
- Visible: talk normally (no wake word)
- Privacy: **"Jarvis stop listening"**, then click the red button

## Troubleshooting

- Log file: `setup.log` in the install folder (or `%LOCALAPPDATA%\Assistance\setup.log`)
- If speech does not work, confirm `offline_assets\vosk-model-small-en-us-0.15` was copied with the package
- Smart LLM only works if the package was built with `-IncludeLLM`
