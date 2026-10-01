param([string]$Views = '00,01', [string]$By = 'agent', [string]$Note = '', [int]$WaitMinutes = 20)
# Continuous mode ("עבודה רציפה"): put a run request in the queue and wait for Revit's answer.
# The request holds plan names only. Prints the answer (results: png / md per plan) or "timeout".
$auto = Join-Path $env:USERPROFILE 'OneDrive - אלון מיכאלי - אדריכל\ספריית ניסיונות\עריכת בקשה להיתר\הערות\auto'
$queue = Join-Path $auto 'queue'; $done = Join-Path $auto 'done'
New-Item -ItemType Directory -Force $queue, $done | Out-Null
$status = Join-Path $auto 'watch_status.json'
if (Test-Path $status) { "status: " + [IO.File]::ReadAllText($status, [Text.Encoding]::UTF8) } else { 'status: (no status file - continuous mode was never started)' }
$name = (Get-Date -Format 'yyMMdd_HHmmss') + '_' + ($By -replace '[^\w\-]', '_') + '.json'
$req = @{ views = @($Views.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ }); by = $By; note = $Note }
$json = $req | ConvertTo-Json -Compress
[IO.File]::WriteAllText((Join-Path $queue $name), $json, (New-Object System.Text.UTF8Encoding $false))
"queued: $name  views: $Views"
$deadline = (Get-Date).AddMinutes($WaitMinutes)
while ((Get-Date) -lt $deadline) {
    $ans = Join-Path $done $name
    if (Test-Path $ans) { Start-Sleep -Milliseconds 500; 'answer:'; [IO.File]::ReadAllText($ans, [Text.Encoding]::UTF8); exit 0 }
    Start-Sleep -Seconds 5
}
"timeout after $WaitMinutes minutes - is continuous mode on in Revit? (the request stays in the queue)"
exit 1
