Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
shell.CurrentDirectory = root
command = Chr(34) & root & "\.venv\Scripts\python.exe" & Chr(34) & " " & Chr(34) & root & "\app.py" & Chr(34)
shell.Run command, 0, False
