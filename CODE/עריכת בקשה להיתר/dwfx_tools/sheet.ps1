param([string]$Dir, [int]$From, [int]$To, [string]$Out, [int]$Cols = 2, [int]$CellW = 1000)
Add-Type -AssemblyName System.Drawing
$files = @(); for ($i = $From; $i -le $To; $i++) { $p = Join-Path $Dir ("p{0:D3}.png" -f $i); if (Test-Path $p) { $files += $p } }
$first = [System.Drawing.Image]::FromFile($files[0]); $ratio = $first.Height / $first.Width; $first.Dispose()
$cellH = [int]($CellW * $ratio); $rows = [Math]::Ceiling($files.Count / $Cols)
$bmp = New-Object System.Drawing.Bitmap ($Cols * $CellW), ($rows * $cellH)
$g = [System.Drawing.Graphics]::FromImage($bmp); $g.Clear([System.Drawing.Color]::White)
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
for ($k = 0; $k -lt $files.Count; $k++) {
    $im = [System.Drawing.Image]::FromFile($files[$k])
    $g.DrawImage($im, ($k % $Cols) * $CellW, [Math]::Floor($k / $Cols) * $cellH, $CellW, $cellH); $im.Dispose()
    $g.DrawRectangle([System.Drawing.Pens]::Gray, ($k % $Cols) * $CellW, [Math]::Floor($k / $Cols) * $cellH, $CellW - 1, $cellH - 1)
}
$g.Dispose(); $bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
