param([string]$File, [string]$OutDir, [int]$PageIndex, [double]$Scale, [string]$Regions)
Add-Type -AssemblyName PresentationCore, PresentationFramework, ReachFramework, WindowsBase
New-Item -ItemType Directory -Force $OutDir | Out-Null
$tmp = Join-Path $env:TEMP ("x_" + [guid]::NewGuid().ToString() + ".xps")
Copy-Item $File $tmp
$doc = New-Object System.Windows.Xps.Packaging.XpsDocument($tmp, [System.IO.FileAccess]::Read)
$fd = $doc.GetFixedDocumentSequence().References[0].GetDocument($false)
$fp = $fd.Pages[$PageIndex].GetPageRoot($false)
$PW = $fp.Width; $PH = $fp.Height
$fp.Measure((New-Object System.Windows.Size $PW, $PH)); $fp.Arrange((New-Object System.Windows.Rect 0,0,$PW,$PH)); $fp.UpdateLayout()
foreach ($r in $Regions.Split(';')) {
  if (-not $r) { continue }
  $parts = $r.Split(':'); $name = $parts[0]; $v = @($parts[1].Split(',') | % { [double]$_ })
  $x0=$v[0]*$PW; $y0=$v[1]*$PH; $x1=$v[2]*$PW; $y1=$v[3]*$PH
  $w=[int](($x1-$x0)*$Scale); $h=[int](($y1-$y0)*$Scale)
  $dv = New-Object System.Windows.Media.DrawingVisual; $dc=$dv.RenderOpen()
  $dc.DrawRectangle([System.Windows.Media.Brushes]::White,$null,(New-Object System.Windows.Rect 0,0,$w,$h))
  $vb = New-Object System.Windows.Media.VisualBrush $fp
  $vb.ViewboxUnits='Absolute'; $vb.Viewbox = New-Object System.Windows.Rect $x0,$y0,($x1-$x0),($y1-$y0)
  $dc.DrawRectangle($vb,$null,(New-Object System.Windows.Rect 0,0,$w,$h)); $dc.Close()
  $rtb = New-Object System.Windows.Media.Imaging.RenderTargetBitmap $w,$h,96,96,([System.Windows.Media.PixelFormats]::Pbgra32)
  $rtb.Render($dv)
  $enc = New-Object System.Windows.Media.Imaging.PngBitmapEncoder; $enc.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($rtb))
  $fs=[IO.File]::Create((Join-Path $OutDir ($name+'.png'))); $enc.Save($fs); $fs.Close(); "$name ${w}x${h}"
}
