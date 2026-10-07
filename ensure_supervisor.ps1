# Starts the supervisor if it is not already running.
#
# Registered as a Scheduled Task (every 10 minutes, at logon handled by
# autostart.bat) so a watchdog that dies gets replaced instead of leaving
# the stack unwatched for hours -- the supervisor repairs the stack; this
# repairs the supervisor.
#
# Running while one is up is harmless: the supervisor's lock makes a
# duplicate exit quietly, but the process check below is what stops us
# from even trying. The check must exclude powershell processes: this
# script's own command line contains the supervisor path, and counting
# yourself as the thing you were sent to start is a bug that ends in the
# supervisor never being started at all.
#
# Writes nothing on a healthy path. The scheduled task runs at low
# integrity in the logged-on session, so the supervisor lands in the
# interactive desktop like every other launch of it.
#
# Recreate the task (run as the logged-on user, no admin needed):
#   schtasks /Create /TN "Jarvis Ensure Supervisor" `
#     /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass `
#          -File C:\Users\Vijay\my-agent\fullstack-agent\ensure_supervisor.ps1" `
#     /SC MINUTE /MO 10 /RL LIMITED /F
$proc = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -match 'supervisor\.py' -and $_.Name -notmatch 'powershell'
}
if ($proc) { exit 0 }

$sup  = "$env:USERPROFILE\my-agent\fullstack-agent\supervisor.py"
$err  = "$env:USERPROFILE\my-agent\supervisor.stderr.log"
$args = "py -3 `"$sup`" 2>> `"$err`""
Start-Process -FilePath "powershell.exe" -ArgumentList @(
    '-NoProfile', '-NonInteractive', '-Command', $args
) -WindowStyle Normal