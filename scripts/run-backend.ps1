# Runs the FreshJobs backend (API + pollers) and keeps the PC from going to sleep while it runs,
# so jobs keep coming in. Only this window's request: close it and normal sleep settings apply again.
# The screen can still turn off.

$root = Split-Path -Parent $PSScriptRoot
Add-Type -Namespace FreshJobs -Name Power -MemberDefinition @'
[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint flags);
'@
# ES_CONTINUOUS | ES_SYSTEM_REQUIRED
[FreshJobs.Power]::SetThreadExecutionState([uint32]"0x80000001") | Out-Null
$Host.UI.RawUI.WindowTitle = "FreshJobs backend (keep open; Ctrl+C to stop)"
Write-Host "FreshJobs backend: http://localhost:8000  (this PC won't sleep while this window is open)"

Set-Location (Join-Path $root "backend")
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
