param([string]$File)
# remove the raster images (Path elements filled with an ImageBrush) - the WPF renderer cannot load the
# embedded .tif / relative .png parts; the drawings themselves are vector
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$z = [System.IO.Compression.ZipFile]::Open($File, 'Update')
$n = 0
$names = $z.Entries | Where-Object { $_.FullName -like '*.fpage' } | ForEach-Object FullName
foreach ($nm in $names) {
    $e = $z.GetEntry($nm); $sr = New-Object IO.StreamReader($e.Open()); $t = $sr.ReadToEnd(); $sr.Close()
    $before = $t.Length
    $t2 = [regex]::Replace($t, '<Path\b[^>]*?(?<!/)>(?:(?!</Path>).)*?<ImageBrush\b.*?</Path>', '', 'Singleline')
    $t2 = [regex]::Replace($t2, '<Path\b[^>]*ImageBrush[^>]*/>', '', 'Singleline')
    $t2 = [regex]::Replace($t2, '<ImageBrush\b[^>]*/>', '', 'Singleline')
    $t2 = [regex]::Replace($t2, '<ImageBrush\b.*?</ImageBrush>', '', 'Singleline')
    if ($t2.Length -ne $before) {
        $n++
        $e.Delete(); $ne = $z.CreateEntry($nm)
        $sw = New-Object IO.StreamWriter($ne.Open(), (New-Object Text.UTF8Encoding $false)); $sw.Write($t2); $sw.Close()
    }
}
$z.Dispose()
"pages cleaned: $n"
