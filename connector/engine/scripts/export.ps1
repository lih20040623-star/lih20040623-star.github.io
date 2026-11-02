param([Parameter(Mandatory=$true)][string]$RequestPath)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$source = [string]$request.source
$target = [string]$request.target
$ext = [IO.Path]::GetExtension($source).ToLowerInvariant()
$engine = [string]$request.engine
$app = $null
$document = $null
$owned = $false
$evidence = @{}
. (Join-Path $PSScriptRoot 'capture-corel.ps1')
. (Join-Path $PSScriptRoot 'capture-office.ps1')
. (Join-Path $PSScriptRoot 'export-word.ps1')
. (Join-Path $PSScriptRoot 'word-fonts.ps1')
. (Join-Path $PSScriptRoot 'export-quality.ps1')

function Connect-CorelDraw {
    try { return [Runtime.InteropServices.Marshal]::GetActiveObject('CorelDRAW.Application') }
    catch {}
    # Some CorelDRAW installations fail on a cold /Automation activation.
    # Launch the installed executable normally, wait for initialization, then
    # connect through COM. Do not close an existing user application.
    $progid = 'CorelDRAW.Application'
    $versionKey = Get-ItemProperty -LiteralPath 'Registry::HKEY_CLASSES_ROOT\CorelDRAW.Application\CurVer' -ErrorAction SilentlyContinue
    if ($versionKey -and $versionKey.'(default)') { $progid = [string]$versionKey.'(default)' }
    $clsid = (Get-ItemProperty -LiteralPath ('Registry::HKEY_CLASSES_ROOT\' + $progid + '\CLSID') -ErrorAction Stop).'(default)'
    $command = (Get-ItemProperty -LiteralPath ('Registry::HKEY_CLASSES_ROOT\CLSID\' + $clsid + '\LocalServer32') -ErrorAction Stop).'(default)'
    $match = [regex]::Match($command, '^\s*"([^"]+\.exe)"|^\s*(.+?\.exe)(?:\s|$)', [Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if (-not $match.Success) { throw 'COREL_START_FAILED: Registered CorelDRAW executable path is invalid.' }
    $executable = if ($match.Groups[1].Success) { $match.Groups[1].Value } else { $match.Groups[2].Value }
    if (-not (Test-Path -LiteralPath $executable)) { throw 'COREL_NOT_INSTALLED: Registered CorelDRAW executable is missing.' }
    $running = Get-Process -Name CorelDRW -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $executable }
    if (-not $running) {
        $launched = Start-Process -FilePath $executable -WindowStyle Hidden -PassThru
        try { [void]$launched.WaitForInputIdle(30000) } catch {}
    }
    $lastError = $null
    for ($attempt = 0; $attempt -lt 4; $attempt++) {
        try { return New-Object -ComObject $progid }
        catch { $lastError = $_; Start-Sleep -Milliseconds 750 }
    }
    throw ('COREL_START_FAILED: ' + $lastError.Exception.Message)
}

function Connect-Illustrator {
    $lastError=$null
    for($attempt=0;$attempt -lt 20;$attempt++) {
        try {
            $candidate=[Runtime.InteropServices.Marshal]::GetActiveObject('Illustrator.Application')
            if($candidate.Version){return $candidate}
        } catch {$lastError=$_}
        if($attempt -eq 0) {
            $clsid=(Get-ItemProperty -LiteralPath 'Registry::HKEY_CLASSES_ROOT\Illustrator.Application\CLSID').'(default)'
            $command=(Get-ItemProperty -LiteralPath ('Registry::HKEY_CLASSES_ROOT\CLSID\'+$clsid+'\LocalServer32')).'(default)'
            $match=[regex]::Match($command,'^\s*"([^"]+\.exe)"|^\s*(.+?\.exe)(?:\s|$)',[Text.RegularExpressions.RegexOptions]::IgnoreCase)
            $executable=if($match.Groups[1].Success){$match.Groups[1].Value}else{$match.Groups[2].Value}
            if(-not(Test-Path -LiteralPath $executable)){throw 'ILLUSTRATOR_PATH_INVALID: Registered executable is missing.'}
            if(-not(Get-Process -Name Illustrator -ErrorAction SilentlyContinue)) {
                $launched=Start-Process -FilePath $executable -WindowStyle Hidden -PassThru
                try{[void]$launched.WaitForInputIdle(30000)}catch{}
            }
        }
        Start-Sleep -Milliseconds 750
    }
    throw ('ILLUSTRATOR_START_FAILED: '+$lastError.Exception.Message)
}

try {
    if ($ext -in @('.doc', '.docx', '.wps')) {
        $fontAudit=$null
        if($ext -eq '.docx') {
            $fontPackage=Read-DocxFontPackage -Source $source
            $fontAudit=Assert-WordFonts -Xml $fontPackage.xml -Embedded $fontPackage.embedded
        }
        $progid = if ($engine -eq 'wps' -or $ext -eq '.wps') { 'KWPS.Application' } else { 'Word.Application' }
        $app = New-Object -ComObject $progid
        $owned = $true
        $app.Visible = $false
        $app.DisplayAlerts = 0
        $app.AutomationSecurity = 3
        $document = $app.Documents.Open($source, $false, $true, $false)
        if(-not $fontAudit) {
            try { $fontXml=Read-WordAuditXml ([string]$document.WordOpenXML) }
            catch { throw 'FONT_CHECK_FAILED: 无法核对源文档字体，已停止导出。请在原软件中另存为 DOCX 后重试。' }
            $fontAudit=Assert-WordFonts -Xml $fontXml
            Save-WordOriginalImages -Xml $fontXml -Folder ([IO.Path]::GetDirectoryName($target))
        }
        $evidence = Capture-WordSource -Document $document -Folder ([IO.Path]::GetDirectoryName($target))
        $evidence.font_audit=$fontAudit
        $evidence.word_background_export = Export-WordPdf -App $app -Document $document -Target $target
    } elseif ($ext -in @('.xls', '.xlsx', '.et')) {
        $progid = if ($engine -eq 'wps' -or $ext -eq '.et') { 'KET.Application' } else { 'Excel.Application' }
        $app = New-Object -ComObject $progid
        $owned = $true
        $app.Visible = $false
        $app.DisplayAlerts = $false
        $app.AutomationSecurity = 3
        $app.AskToUpdateLinks = $false
        $document = $app.Workbooks.Open($source, 0, $true)
        try { $evidence = Capture-WorkbookInventory -App $app -Document $document }
        catch { $evidence=@{capture_kind='workbook-inventory';inventory_complete=$false;capture_errors=@($_.Exception.Message)} }
        if(-not $evidence.inventory_complete){throw 'FONT_CHECK_FAILED: 无法完整检查工作簿字体，已停止导出。'}
        $evidence.font_audit=Assert-RequiredFonts -Required $evidence.required_fonts
        # Preserve existing print areas, pagination and visible sheet settings.
        $document.ExportAsFixedFormat(0, $target, 0, $true, $false)
    } elseif ($ext -in @('.ppt', '.pptx', '.dps')) {
        $progid = if ($engine -eq 'wps' -or $ext -eq '.dps') { 'KWPP.Application' } else { 'PowerPoint.Application' }
        $app = New-Object -ComObject $progid
        $owned = $true
        $app.AutomationSecurity = 3
        $document = $app.Presentations.Open($source, -1, 0, 0)
        $required=@();$embedded=@()
        foreach($font in $document.Fonts) {
            $required+=[string]$font.Name
            try {if($font.Embedded -eq -1){$embedded+=[string]$font.Name}}catch{}
        }
        $fontAudit=Assert-RequiredFonts -Required $required -Embedded $embedded
        $evidence=Capture-PresentationSource -Document $document -Folder ([IO.Path]::GetDirectoryName($target))
        $evidence.font_audit=$fontAudit
        $document.ExportAsFixedFormat($target,2,2)
    } elseif ($ext -eq '.cdr') {
        # Native design applications can be singletons. Never Quit a user's app.
        $progid = 'CorelDRAW.Application'
        $app = Connect-CorelDraw
        $document = $app.OpenDocument($source)
        $fonts=@(foreach($page in $document.Pages){Get-CorelFonts -Shapes $page.Shapes})
        $fontAudit=Assert-RequiredFonts -Required $fonts
        $evidence = Capture-CorelSource -App $app -Document $document -Folder ([IO.Path]::GetDirectoryName($target))
        $evidence.font_audit=$fontAudit
        $evidence.image_quality=Export-CorelPdfOriginal -Document $document -Target $target
    } elseif ($ext -eq '.ai') {
        $progid = 'Illustrator.Application'
        $app = Connect-Illustrator
        $inputData=@{source=$source;target=$target;folder=[IO.Path]::GetDirectoryName($target);keep_pdf=([bool]$request.keep_pdf)}|ConvertTo-Json -Compress
        $script=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'capture-illustrator.jsx')).Replace('__PDFSWITCH_REQUEST__',$inputData)
        $evidence=$app.DoJavaScript($script)|ConvertFrom-Json
    } else {
        throw 'Unsupported native format'
    }
    $evidence | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path ([IO.Path]::GetDirectoryName($target)) 'evidence.json') -Encoding UTF8
    Write-Output $progid
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally {
    if ($null -ne $document) {
        try {
            if ($ext -eq '.ai') { $document.Close(2) }
            elseif ($ext -in @('.ppt', '.pptx', '.dps', '.cdr')) { $document.Close() }
            else { $document.Close(0) }
        } catch { Write-Warning 'Could not close the task document; please check the application.' }
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)
    }
    if ($null -ne $app) {
        if ($owned) { try { $app.Quit() } catch {} }
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($app)
    }
}
