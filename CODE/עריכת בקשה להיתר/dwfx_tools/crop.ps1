param([string]$Img, [string]$OutPrefix, [double]$X0, [double]$X1, [double]$Y0, [double]$Y1, [int]$Strips)
Add-Type -AssemblyName System.Drawing
$src = [System.Drawing.Image]::FromFile($Img)
$W = $src.Width; $H = $src.Height
$x = [int]($X0 * $W); $w = [int](($X1 - $X0) * $W)
$ys = $Y0 * $H; $h = (($Y1 - $Y0) * $H) / $Strips
for ($k = 0; $k -lt $Strips; $k++) {
    $rect = New-Object System.Drawing.Rectangle $x, ([int]($ys + $k * $h)), $w, ([int]$h)
    $bmp = New-Object System.Drawing.Bitmap $rect.Width, $rect.Height
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.DrawImage($src, (New-Object System.Drawing.Rectangle 0, 0, $rect.Width, $rect.Height), $rect, [System.Drawing.GraphicsUnit]::Pixel)
    $g.Dispose()
    $bmp.Save(("{0}_{1}.png" -f $OutPrefix, $k), [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
}
$src.Dispose()
