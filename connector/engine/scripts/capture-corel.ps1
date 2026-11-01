function Get-CorelShapeInventory {
    param($Shapes, $Page, $App, $Document, [int]$Depth = 0)
    if ($Depth -gt 32) { throw 'Shape nesting exceeds inventory limit.' }
    foreach ($shape in $Shapes) {
        if ($shape.Layer.Visible -eq $false -or $shape.Layer.Printable -eq $false) { continue }
        $left = [double]$shape.LeftX
        $bottom = [double]$shape.BottomY
        $width = [double]$shape.SizeWidth
        $height = [double]$shape.SizeHeight
        if ($left + $width -lt $Page.LeftX -or $left -gt ($Page.LeftX + $Page.SizeWidth) -or $bottom + $height -lt $Page.BottomY -or $bottom -gt ($Page.BottomY + $Page.SizeHeight)) { continue }
        $item = @{ type=[int]$shape.Type; box=@((($left-$Page.LeftX)/$Page.SizeWidth), (1-($bottom+$height-$Page.BottomY)/$Page.SizeHeight), ($width/$Page.SizeWidth), ($height/$Page.SizeHeight)) }
        if ([int]$shape.Type -eq 6) {
            $item.text = [string]$shape.Text.Story.Text
            try { $item.font = [string]$shape.Text.Story.Font } catch {}
        }
        Write-Output $item
        if ([int]$shape.Type -eq 7) {
            Get-CorelShapeInventory -Shapes $shape.Shapes -Page $Page -App $App -Document $Document -Depth ($Depth+1)
        }
    }
}

function Capture-CorelSource {
    param($App, $Document, [string]$Folder)
    $previewDirectory = Join-Path $Folder 'previews'
    [void][IO.Directory]::CreateDirectory($previewDirectory)
    $result = @{ capture_kind='coreldraw-raster'; capture_scope='visible-printable-pages'; capture_complete=$true; source_pages=@(); capture_errors=@(); text=''; pages=[int]$Document.Pages.Count }
    $pageLimit = [Math]::Min(100, $Document.Pages.Count)
    if ($Document.Pages.Count -gt 100) { $result.capture_complete=$false; $result.capture_errors += 'Source preview limited to 100 pages.' }
    for ($index=1; $index -le $pageLimit; $index++) {
        $page = $Document.Pages.Item($index)
        $page.Activate()
        $widthPoints = [double]$App.ConvertUnits($page.SizeWidth, $Document.Unit, 14)
        $heightPoints = [double]$App.ConvertUnits($page.SizeHeight, $Document.Unit, 14)
        $dpi = [Math]::Max(1,[int][Math]::Floor([Math]::Min(120, 2400 * 72 / [Math]::Max($widthPoints, $heightPoints))))
        $width = [int][Math]::Ceiling($widthPoints * $dpi / 72)
        $height = [int][Math]::Ceiling($heightPoints * $dpi / 72)
        $entry = @{ page=$index; width_points=$widthPoints; height_points=$heightPoints; width=$width; height=$height; dpi=$dpi; image="source-$index.png"; objects=@(); inventory_complete=$true }
        try {
            $entry.objects = @(Get-CorelShapeInventory -Shapes $page.Shapes -Page $page -App $App -Document $Document)
            $result.text += (($entry.objects | Where-Object { $_.ContainsKey('text') } | ForEach-Object { $_.text }) -join "`n") + "`n"
        } catch { $entry.inventory_complete=$false; $result.capture_complete=$false; $result.capture_errors += "Page $index inventory failed: $($_.Exception.Message)" }
        try {
            # Explicit page rectangle avoids cropping to artwork bounds.
            $area = $App.CreateRect($page.LeftX, $page.BottomY, $page.SizeWidth, $page.SizeHeight)
            $palette = $App.CreateStructPaletteOptions()
            $filter = $Document.ExportBitmap((Join-Path $previewDirectory $entry.image), 802, 1, 4, $width, $height, [int]$dpi, [int]$dpi, 1, $false, $false, $true, $false, 0, $palette, $area)
            $filter.Finish()
            [void][Runtime.InteropServices.Marshal]::ReleaseComObject($filter)
        } catch { $entry.Remove('image'); $result.capture_complete=$false; $result.capture_errors += "Page $index preview failed: $($_.Exception.Message)" }
        $result.source_pages += $entry
    }
    return $result
}
