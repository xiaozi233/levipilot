param([Parameter(Mandatory = $true)][int]$ProcessId)
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32HarnessFocus {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr SetActiveWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
}
"@
$p = Get-Process -Id $ProcessId
$h = $p.MainWindowHandle
if ($h -eq [IntPtr]::Zero) { throw "process $ProcessId has no main window" }
$fg = [Win32HarnessFocus]::GetForegroundWindow()
[uint32]$fgPid = 0
[uint32]$targetPid = 0
$fgThread = [Win32HarnessFocus]::GetWindowThreadProcessId($fg, [ref]$fgPid)
$targetThread = [Win32HarnessFocus]::GetWindowThreadProcessId($h, [ref]$targetPid)
[Win32HarnessFocus]::AttachThreadInput($fgThread, $targetThread, $true) | Out-Null
[Win32HarnessFocus]::ShowWindow($h, 9) | Out-Null
[Win32HarnessFocus]::BringWindowToTop($h) | Out-Null
[Win32HarnessFocus]::SetActiveWindow($h) | Out-Null
$ok = [Win32HarnessFocus]::SetForegroundWindow($h)
[Win32HarnessFocus]::AttachThreadInput($fgThread, $targetThread, $false) | Out-Null
Start-Sleep -Milliseconds 600
$now = [Win32HarnessFocus]::GetForegroundWindow()
[uint32]$nowPid = 0
[Win32HarnessFocus]::GetWindowThreadProcessId($now, [ref]$nowPid) | Out-Null
Write-Output "SetForegroundWindow=$ok foregroundPid=$nowPid targetPid=$ProcessId"
if ($nowPid -ne $ProcessId) { exit 1 }
