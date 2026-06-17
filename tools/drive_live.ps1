param([int]$Port = 8766)
$base = "http://127.0.0.1:$Port"
# wait until the server is accepting connections
$up = $false
for ($i = 0; $i -lt 40; $i++) {
  try { Invoke-RestMethod "$base/state" -TimeoutSec 2 | Out-Null; $up = $true; break }
  catch { Start-Sleep -Milliseconds 150 }
}
if (-not $up) { Write-Output "server not up on $Port"; exit 1 }

function Post($path, $bodyObj) {
  Invoke-RestMethod "$base$path" -Method Post -ContentType 'application/json' `
    -Body ($bodyObj | ConvertTo-Json -Compress)
}

Post '/move'    @{ uci = 'b7b8q' } | Out-Null
$afterConfirm = Post '/confirm' @{}
Write-Output ("after confirm: mode={0} promo={1}" -f $afterConfirm.mode, ($afterConfirm.promo -join ','))
$afterN = Post '/promote' @{ piece = 'n' }
Write-Output ("promote('n') -> history={0} active={1}" -f ($afterN.history -join ' '), $afterN.active)
