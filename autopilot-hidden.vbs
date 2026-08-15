' ===================================================================
'  Launch the Autopilot cycle with NO console window.
'
'  Owner, 2026-08-15: "this box popping up is a PITA." The window only
'  ever repeated what reports\autopilot.log and reports\autopilot-status.txt
'  already contain, so nothing is lost by hiding it.
'
'  Why a VBScript shim rather than a Task Scheduler setting: the native
'  way to get no window is "Run whether user is logged on or not", which
'  stores the account password AND moves the task to session 0, where it
'  loses the interactive user's Credential Manager - the same credentials
'  the cycle's `git push` depends on. This shim keeps the task running
'  exactly as it does today (same user, same token, same credentials) and
'  only changes the window style.
'
'  autopilot.bat still runs visibly when double-clicked, which is what you
'  want when debugging a cycle by hand.
' ===================================================================
Option Explicit
Dim shell, fso, here, target
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
target = """" & fso.BuildPath(here, "autopilot.bat") & """ --hidden"

' 0 = hidden window, False = do not wait. Task Scheduler treats the task
' as finished immediately; the cycle's own status file is the progress
' signal, exactly as it was before.
shell.Run "cmd /c " & target, 0, False
