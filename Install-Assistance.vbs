Set shell = CreateObject("Wscript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
setupScript = appDir & "\scripts\setup_runtime_offline.ps1"

If Not fso.FileExists(setupScript) Then
    MsgBox "Setup script not found. Make sure the full Assistance offline folder was copied.", vbCritical, "Assistance"
    WScript.Quit 1
End If

shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & setupScript & """ -InstallDir """ & appDir & """", 1, True

If fso.FileExists(appDir & "\runtime\python\pythonw.exe") Then
    shell.Run """" & appDir & "\launch_assistance.vbs""", 0, False
    MsgBox "Assistance is ready and running in the background.", vbInformation, "Assistance"
Else
    MsgBox "Setup may have failed. Check setup.log in this folder.", vbExclamation, "Assistance"
End If
