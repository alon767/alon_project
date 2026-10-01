param([string]$File, [string]$OutDir, [double]$Scale = 0.3, [int]$PageIndex = -1)
Add-Type -AssemblyName PresentationCore, PresentationFramework, ReachFramework, WindowsBase
New-Item -ItemType Directory -Force $OutDir | Out-Null
$tmp = Join-Path $env:TEMP ("x_" + [guid]::NewGuid().ToString() + ".xps")
Copy-Item $File $tmp
$doc = New-Object System.Windows.Xps.Packaging.XpsDocument($tmp, [System.IO.FileAccess]::Read)
$fd = $doc.GetFixedDocumentSequence().References[0].GetDocument($false)
"pages=" + $fd.Pages.Count
for ($i = 0; $i -lt $fd.Pages.Count; $i++) {
  if ($PageIndex -ge 0 -and $i -ne $PageIndex) { continue }
  $src = $fd.Pages[$i].Source
  try {
    $fp = $fd.Pages[$i].GetPageRoot($false)
    $W0 = $fp.Width; $H0 = $fp.Height
    $fp.Measure((New-Object System.Windows.Size $W0, $H0)); $fp.Arrange((New-Object System.Windows.Rect 0,0,$W0,$H0)); $fp.UpdateLayout()
    $w = [int]($W0*$Scale); $h=[int]($H0*$Scale)
    $dv = New-Object System.Windows.Media.DrawingVisual; $dc = $dv.RenderOpen()
    $dc.DrawRectangle([System.Windows.Media.Brushes]::White, $null, (New-Object System.Windows.Rect 0,0,$w,$h))
    $dc.PushTransform((New-Object System.Windows.Media.ScaleTransform $Scale,$Scale))
    $dc.DrawRectangle((New-Object System.Windows.Media.VisualBrush $fp), $null, (New-Object System.Windows.Rect 0,0,$W0,$H0))
    $dc.Pop(); $dc.Close()
    $rtb = New-Object System.Windows.Media.Imaging.RenderTargetBitmap $w,$h,96,96,([System.Windows.Media.PixelFormats]::Pbgra32)
    $rtb.Render($dv)
    $enc = New-Object System.Windows.Media.Imaging.PngBitmapEncoder; $enc.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($rtb))
    $fs=[IO.File]::Create((Join-Path $OutDir ("p{0}.png" -f $i))); $enc.Save($fs); $fs.Close()
    "page $i ok ${W0}x${H0} $src"
  } catch { "page $i FAIL $src : " + $_.Exception.InnerException.Message + $_.Exception.Message }
}
