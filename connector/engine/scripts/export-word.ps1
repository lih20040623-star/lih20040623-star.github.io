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
        $Document.ExportAsFixedFormat($Target, 17)
    } finally {
        if ($policy.background_suppressed) { $fill.Visible = $originalVisibility }
    }
    return $policy
}
