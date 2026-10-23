# Records Windows' own voice saying a voice note, for the app's self-test:
#   powershell -File packaging\windows\speak.ps1 -Path build\note.wav
# The listener has to hear "notes on", take down the note, and stop at "notes off".
param(
  [Parameter(Mandatory = $true)][string]$Path,
  [string]$Note = "remind me to buy milk tomorrow at nine"
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
  16000,
  [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
  [System.Speech.AudioFormat.AudioChannel]::Mono
)
$voice.SetOutputToWaveFile([System.IO.Path]::GetFullPath($Path), $format)
$voice.Rate = -1
$prompt = New-Object System.Speech.Synthesis.PromptBuilder
$prompt.AppendBreak([TimeSpan]::FromMilliseconds(800))
$prompt.AppendText("Notes on.")
$prompt.AppendBreak([TimeSpan]::FromMilliseconds(600))
$prompt.AppendText("$Note.")
$prompt.AppendBreak([TimeSpan]::FromMilliseconds(700))
$prompt.AppendText("Notes off.")
$prompt.AppendBreak([TimeSpan]::FromMilliseconds(800))
$voice.Speak($prompt)
Write-Host "Recorded '$Note' with the voice $($voice.Voice.Name) to $Path"
$voice.Dispose()
