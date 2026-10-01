param([string]$File)
# Hebrew runs that still use an embedded (subset) font render as boxes: switch them to Arial, drop the glyph
# indices, and write the string in visual order (reverse, but keep digit / latin runs readable).
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem, System.Web
$z = [System.IO.Compression.ZipFile]::Open($File, 'Update')
$fontEntry = 'fonts/arial.ttf'
if (-not $z.GetEntry($fontEntry)) {
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($z, "$env:WINDIR\Fonts\arial.ttf", $fontEntry) | Out-Null
}
$script:n = 0
$eval = [System.Text.RegularExpressions.MatchEvaluator] {
    param($m)
    $tag = $m.Value
    if ($tag -match 'FontUri="/fonts/arial.ttf"') { return $tag }
    $u = [regex]::Match($tag, 'UnicodeString="([^"]*)"')
    if (-not $u.Success) { return $tag }
    $str = [System.Web.HttpUtility]::HtmlDecode($u.Groups[1].Value)
    if ($str -notmatch '[א-ת]') { return $tag }
    # visual order: split into runs; reverse the run order, reverse the letters of Hebrew/other runs only
    $runs = [regex]::Matches($str, '[0-9A-Za-z.,:/%+\-]+|[^0-9A-Za-z.,:/%+\-]+')
    $parts = @()
    foreach ($r in $runs) {
        $v = $r.Value
        if ($v -match '^[0-9A-Za-z.,:/%+\-]+$') { $parts += $v }
        else { $c = $v.ToCharArray(); [array]::Reverse($c); $parts += (-join $c) }
    }
    [array]::Reverse($parts)
    $vis = -join $parts
    $script:n++
    $t = $tag.Replace($u.Value, 'UnicodeString="' + [System.Security.SecurityElement]::Escape($vis) + '"')
    $t = [regex]::Replace($t, ' Indices="[^"]*"', '')
    $t = [regex]::Replace($t, 'FontUri="[^"]*"', 'FontUri="/' + $fontEntry + '"')
    return $t
}
$names = $z.Entries | Where-Object { $_.FullName -like '*.fpage' } | ForEach-Object FullName
foreach ($nm in $names) {
    $e = $z.GetEntry($nm); $sr = New-Object IO.StreamReader($e.Open()); $t = $sr.ReadToEnd(); $sr.Close()
    $t2 = [regex]::Replace($t, '<Glyphs [^>]*>', $eval)
    $e.Delete(); $ne = $z.CreateEntry($nm)
    $sw = New-Object IO.StreamWriter($ne.Open(), (New-Object Text.UTF8Encoding $false)); $sw.Write($t2); $sw.Close()
}
$z.Dispose()
"hebrew runs switched: " + $script:n
