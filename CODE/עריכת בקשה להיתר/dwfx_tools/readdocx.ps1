param([string]$Docx, [string]$Out)
# read the rulebook back: full text by paragraph (with the heading it sits under), every highlighted range,
# the answers table, tracked changes and comments. Read-only - the file is opened read-only.
$w = New-Object -ComObject Word.Application
$w.Visible = $false
$sb = New-Object System.Text.StringBuilder
try {
    $d = $w.Documents.Open($Docx, $false, $true)
    [void]$sb.AppendLine("=== revisions: " + $d.Revisions.Count + " | comments: " + $d.Comments.Count)
    foreach ($r in $d.Revisions) { [void]$sb.AppendLine("REV type=" + $r.Type + " : " + $r.Range.Text) }
    foreach ($c in $d.Comments) { [void]$sb.AppendLine("COMMENT on [" + $c.Scope.Text + "] : " + $c.Range.Text) }

    [void]$sb.AppendLine("=== highlighted ranges (with the heading above them)")
    $heading = ''
    foreach ($p in $d.Paragraphs) {
        $lvl = $p.OutlineLevel
        $t = $p.Range.Text.Trim()
        if ($lvl -le 3 -and $t) { $heading = $t }
        $hl = $p.Range.HighlightColorIndex
        if ($hl -ne 0) {
            # 9999999 = mixed: collect the highlighted runs inside the paragraph
            if ($hl -eq 9999999) {
                $parts = @()
                foreach ($wd in $p.Range.Words) { if ($wd.HighlightColorIndex -ne 0) { $parts += $wd.Text } }
                [void]$sb.AppendLine("[" + $heading + "] (part) " + (-join $parts).Trim() + "   || in: " + $t)
            } else {
                [void]$sb.AppendLine("[" + $heading + "] " + $t)
            }
        }
    }
    [void]$sb.AppendLine("=== tables")
    $ti = 0
    foreach ($tb in $d.Tables) {
        $ti++
        [void]$sb.AppendLine("--- table $ti rows=" + $tb.Rows.Count)
        foreach ($row in $tb.Rows) {
            $cells = @()
            foreach ($c in $row.Cells) { $cells += ($c.Range.Text -replace "[\r\a]+", ' ').Trim() }
            [void]$sb.AppendLine(($cells -join ' | '))
        }
    }
    [void]$sb.AppendLine("=== full text")
    foreach ($p in $d.Paragraphs) { $t = $p.Range.Text.Trim(); if ($t) { [void]$sb.AppendLine($t) } }
    $d.Close([ref]0)
} finally {
    $w.Quit()
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($w)
}
[System.IO.File]::WriteAllText($Out, $sb.ToString(), (New-Object System.Text.UTF8Encoding $true))
"written: $Out"
