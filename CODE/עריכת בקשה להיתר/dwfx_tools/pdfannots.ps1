param([string]$Pdf)
# the text of the comments (annotations) in a PDF: /Contents of every annotation, also inside compressed
# object streams; with the /Rect so they can be matched to the drawing. Read-only.
Add-Type -AssemblyName System.IO.Compression
$bytes = [System.IO.File]::ReadAllBytes($Pdf)
$latin = [System.Text.Encoding]::GetEncoding(28591)
$text = $latin.GetString($bytes)
$chunks = New-Object System.Collections.Generic.List[string]
$chunks.Add($text)
# inflate every FlateDecode stream (object streams hold the annotation dictionaries)
$rx = [regex]'stream\r?\n'
$pos = 0
while (($m = $rx.Match($text, $pos)).Success) {
    $start = $m.Index + $m.Length
    $end = $text.IndexOf('endstream', $start)
    if ($end -lt 0) { break }
    $pos = $end + 9
    $head = $text.Substring([Math]::Max(0, $m.Index - 400), [Math]::Min(400, $m.Index))
    if ($head -notmatch 'FlateDecode') { continue }
    try {
        $ms = New-Object System.IO.MemoryStream($bytes, $start + 2, $end - $start - 2)
        $ds = New-Object System.IO.Compression.DeflateStream($ms, [System.IO.Compression.CompressionMode]::Decompress)
        $out = New-Object System.IO.MemoryStream
        $ds.CopyTo($out)
        $s = $latin.GetString($out.ToArray())
        if ($s -match '/Annot|/Contents|/Subj') { $chunks.Add($s) }
    } catch { }
}
function Decode-PdfString([string]$raw) {
    # hex <FEFF...> or literal (\xFE\xFF...) UTF-16BE, or PDFDoc
    if ($raw.StartsWith('<')) {
        $h = $raw.Trim('<', '>') -replace '\s', ''
        $b = New-Object byte[] ($h.Length / 2)
        for ($i = 0; $i -lt $b.Length; $i++) { $b[$i] = [Convert]::ToByte($h.Substring($i * 2, 2), 16) }
    } else {
        $s = $raw.Substring(1, $raw.Length - 2)
        $s = [regex]::Replace($s, '\\([0-7]{1,3})', { param($x) [char][Convert]::ToInt32($x.Groups[1].Value, 8) })
        $s = $s -replace '\\n', "`n" -replace '\\r', "`r" -replace '\\t', "`t" -replace '\\\(', '(' -replace '\\\)', ')' -replace '\\\\', '\'
        $b = $latin.GetBytes($s)
    }
    if ($b.Length -ge 2 -and $b[0] -eq 0xFE -and $b[1] -eq 0xFF) { return [System.Text.Encoding]::BigEndianUnicode.GetString($b, 2, $b.Length - 2) }
    return [System.Text.Encoding]::UTF8.GetString($b)
}
$n = 0
foreach ($c in $chunks) {
    foreach ($m in [regex]::Matches($c, '/Contents\s*(\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f\s]+>)')) {
        $ctx = $c.Substring([Math]::Max(0, $m.Index - 600), [Math]::Min(1200, $c.Length - [Math]::Max(0, $m.Index - 600)))
        if ($ctx -notmatch '/Annot|/Subtype\s*/(Text|FreeText|Popup|Square|Circle|Line|Polygon|Ink|Highlight)') { continue }
        $rect = [regex]::Match($ctx, '/Rect\s*\[([^\]]+)\]').Groups[1].Value
        $sub = [regex]::Match($ctx, '/Subtype\s*/(\w+)').Groups[1].Value
        $n++
        "#$n [$sub] rect=[$rect]"
        "   " + (Decode-PdfString $m.Groups[1].Value)
    }
}
"annotations with text: $n"
