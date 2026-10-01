param([string]$File, [string]$OutDir, [int]$PageIndex = -1, [double]$Scale = 1.0)
Add-Type -AssemblyName PresentationCore, PresentationFramework, ReachFramework, WindowsBase, System.Printing
New-Item -ItemType Directory -Force $OutDir | Out-Null
$tmp = Join-Path $env:TEMP ("x_" + [guid]::NewGuid().ToString() + ".xps")
Copy-Item $File $tmp
$doc = New-Object System.Windows.Xps.Packaging.XpsDocument($tmp, [System.IO.FileAccess]::Read)
$seq = $doc.GetFixedDocumentSequence()
$pag = $seq.DocumentPaginator
$pag.ComputePageCount()
"pages=" + $pag.PageCount
for ($i = 0; $i -lt $pag.PageCount; $i++) {
    if ($PageIndex -ge 0 -and $i -ne $PageIndex) { continue }
    $p = $pag.GetPage($i)
    $w = [int]($p.Size.Width * $Scale); $h = [int]($p.Size.Height * $Scale)
    "page $i size " + $p.Size.Width + "x" + $p.Size.Height + " -> " + $w + "x" + $h
    $dv = New-Object System.Windows.Media.DrawingVisual
    $dc = $dv.RenderOpen()
    $dc.DrawRectangle([System.Windows.Media.Brushes]::White, $null, (New-Object System.Windows.Rect 0, 0, $w, $h))
    $dc.PushTransform((New-Object System.Windows.Media.ScaleTransform $Scale, $Scale))
    $dc.DrawRectangle((New-Object System.Windows.Media.VisualBrush $p.Visual), $null, (New-Object System.Windows.Rect 0, 0, $p.Size.Width, $p.Size.Height))
    $dc.Pop(); $dc.Close()
    $rtb = New-Object System.Windows.Media.Imaging.RenderTargetBitmap $w, $h, 96, 96, ([System.Windows.Media.PixelFormats]::Pbgra32)
    $rtb.Render($dv)
    $enc = New-Object System.Windows.Media.Imaging.PngBitmapEncoder
    $enc.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($rtb))
    $fs = [System.IO.File]::Create((Join-Path $OutDir ("page{0}.png" -f $i)))
    $enc.Save($fs); $fs.Close()
}
$doc.Close()
Remove-Item $tmp
"done"
