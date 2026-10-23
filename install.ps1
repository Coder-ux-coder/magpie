# Installs (or updates) Magpie on Windows and starts it. Nothing to answer, no administrator
# rights. Paste this into PowerShell:
#
#   irm https://raw.githubusercontent.com/Coder-ux-coder/magpie/main/install.ps1 | iex
#
# It downloads the latest release, checks the installer against the release's SHA-256,
# installs it for the current user, and waits until Magpie answers. The last line it prints
# is Magpie's address on this computer, such as http://127.0.0.1:8000.

& {
  $ErrorActionPreference = "Stop"
  $ProgressPreference = "SilentlyContinue"  # the progress bar makes downloads very slow
  [Net.ServicePointManager]::SecurityProtocol =
    [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
  if ($env:OS -ne "Windows_NT") {
    throw "The Magpie app is for Windows. For a Mac or Linux, see https://github.com/Coder-ux-coder/magpie#mac-and-linux"
  }

  $release = "https://github.com/Coder-ux-coder/magpie/releases/latest/download"
  $work = Join-Path ([IO.Path]::GetTempPath()) "magpie-install"
  New-Item -ItemType Directory -Force $work | Out-Null
  $info = Join-Path $work "latest.json"
  $setup = Join-Path $work "MagpieSetup.exe"

  Write-Host "Finding the latest Magpie..."
  Invoke-WebRequest "$release/latest.json" -OutFile $info -UseBasicParsing
  $latest = Get-Content $info -Raw | ConvertFrom-Json
  Write-Host "Downloading Magpie $($latest.version) ($([math]::Round($latest.windows.size / 1MB)) MB)..."
  Invoke-WebRequest $latest.windows.url -OutFile $setup -UseBasicParsing
  $hash = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
  if ((Get-Item $setup).Length -ne $latest.windows.size -or $hash -ne $latest.windows.sha256) {
    Remove-Item $setup -Force
    throw "The download doesn't match the release, so it wasn't installed. Run this again."
  }

  Write-Host "Installing..."
  # Wait for the installer itself: Start-Process -Wait would also wait for Magpie, which
  # the installer starts and which keeps running.
  $process = Start-Process $setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-" -PassThru
  $null = $process.Handle
  $process.WaitForExit()
  if ($process.ExitCode -ne 0) { throw "The installer stopped with code $($process.ExitCode)." }
  Remove-Item $setup -Force -ErrorAction SilentlyContinue

  Write-Host "Starting Magpie..."
  $running = Join-Path $env:LOCALAPPDATA "Magpie\running.json"
  $deadline = (Get-Date).AddMinutes(4)  # the first start can bring notes over from Docker
  while ((Get-Date) -lt $deadline) {
    if (Test-Path $running) {
      try {
        $port = (Get-Content $running -Raw | ConvertFrom-Json).port
        $address = "http://127.0.0.1:$port"
        Invoke-RestMethod "$address/healthz" -TimeoutSec 5 | Out-Null
        Write-Host "Magpie $($latest.version) is installed and running. Say ""notes on"" in any program."
        Write-Host $address
        return
      } catch {
        # not answering yet
      }
    }
    Start-Sleep -Seconds 2
  }
  throw "Magpie is installed but isn't answering yet. Open it from the Start menu; its log is in $env:LOCALAPPDATA\Magpie\logs."
}
