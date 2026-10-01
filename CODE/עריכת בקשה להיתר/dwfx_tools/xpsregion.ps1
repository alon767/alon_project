param([string]$File, [string]$OutDir, [int]$PageIndex, [double]$Scale, [string]$Regions)
# Regions: "name:x0,y0,x1,y1;..." as fractions of page
Add-Type -AssemblyName PresentationCore, PresentationFramework, ReachFramework, WindowsBase
New-Item -ItemType Directory -Force $OutDir | Out-Null
$tmp = Join-Path $env:TEMP ("x_" + [guid]::NewGuid().ToString() + ".xps")
Copy-Item $File $tmp
$doc = New-Object System.Windows.Xps.Packaging.XpsDocument($tmp, [System.IO.FileAccess]::Read)
$pag = $doc.GetFixedDocumentSequence().DocumentPaginator
$p = $pag.GetPage($PageIndex)
$PW = $p.Size.Width; $PH = $p.Size.Height
$cv = New-Object System.Windows.Media.ContainerVisual
$cv.Children.Add($p.Visual)
foreach ($r in $Regions.Split(';')) {
    if (-not $r) { continue }
    $parts = $r.Split(":"); $name = $parts[0]; $v = @($parts[1].Split(",") | ForEach-Object { [double]$_ })
    $x0 = $v[0] * $PW; $y0 = $v[1] * $PH; $x1 = $v[2] * $PW; $y1 = $v[3] * $PH
    $w = [int](($x1 - $x0) * $Scale); $h = [int](($y1 - $y0) * $Scale)
    $tg = New-Object System.Windows.Media.TransformGroup
    $tg.Children.Add((New-Object System.Windows.Media.TranslateTransform (-$x0), (-$y0)))
    $tg.Children.Add((New-Object System.Windows.Media.ScaleTransform $Scale, $Scale))
    $cv.Transform = $tg
    $bg = New-Object System.Windows.Media.DrawingVisual
    $dc = $bg.RenderOpen(); $dc.DrawRectangle([System.Windows.Media.Brushes]::White, $null, (New-Object System.Windows.Rect 0, 0, $w, $h)); $dc.Close()
    $rtb = New-Object System.Windows.Media.Imaging.RenderTargetBitmap $w, $h, 96, 96, ([System.Windows.Media.PixelFormats]::Pbgra32)
    $rtb.Render($bg); $rtb.Render($cv)
    $enc = New-Object System.Windows.Media.Imaging.PngBitmapEncoder
    $enc.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($rtb))
    $fs = [System.IO.File]::Create((Join-Path $OutDir ($name + ".png"))); $enc.Save($fs); $fs.Close()
    "$name ${w}x${h}"
}
$doc.Close(); Remove-Item $tmp
