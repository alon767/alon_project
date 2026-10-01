param([string]$Md, [string]$Docx, [string]$Pdf)
# Markdown (the rulebook) -> RTL HTML -> Word .docx (+ PDF for checking). The questions section becomes a
# table with an empty answer column, so the architect can answer inside the document.
$lines = [System.IO.File]::ReadAllLines($Md, [System.Text.Encoding]::UTF8)

function Esc([string]$s) { return [System.Net.WebUtility]::HtmlEncode($s) }
function Inline([string]$s) {
    $h = Esc $s
    $h = [regex]::Replace($h, '\*\*(.+?)\*\*', '<b>$1</b>')
    $h = [regex]::Replace($h, '`(.+?)`', '<span class="code">$1</span>')
    return $h
}

$out = New-Object System.Text.StringBuilder
$css = @'
<style>
body { direction: rtl; font-family: Arial; font-size: 10pt; line-height: 1.35; }
p, li, td, th { direction: rtl; text-align: right; font-family: Arial; font-size: 10pt; }
h1 { font-family: Arial; font-size: 22pt; color: #1F3864; margin: 0 0 4pt 0; text-align: right; }
h2 { font-family: Arial; font-size: 15pt; color: #1F3864; border-bottom: 1.5pt solid #1F3864; padding-bottom: 2pt; margin-top: 18pt; text-align: right; }
h3 { font-family: Arial; font-size: 12.5pt; color: #2E75B6; margin-top: 12pt; text-align: right; }
table { border-collapse: collapse; width: 100%; direction: rtl; margin: 6pt 0; }
th { background: #D9E2F3; border: 1pt solid #8EA9DB; padding: 4pt; font-weight: bold; }
td { border: 1pt solid #8EA9DB; padding: 4pt; vertical-align: top; }
td.answer { background: #FFF9E6; width: 45%; }
.code { font-family: Consolas; font-size: 10pt; color: #7B2C00; }
.box { border: 1pt solid #2E75B6; background: #EEF4FB; padding: 6pt; margin: 6pt 0; }
.sub { color: #595959; font-size: 10.5pt; }
</style>
'@
[void]$out.Append('<html dir="rtl" lang="he"><head><meta charset="utf-8"><title>ספר כללים PermitAgent</title>' + $css + '</head><body dir="rtl">')

$listStack = @()      # 'ul' / 'ol' per nesting level
$inTable = $false
$tableRows = @()
$inQuestions = $false
$qRows = @()

function CloseLists {
    while ($script:listStack.Count -gt 0) {
        [void]$script:out.Append('</' + $script:listStack[-1] + '>')
        if ($script:listStack.Count -eq 1) { $script:listStack = @() } else { $script:listStack = $script:listStack[0..($script:listStack.Count - 2)] }
    }
}
function FlushTable {
    if (-not $script:inTable) { return }
    [void]$script:out.Append('<table dir="rtl">')
    $first = $true
    foreach ($r in $script:tableRows) {
        $cells = $r.Trim().Trim('|').Split('|') | ForEach-Object { $_.Trim() }
        if ($cells[0] -match '^-+$') { continue }
        [void]$script:out.Append('<tr>')
        foreach ($c in $cells) {
            if ($first) { [void]$script:out.Append('<th>' + (Inline $c) + '</th>') }
            else { [void]$script:out.Append('<td>' + (Inline $c) + '</td>') }
        }
        [void]$script:out.Append('</tr>')
        $first = $false
    }
    [void]$script:out.Append('</table>')
    $script:inTable = $false; $script:tableRows = @()
}
function FlushQuestions {
    if ($script:qRows.Count -eq 0) { return }
    [void]$script:out.Append('<table dir="rtl"><tr><th style="width:5%">#</th><th style="width:50%">שאלה</th><th>התשובה שלך</th></tr>')
    $i = 0
    foreach ($q in $script:qRows) { $i++; [void]$script:out.Append('<tr><td>' + $i + '</td><td>' + (Inline $q) + '</td><td class="answer">&nbsp;</td></tr>') }
    [void]$script:out.Append('</table>')
    $script:qRows = @()
}

foreach ($raw in $lines) {
    $line = $raw.TrimEnd()
    if ($line -match '^\|') { CloseLists; if (-not $inTable) { $inTable = $true; $tableRows = @() }; $tableRows += $line; continue }
    else { FlushTable }

    if ($line -match '^(#{1,3}) (.+)$') {
        CloseLists; FlushQuestions
        $lvl = $Matches[1].Length; $txt = $Matches[2]
        $inQuestions = ($lvl -eq 2 -and $txt -match 'שאלות מרוכזות')
        [void]$out.Append("<h$lvl dir=`"rtl`">" + (Inline $txt) + "</h$lvl>")
        if ($lvl -eq 1) {
        }
        continue
    }
    if ($line -match '^---+$') { CloseLists; continue }
    if ($inQuestions -and $line -match '^\d+\.\s+(.+)$') { $qRows += $Matches[1]; continue }

    $m = [regex]::Match($line, '^(\s*)([-*]|\d+\.)\s+(.+)$')
    if ($m.Success) {
        $depth = [int]([math]::Floor($m.Groups[1].Value.Length / 2)) + 1
        $kind = if ($m.Groups[2].Value -match '\d') { 'ol' } else { 'ul' }
        while ($listStack.Count -gt $depth) { [void]$out.Append('</' + $listStack[-1] + '>'); $listStack = @($listStack[0..($listStack.Count - 2)]) }
        while ($listStack.Count -lt $depth) { [void]$out.Append("<$kind dir=`"rtl`">"); $listStack += $kind }
        [void]$out.Append('<li dir="rtl">' + (Inline $m.Groups[3].Value) + '</li>')
        continue
    }
    CloseLists
    if ($line.Trim() -eq '') { continue }
    [void]$out.Append('<p dir="rtl">' + (Inline $line) + '</p>')
}
FlushTable; CloseLists; FlushQuestions
[void]$out.Append('</body></html>')

$html = [System.IO.Path]::ChangeExtension($Docx, '.html')
[System.IO.File]::WriteAllText($html, $out.ToString(), (New-Object System.Text.UTF8Encoding $true))

$w = New-Object -ComObject Word.Application
$w.Visible = $false
try {
    $d = $w.Documents.Open($html, $false, $true)
    # page: A4, margins 2cm, RTL section
    $d.PageSetup.PaperSize = 7; $d.PageSetup.Orientation = 1
    foreach ($p in 'TopMargin', 'BottomMargin', 'LeftMargin', 'RightMargin') { $d.PageSetup.$p = 56.7 }
    $d.Paragraphs | Out-Null
    $d.Content.ParagraphFormat.ReadingOrder = 0      # wdReadingOrderRtl
    # page numbers in the footer
    $f = $d.Sections(1).Footers(1)
    $f.PageNumbers.Add(1) | Out-Null
    $d.SaveAs2([ref]$Docx, [ref]16)
    if ($Pdf) { $d.ExportAsFixedFormat($Pdf, 17) }
    $d.Close([ref]0)
} finally {
    $w.Quit()
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($w)
}
Remove-Item $html -ErrorAction SilentlyContinue
"ok: $Docx"
