param([string]$Src, [string]$Out, [string]$TextDump)
# Hebrew in these DWFX glyphs is DOS-Hebrew (CP862) bytes shown as CP1252 chars, in visual order.
# Map each char back to its byte; 0x80-0x9A -> U+05D0.. ; swap to Arial so it renders; dump decoded text with positions.
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem, System.Web
$cp = [System.Text.Encoding]::GetEncoding(1252)
$map = @{}
for ($b = 0x80; $b -le 0x9A; $b++) {
    $heb = [char](0x05D0 + ($b - 0x80))
    $map[[char]$b] = $heb
    $s = $cp.GetString([byte[]]@($b))
    if ($s.Length -eq 1) { $map[$s[0]] = $heb }
}
Copy-Item $Src $Out -Force
$z = [System.IO.Compression.ZipFile]::Open($Out, 'Update')
$fontEntry = 'fonts/arial.ttf'
if (-not $z.GetEntry($fontEntry)) {
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($z, "$env:WINDIR\Fonts\arial.ttf", $fontEntry) | Out-Null
}
$names = $z.Entries | Where-Object { $_.FullName -like '*.fpage' } | ForEach-Object FullName
$script:dump = New-Object System.Collections.Generic.List[string]
$script:page = ''
$eval = [System.Text.RegularExpressions.MatchEvaluator] {
    param($m)
    $tag = $m.Value
    $u = [regex]::Match($tag, 'UnicodeString="([^"]*)"')
    if (-not $u.Success) { return $tag }
    $str = [System.Web.HttpUtility]::HtmlDecode($u.Groups[1].Value)
    $hit = $false
    $sb = New-Object System.Text.StringBuilder
    foreach ($ch in $str.ToCharArray()) {
        if ($map.ContainsKey($ch)) { [void]$sb.Append($map[$ch]); $hit = $true } else { [void]$sb.Append($ch) }
    }
    $rt = [regex]::Match($tag, 'RenderTransform="([^"]*)"').Groups[1].Value
    $vis = $sb.ToString()
    $chars = $vis.ToCharArray(); [array]::Reverse($chars); $logical = -join $chars
    if ($hit) { $script:dump.Add($script:page + "`t" + $logical + "`t" + $rt) }
    elseif ($str.Trim()) { $script:dump.Add($script:page + "`t" + $str + "`t" + $rt) }
    if (-not $hit) { return $tag }
    $enc = [System.Security.SecurityElement]::Escape($vis)
    $t = $tag.Replace($u.Value, 'UnicodeString="' + $enc + '"')
    $t = [regex]::Replace($t, ' Indices="[^"]*"', '')
    $t = [regex]::Replace($t, 'FontUri="[^"]*"', 'FontUri="/' + $fontEntry + '"')
    return $t
}
$eval2 = [System.Text.RegularExpressions.MatchEvaluator] {
    param($m)
    $tag = $m.Value
    $u = [regex]::Match($tag, 'UnicodeString="([^"]*)"')
    $ix = [regex]::Match($tag, ' Indices="([^"]*)"')
    if (-not $ix.Success) { return $tag }
    $ulen = 0; if ($u.Success) { $ulen = [System.Web.HttpUtility]::HtmlDecode($u.Groups[1].Value).Length }
    if (($ix.Groups[1].Value.Split(';')).Count -gt $ulen) { return $tag.Replace($ix.Value, '') }
    return $tag
}
foreach ($n in $names) {
    $script:page = ($n -split '/')[-2]
    $e = $z.GetEntry($n); $sr = New-Object IO.StreamReader($e.Open()); $t = $sr.ReadToEnd(); $sr.Close()
    $t = [regex]::Replace($t, '<Glyphs [^>]*>', $eval)
    $t = [regex]::Replace($t, '<Glyphs [^>]*>', $eval2)
    $e.Delete(); $ne = $z.CreateEntry($n)
    $sw = New-Object IO.StreamWriter($ne.Open(), (New-Object Text.UTF8Encoding $false)); $sw.Write($t); $sw.Close()
}
$z.Dispose()
[System.IO.File]::WriteAllLines($TextDump, $script:dump, (New-Object Text.UTF8Encoding $false))
"text items: " + $script:dump.Count
