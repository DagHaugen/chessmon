# Stop a previous chessmon server/bridge before (re)starting, so re-running a launcher never stacks
# duplicate processes. Usage: powershell -File stop.ps1 <match>   (<match> = 'uvicorn' or 'rtc_peer')
param([Parameter(Mandatory = $true)][string]$match)
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match $match } |
  ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }
