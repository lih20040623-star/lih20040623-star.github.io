function Export-WordPdf {
    param($App, $Document, [string]$Target)
    $policy = @{print_backgrounds=$null; background_suppressed=$false}
    # Some Word versions export a legacy document's hidden page color even
    # with PrintBackgrounds disabled. Match the user's print setting explicitly.
    # Only change this read-only task document in memory, never Word's options.
    try {
        $setting = $App.Options.PrintBackgrounds
        if ($null -eq $setting) { $policy.setting_unavailable = $true }
        else { $policy.print_backgrounds = [bool]$setting }
    }
    catch { $policy.setting_unavailable = $true } # Some WPS versions omit it.
    $fill = $null
    $originalVisibility = $null
    try {
        if ($policy.print_backgrounds -eq $false) {
            $fill = $Document.Background.Fill
            $originalVisibility = [int]$fill.Visible
            if ($originalVisibility -ne 0) {
                $fill.Visible = 0
                $policy.background_suppressed = $true
            }
        }
        # The legacy API downsamples images even when DOCX auto-compression is
        # disabled. The quality argument belongs to ExportAsFixedFormat2.
        if($Document | Get-Member -Name ExportAsFixedFormat2) {
            $Document.ExportAsFixedFormat2($Target,17,$false,0,0,1,1,0,$true,$true,0,$true,$true,$false,$true)
            $policy.export_method='ExportAsFixedFormat2'
        } else {
            # The Python quality gate restores and verifies original source pixels
            # for older renderers too; no unverified output is published.
            $Document.ExportAsFixedFormat($Target,17,$false,0)
            $policy.export_method='ExportAsFixedFormat'
        }
        $policy.image_quality='source-pixel-verification-required'
    } finally {
        if ($policy.background_suppressed) { $fill.Visible = $originalVisibility }
    }
    return $policy
}
