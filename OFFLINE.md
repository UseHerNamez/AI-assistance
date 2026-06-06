# Assistance — Offline Install Guide (v0.2.0)

Use this package on a **completely offline** Windows machine. Everything is bundled — no downloads during install.

## What is included

| Component | Bundled |
|-----------|---------|
| Python 3.12 runtime + all libraries | Yes |
| Speech recognition model (Vosk) | Yes |
| Full app source (voice, memory, compose, quests, etc.) | Yes |
| Local LLM (Ollama + model) | Only if built with `-IncludeLLM` |

Voice uses the **offline Windows voice** (no internet needed for speech).

## For the person who prepares the package (online machine)

Rebuild from the latest source after any code change:

```powershell
.\build_all.ps1
```

Or offline package only:

```powershell
.\build_offline_package.ps1
```

Optional — include the local LLM (adds several GB):

```powershell
.\build_offline_package.ps1 -IncludeLLM
```

Output (in `dist\offline\`):

| File / folder | Use |
|---------------|-----|
| **`Assistance-Offline\`** | Copy entire folder to USB (recommended) |
| **`Assistance-Offline.zip`** | Same contents, zipped |
| **`Assistance-Offline-Setup.exe`** | One-click offline installer |

Ship any of these to the offline machine.

## For the offline machine (no internet)

### Option A — Setup.exe (easiest)

1. Copy `Assistance-Offline-Setup.exe` to the offline PC
2. Double-click it
3. Follow the wizard
4. Launch **Assistance** from the desktop shortcut

### Option B — Folder (USB copy)

1. Copy the entire **`Assistance-Offline`** folder to the offline PC
2. Double-click **`Install-Assistance.vbs`** inside that folder
3. Wait for setup to finish (creates `runtime\python` from bundled assets)
4. Double-click **`launch_assistance.vbs`** or use the desktop shortcut

### Option C — Zip

1. Copy `Assistance-Offline.zip` to the offline PC and unzip
2. Same as Option B — run **`Install-Assistance.vbs`**

## Typical package sizes

| Package | Approx. size |
|---------|----------------|
| Without LLM | ~400–700 MB (folder); Setup.exe is compressed |
| With LLM | ~3–5 GB+ |

## Voice commands (same as online v0.2)

- Hidden: **"Open up"**, **"Wake up"**, or **"Jarvis …"** then your command
- Visible: talk normally (no wake word)
- Hide: **"Hide"**, **"Sleep"**
- Quit: **"Jarvis quit"**, **"Goodbye"**
- Memory: **"Open memory"**, **"Close memory"**
- Draft in Word/Notepad/Outlook: needs bundled LLM (`-IncludeLLM` build)
- Privacy: **"Stop listening"**, then click the red mic button

## Troubleshooting

- Log file: `setup.log` in the install folder (or `%LOCALAPPDATA%\Assistance\setup.log`)
- Build info: check `BUILD_INFO.txt` in the package for version and build date
- If speech does not work, confirm `offline_assets\vosk-model-small-en-us-0.15` exists in the package
- Smart LLM only works if the package was built with `-IncludeLLM`
- After install, data and logs live in `%USERPROFILE%\.quest_assistant\`
