function Export-CorelPdfOriginal {
    param($Document,[string]$Target)
    $settings=$Document.PDFSettings
    $original=@{}
    $desired=@{DownsampleColor=$false;DownsampleGray=$false;DownsampleMono=$false;BitmapCompression=3;ComplexFillsAsBitmaps=$false;EmbedFonts=$true}
    try {
        foreach($key in $desired.Keys) {
            $original[$key]=$settings.$key
            $settings.$key=$desired[$key]
            if($settings.$key -ne $desired[$key]){throw "IMAGE_QUALITY_UNSUPPORTED: CorelDRAW could not disable $key."}
        }
        $Document.PublishToPDF($Target)
    } finally {
        foreach($key in $original.Keys){$settings.$key=$original[$key]}
    }
    return @{policy='original-pixels';downsample_color=$false;downsample_gray=$false;downsample_mono=$false;compression='lossless-ZIP'}
}

function Get-CorelFonts {
    param($Shapes,[int]$Depth=0)
    if($Depth -gt 32){throw 'FONT_CHECK_FAILED: CorelDRAW shape nesting exceeds the font inspection limit.'}
    foreach($shape in $Shapes) {
        if($shape.Layer.Visible -eq $false -or $shape.Layer.Printable -eq $false){continue}
        if([int]$shape.Type -eq 6) {
            foreach($character in $shape.Text.Story.Characters) {
                $font=[string]$character.Font
                if($font){Write-Output $font}
            }
        } elseif([int]$shape.Type -eq 7) {Get-CorelFonts $shape.Shapes ($Depth+1)}
    }
}
