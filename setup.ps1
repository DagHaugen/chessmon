#requires -Version 7
<#
  chessmon setup — gets a club PC ready in one run:
    1. Python virtualenv + dependencies
    2. HTTPS certificate — make it, trust it on THIS PC, export dev.cer for phones
    3. Stockfish engine (auto-download) for move suggestions
    4. optional — link this PC to a chessmon-cloud broadcast (event key + club)

  Double-click setup.bat, or:  pwsh -File setup.ps1
    -NewCert    regenerate the certificate even if one already exists
    -NoEngine   skip the Stockfish download
    -NoCloud    skip the broadcast-link step
#>
[CmdletBinding()]
param([switch]$NewCert, [switch]$NoEngine, [switch]$NoCloud)
$ErrorActionPreference = 'Stop'

$Root   = $PSScriptRoot
$Server = Join-Path $Root 'server'
$Venv   = Join-Path $Root '.venv'
$Py     = Join-Path $Venv 'Scripts\python.exe'

function Step($n) { Write-Host "`n=== $n ===" -ForegroundColor Cyan }
function Good($m) { Write-Host "  [ok] $m" -ForegroundColor Green }
function Note($m) { Write-Host "       $m" -ForegroundColor DarkGray }
function Warn($m) { Write-Host "  [!]  $m" -ForegroundColor Yellow }

# 1) Python + venv + dependencies --------------------------------------------
Step '1/4  Python + dependencies'
if (-not (Test-Path $Py)) {
  $sys = Get-Command python -ErrorAction SilentlyContinue
  if (-not $sys) {
    Warn 'Python is not installed.'
    Note 'Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run setup again.'
    Start-Process 'https://www.python.org/downloads/' -ErrorAction SilentlyContinue
    return
  }
  Note 'creating virtualenv (.venv) ...'
  & $sys.Source -m venv $Venv
}
Note 'installing dependencies (this can take a minute) ...'
& $Py -m pip install --quiet --upgrade pip
& $Py -m pip install --quiet -r (Join-Path $Root 'requirements.txt')
& $Py -m pip install --quiet -r (Join-Path $Server 'requirements.txt')
& $Py -m pip install --quiet websockets
Good 'Python environment ready'

# 2) HTTPS certificate -------------------------------------------------------
Step '2/4  HTTPS certificate'
if ($NewCert -or -not (Test-Path (Join-Path $Root 'cert.pem'))) {
  Note 'generating a self-signed cert (localhost + this PC''s LAN IP) ...'
  & $Py (Join-Path $Server 'serve_https.py') gencert
} else {
  Note 'cert.pem already exists — keeping it (pass -NewCert to regenerate)'
}
& (Join-Path $Server 'cert-tool.ps1') -Trust
& (Join-Path $Server 'cert-tool.ps1') -Export (Join-Path $Root 'dev.cer')
Good 'certificate trusted on this PC; dev.cer exported for phones'
Note 'CLOCK phone: tap through the warning once.   CAMERA phone: it needs dev.cer installed'
Note '(iOS: open dev.cer, install the profile, then Settings > General > About > Certificate Trust Settings).'

# 3) Stockfish engine --------------------------------------------------------
if (-not $NoEngine) {
  Step '3/4  Stockfish engine'
  $eng = Join-Path $Server 'engines'
  $exe = Join-Path $eng 'stockfish.exe'
  if (Test-Path $exe) {
    Good "already present: $exe"
  } else {
    New-Item -ItemType Directory -Force -Path $eng | Out-Null
    try {
      Note 'finding the latest Windows build ...'
      $rel = Invoke-RestMethod 'https://api.github.com/repos/official-stockfish/Stockfish/releases/latest' -Headers @{ 'User-Agent' = 'chessmon-setup' }
      $asset = $null
      foreach ($p in 'windows-x86-64-avx2', 'windows-x86-64-sse41-popcnt', 'windows-x86-64-modern', 'windows-x86-64') {
        $asset = $rel.assets | Where-Object { $_.name -like "*$p*" -and $_.name -like '*.zip' } | Select-Object -First 1
        if ($asset) { break }
      }
      if (-not $asset) { throw 'no Windows .zip asset in the latest release' }
      $zip = Join-Path $env:TEMP $asset.name
      Note "downloading $($asset.name)  ($([math]::Round($asset.size / 1MB, 1)) MB) ..."
      Invoke-WebRequest $asset.browser_download_url -OutFile $zip
      $tmp = Join-Path $env:TEMP 'cm_sf'
      Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
      Expand-Archive $zip -DestinationPath $tmp -Force
      $bin = Get-ChildItem $tmp -Recurse -Filter '*.exe' | Select-Object -First 1
      if (-not $bin) { throw 'no .exe inside the download' }
      Copy-Item $bin.FullName $exe -Force
      Remove-Item $zip, $tmp -Recurse -Force -ErrorAction SilentlyContinue
      Good "Stockfish -> $exe"
    } catch {
      Warn "couldn't auto-download Stockfish: $($_.Exception.Message)"
      Note 'Grab a Windows build from https://stockfishchess.org/download/ and save it as:'
      Note "  $exe"
    }
  }
}

# 4) chessmon-cloud broadcast (optional) -------------------------------------
if (-not $NoCloud) {
  Step '4/4  chessmon-cloud broadcast (optional)'
  if ((Read-Host 'Broadcast games to chessmon-cloud from this PC? (y/N)') -match '^(y|yes)$') {
    $key  = (Read-Host '  event key (from your chessmon-cloud config)').Trim()
    $club = (Read-Host '  club name (shown to spectators)').Trim()
    $url  = (Read-Host '  relay URL [https://comlos.com/relay/agent.php]').Trim()
    if (-not $url) { $url = 'https://comlos.com/relay/agent.php' }
    if (-not $key) {
      Note 'no event key entered — skipped'
    } else {
      @{ key = $key; club = $club; url = $url } | ConvertTo-Json | Set-Content (Join-Path $Root 'cloud.json') -Encoding UTF8
      $agent = @('C:\Claude\chessmon-cloud\php\club_agent.py',
                 (Join-Path $Root '..\..\chessmon-cloud\php\club_agent.py')) |
               Where-Object { Test-Path $_ } | Select-Object -First 1
      if ($agent) {
        $line = '"' + $Py + '" "' + (Resolve-Path $agent).Path + '"'
        @(
          '@echo off',
          'rem chessmon -> chessmon-cloud broadcast (generated by setup). Start the chessmon server first.',
          "set RELAY_KEY=$key",
          "set CLUB_NAME=$club",
          "set RELAY_URL=$url",
          'set CHESSMON_WS=wss://127.0.0.1:8000/ws',
          ('set STOCKFISH_PATH=' + (Join-Path $Server 'engines\stockfish.exe')),
          $line,
          'pause'
        ) -join "`r`n" | Set-Content (Join-Path $Root 'broadcast.bat') -Encoding ASCII
        Good 'saved cloud.json + broadcast.bat — double-click broadcast.bat to go live (server must be running)'
      } else {
        Good 'saved cloud.json'
        Warn 'broadcast agent (club_agent.py) not found — run it from your chessmon-cloud checkout with those values.'
      }
    }
  } else {
    Note 'skipped'
  }
}

Step 'Done'
Note 'Start chessmon over HTTPS with PLAY.BAT, then open the console and add devices.'
