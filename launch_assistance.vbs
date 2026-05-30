Set shell = CreateObject("Wscript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = appDir & "\runtime\python\pythonw.exe"

If fso.FileExists(appDir & "\offline.mode") Then
    shell.Environment("Process")("JARVIS_TTS_BACKEND") = "sapi"
End If

If fso.FileExists(pythonw) Then
    shell.Run """" & pythonw & """ -m quest_assistant", 0, False
Else
    shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & appDir & "\run_quest_assistant.ps1""", 0, False
End If
