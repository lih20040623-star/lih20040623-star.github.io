function Capture-WordSource {
    param($Document, [string]$Folder)
    $result=@{ capture_kind='word-emf'; capture_scope='visible-printable-pages'; capture_complete=$true; source_pages=@(); capture_errors=@(); text=[string]$Document.Content.Text; pages=[int]$Document.ComputeStatistics(2) }
    $directory=Join-Path $Folder 'previews'
    [void][IO.Directory]::CreateDirectory($directory)
    try {
        Add-Type -AssemblyName System.Drawing
        $Document.Repaginate()
        $Document.ActiveWindow.View.Type=3
        $pages=$Document.ActiveWindow.Panes.Item(1).Pages
        if ($pages.Count -ne $result.pages) { $result.capture_complete=$false; $result.capture_errors+='Word page view does not expose every page.' }
        for ($index=1; $index -le [Math]::Min(100,$pages.Count); $index++) {
            $page=$pages.Item($index)
            $dpi=[Math]::Max(1,[int][Math]::Floor([Math]::Min(120,2400*72/[Math]::Max($page.Width,$page.Height))))
            $width=[int][Math]::Ceiling($page.Width*$dpi/72)
            $height=[int][Math]::Ceiling($page.Height*$dpi/72)
            $memory=[IO.MemoryStream]::new([byte[]]$page.EnhMetaFileBits)
            try {
                $metafile=[Drawing.Imaging.Metafile]::new($memory)
                try {
                    $bitmap=[Drawing.Bitmap]::new($width,$height)
                    try {
                        $graphics=[Drawing.Graphics]::FromImage($bitmap)
                        try { $graphics.Clear([Drawing.Color]::White); $graphics.DrawImage($metafile,0,0,$width,$height) }
                        finally { $graphics.Dispose() }
                        $bitmap.Save((Join-Path $directory "source-$index.png"),[Drawing.Imaging.ImageFormat]::Png)
                    } finally { $bitmap.Dispose() }
                } finally { $metafile.Dispose() }
            } finally { $memory.Dispose() }
            $result.source_pages+=@{page=$index;width_points=[double]$page.Width;height_points=[double]$page.Height;width=$width;height=$height;dpi=$dpi;image="source-$index.png";objects=@();inventory_complete=$true}
        }
        if ($result.pages -gt 100) { $result.capture_complete=$false; $result.capture_errors+='Source preview limited to 100 pages.' }
    } catch { $result.capture_complete=$false; $result.capture_errors+=('Word page capture unavailable: '+$_.Exception.Message) }
    return $result
}

function Capture-PresentationSource {
    param($Document,[string]$Folder)
    $result=@{capture_kind='powerpoint-raster';capture_scope='slides';capture_complete=$true;source_pages=@();capture_errors=@();text='';pages=[int]$Document.Slides.Count}
    $directory=Join-Path $Folder 'previews'
    [void][IO.Directory]::CreateDirectory($directory)
    $widthPoints=[double]$Document.PageSetup.SlideWidth
    $heightPoints=[double]$Document.PageSetup.SlideHeight
    $width=[int][Math]::Ceiling($widthPoints*120/72)
    $height=[int][Math]::Ceiling($heightPoints*120/72)
    $scale=[Math]::Min(1,2400/[Math]::Max($width,$height))
    $width=[int]($width*$scale); $height=[int]($height*$scale)
    for ($index=1; $index -le [Math]::Min(100,$Document.Slides.Count); $index++) {
        $slide=$Document.Slides.Item($index)
        $entry=@{page=$index;width_points=$widthPoints;height_points=$heightPoints;width=$width;height=$height;image="source-$index.png";objects=@();inventory_complete=$true}
        try {
            foreach ($shape in $slide.Shapes) {
                $item=@{type=[int]$shape.Type;box=@(($shape.Left/$widthPoints),($shape.Top/$heightPoints),($shape.Width/$widthPoints),($shape.Height/$heightPoints))}
                if ($shape.HasTextFrame -eq -1) { $item.text=[string]$shape.TextFrame.TextRange.Text; $result.text+=$item.text+"`n" }
                $entry.objects+=$item
            }
            $slide.Export((Join-Path $directory $entry.image),'PNG',$width,$height)
        } catch { $result.capture_complete=$false; $entry.Remove('image'); $result.capture_errors+=('Slide '+$index+': '+$_.Exception.Message) }
        $result.source_pages+=$entry
    }
    if ($Document.Slides.Count -gt 100) { $result.capture_complete=$false; $result.capture_errors+='Source preview limited to 100 slides.' }
    return $result
}

function Capture-WorkbookInventory {
    param($App,$Document)
    $result=@{capture_kind='workbook-inventory';inventory_complete=$true;sheet_inventory=@();text='';capture_errors=@()}
    $visited=0
    foreach ($sheet in $Document.Worksheets) {
        $entry=@{name=[string]$sheet.Name;visible=($sheet.Visible -eq -1);print_area=[string]$sheet.PageSetup.PrintArea;cells=@();shapes=[int]$sheet.Shapes.Count;charts=[int]$sheet.ChartObjects().Count;outside_print_area=0}
        if ($entry.visible) {
            if ($sheet.UsedRange.Cells.Count -gt 50000) { $result.inventory_complete=$false; $result.capture_errors+=('Sheet '+$entry.name+' exceeds 50000-cell inventory limit.') }
            else {
                foreach ($cell in $sheet.UsedRange.Cells) {
                    if ($visited -ge 50000) { $result.inventory_complete=$false; break }
                    $visited++
                    if ($null -eq $cell.Value2 -or $cell.EntireRow.Hidden -or $cell.EntireColumn.Hidden) { continue }
                    if ($entry.print_area -and $null -eq $App.Intersect($cell,$sheet.Range($entry.print_area))) { $entry.outside_print_area++; continue }
                    $display=[string]$cell.Text
                    $entry.cells+=@{address=[string]$cell.Address();text=$display}
                    $result.text+=$display+"`n"
                }
            }
        }
        $result.sheet_inventory+=$entry
    }
    return $result
}
