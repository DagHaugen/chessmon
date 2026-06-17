# Report which apps are currently holding a camera, and which camera-grabbing
# processes are running. An app with LastUsedTimeStop = 0 is using it right now.
$stores = @(
  'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam',
  'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam'
)
Write-Output "=== apps CURRENTLY using the webcam (LastUsedTimeStop = 0) ==="
$found = $false
foreach ($s in $stores) {
  foreach ($scope in @($s, "$s\NonPackaged")) {
    Get-ChildItem $scope -ErrorAction SilentlyContinue | ForEach-Object {
      $stop = (Get-ItemProperty $_.PSPath -Name LastUsedTimeStop -ErrorAction SilentlyContinue).LastUsedTimeStop
      if ($null -ne $stop -and $stop -eq 0) {
        Write-Output ("  IN USE: " + ($_.PSChildName -replace '#', '\'))
        $script:found = $true
      }
    }
  }
}
if (-not $found) { Write-Output "  (none reported as actively holding the camera)" }
Write-Output "=== camera-grabbing processes running ==="
Get-Process -ErrorAction SilentlyContinue |
  Where-Object { $_.ProcessName -match 'Teams|Zoom|obs|Logi|Camera|Skype|Discord|Webex|slack|RealSense|Unison' } |
  Select-Object ProcessName, Id | Sort-Object ProcessName -Unique | Format-Table -AutoSize | Out-String
