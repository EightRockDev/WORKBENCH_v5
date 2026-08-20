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
'  SELF-EVIDENCING (lesson of 2026-08-19): the first version launched
'  fire-and-forget and left no trace of its own execution. When the chain
'  died, the scheduled task reported "successfully finished" while the
'  cycle never ran, and nothing anywhere said which link broke. This
'  version writes reports\launcher-last.txt BEFORE launching and appends
'  the cycle's real exit code after it finishes, so "did the launcher
'  run, and what did the cycle return?" always has an answer on disk.
'
'  autopilot.bat still runs visibly when double-clicked, which is what you
'  want when debugging a cycle by hand.
' ===================================================================
Option Explicit
Dim shell, fso, here, target, mark, log, code
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)

' Breadcrumb first: if this file never updates, wscript/the task is the
' broken link; if it shows a launch but no exit, the cycle is the link.
mark = fso.BuildPath(here, "reports\launcher-last.txt")
On Error Resume Next
If Not fso.FolderExists(fso.BuildPath(here, "reports")) Then
    fso.CreateFolder fso.BuildPath(here, "reports")
End If
Set log = fso.OpenTextFile(mark, 2, True)   ' 2 = overwrite
log.WriteLine "launcher started " & Now
log.Close
On Error GoTo 0

' 0 = hidden window. True = WAIT for the cycle, so its exit code is real
' evidence and Task Scheduler's "Do not start a new instance" default
' becomes a free second guard against overlapping cycles.
target = """" & fso.BuildPath(here, "autopilot.bat") & """ --hidden"
code = shell.Run("cmd /c " & target, 0, True)

On Error Resume Next
Set log = fso.OpenTextFile(mark, 8, True)   ' 8 = append
log.WriteLine "cycle exited " & code & " at " & Now
log.Close
On Error GoTo 0

WScript.Quit code
