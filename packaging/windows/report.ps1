# Shows a self-test's results, and what the app logged, as annotations on the workflow
# run, so they can be read on the run's summary page without downloading anything.
#   ./packaging/windows/report.ps1 -Report build/selftest.json -Title "Built app"
#   ./packaging/windows/report.ps1 -Log "$env:LOCALAPPDATA\Magpie\logs\magpie.log"
param(
  [string]$Report = "",
  [string]$Log = "",
  [string]$Title = "Self-test"
)

function Annotate($kind, $title, $message) {
  # Workflow commands: one line, with % escaped; titles without commas or colons.
  $message = ($message -replace "%", "%25") -replace "`r|`n", " "
  $title = ($title -replace "[,:]", " ")
  Write-Host "::$kind title=$title::$message"
}

if ($Report -and (Test-Path $Report)) {
  $results = Get-Content $Report -Raw | ConvertFrom-Json
  foreach ($check in $results.checks) {
    $kind = if ($check.ok) { "notice" } elseif ($check.required) { "error" } else { "warning" }
    Annotate $kind "$Title - $($check.name)" $check.detail
  }
}

if ($Log -and (Test-Path $Log)) {
  $lines = Get-Content $Log
  $lines | Select-String -Pattern "starting \(|is ready on|Time zone set|Brought over" |
    Select-Object -First 4 | ForEach-Object { Annotate "notice" "Installed app's log" $_.Line }
  $lines | Select-String -Pattern " (WARNING|ERROR|CRITICAL) " |
    Select-Object -First 6 | ForEach-Object { Annotate "warning" "Installed app's log" $_.Line }
}
