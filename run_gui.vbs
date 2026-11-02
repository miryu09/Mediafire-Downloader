Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
scriptDirectory = fileSystem.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = scriptDirectory

If Not fileSystem.FolderExists(scriptDirectory & "\.venv") Then
    shell.Run "cmd.exe /c py -3.14 -m venv """ & scriptDirectory & "\.venv""", 0, True
End If

exitCode = shell.Run("""" & scriptDirectory & "\run_gui.bat"" --vbs", 0, True)
If exitCode <> 0 Then
    MsgBox "MediaFire Downloader could not start (exit code " & exitCode & "). Run run_gui.bat in a terminal to see details.", vbExclamation, "MediaFire Downloader"
End If
