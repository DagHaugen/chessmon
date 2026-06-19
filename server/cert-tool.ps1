#requires -Version 7
<#
  chessmon cert-tool — look at the server certificate, or trust it on THIS PC.

    cert-tool.ps1                  show what cert.pem is (subject, what it covers, expiry, thumbprint,
                                   and whether this PC already trusts it)
    cert-tool.ps1 -Trust           add it to your Trusted Root (this user) -> the browser stops warning
    cert-tool.ps1 -Trust -Machine  trust it for ALL users on this PC (run from an elevated shell)
    cert-tool.ps1 -Untrust         take it back out of Trusted Root
    cert-tool.ps1 -Export dev.cer  write a .cer to hand to a phone/tablet
    cert-tool.ps1 -Path other.pem  inspect a different cert (.pem / .cer / .crt / .pfx)

  This only affects Windows machines. An iPhone/iPad/Android still needs the .cer installed on
  it (use -Export, send it over, open it, then trust it in the device's settings) — a Windows
  .exe can't reach into a phone's trust store.
#>
[CmdletBinding()]
param(
  [string]$Path = (Join-Path (Split-Path $PSScriptRoot -Parent) 'cert.pem'),
  [switch]$Trust,
  [switch]$Untrust,
  [switch]$Machine,
  [string]$Export
)

$ErrorActionPreference = 'Stop'

function Load-Cert([string]$p) {
  if (-not (Test-Path $p)) {
    throw "no certificate at: $p`n(run server\serve_https.py once to make cert.pem, or pass -Path)"
  }
  if ([IO.Path]::GetExtension($p).ToLower() -eq '.pfx') { return Get-PfxCertificate -FilePath $p }
  $bytes = [IO.File]::ReadAllBytes($p)
  $text  = [Text.Encoding]::ASCII.GetString($bytes)
  if ($text -match '(?s)-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----') {
    $der = [Convert]::FromBase64String(($Matches[1] -replace '\s', ''))   # PEM .pem/.crt -> the public cert
  } else {
    $der = $bytes                                                          # already DER (.cer)
  }
  return [System.Security.Cryptography.X509Certificates.X509CertificateLoader]::LoadCertificate($der)
}

function Test-Trusted($cert, [string]$loc) {
  $store = [System.Security.Cryptography.X509Certificates.X509Store]::new('Root', $loc)
  $store.Open('ReadOnly')
  $hit = @($store.Certificates | Where-Object { $_.Thumbprint -eq $cert.Thumbprint }).Count -gt 0
  $store.Close()
  return $hit
}

$cert = Load-Cert $Path

if ($Export) {
  [IO.File]::WriteAllBytes($Export, $cert.Export('Cert'))        # DER .cer
  Write-Host "exported -> $Export" -ForegroundColor Green
  Write-Host "send it to the phone/tablet, open it, then trust it in the device's settings." -ForegroundColor DarkGray
  return
}

if ($Trust -or $Untrust) {
  $loc = if ($Machine) { 'LocalMachine' } else { 'CurrentUser' }
  $store = [System.Security.Cryptography.X509Certificates.X509Store]::new('Root', $loc)
  $store.Open('ReadWrite')
  if ($Trust) {
    $store.Add($cert)
    Write-Host "trusted on this PC ($loc). Restart the browser — the chessmon URL is now trusted." -ForegroundColor Green
  } else {
    $store.Remove($cert)
    Write-Host "removed from Trusted Root ($loc)." -ForegroundColor Yellow
  }
  $store.Close()
  return
}

# default: info
$days   = [int]($cert.NotAfter - (Get-Date)).TotalDays
$san    = $cert.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.17' }
$covers = if ($san) { ($san.Format($false) -split ',\s*') -join "`n              " } else { '(no Subject Alternative Names)' }
Write-Host ""
Write-Host "  $Path" -ForegroundColor Cyan
Write-Host "  subject     $($cert.Subject)"
Write-Host "  issuer      $($cert.Issuer)"
Write-Host "  self-signed $($cert.Subject -eq $cert.Issuer)"
Write-Host "  valid       $($cert.NotBefore.ToString('yyyy-MM-dd')) .. $($cert.NotAfter.ToString('yyyy-MM-dd'))  ($days days left)"
Write-Host "  thumbprint  $($cert.Thumbprint)"
Write-Host "  covers      $covers"
Write-Host ""
Write-Host "  trusted on this PC — you      $(Test-Trusted $cert 'CurrentUser')"
Write-Host "  trusted on this PC — machine  $(Test-Trusted $cert 'LocalMachine')"
Write-Host ""
Write-Host "  -Trust to trust it here · -Export dev.cer to hand to a phone" -ForegroundColor DarkGray
