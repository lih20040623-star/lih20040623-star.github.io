function Read-WordAuditXml {
    param([string]$Text)
    $settings=[Xml.XmlReaderSettings]::new()
    $settings.DtdProcessing=[Xml.DtdProcessing]::Prohibit
    $settings.XmlResolver=$null
    $settings.MaxCharactersInDocument=52428800
    $reader=[Xml.XmlReader]::Create([IO.StringReader]::new($Text),$settings)
    try { $xml=[Xml.XmlDocument]::new(); $xml.XmlResolver=$null; $xml.Load($reader); return ,$xml }
    finally { $reader.Dispose() }
}

function Read-DocxFontPackage {
    param([string]$Source)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip=[IO.Compression.ZipFile]::OpenRead($Source)
    try {
        $xml=Read-WordAuditXml '<audit />'
        $embedded=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        $fontTable=$null; $fontRels=$null
        foreach($entry in $zip.Entries) {
            if($entry.FullName -notmatch '^word/(document|styles|settings|fontTable|theme/theme\d+|header\d+|footer\d+|footnotes|endnotes)\.xml$' -and $entry.FullName -ne 'word/_rels/fontTable.xml.rels') { continue }
            if($entry.Length -gt 52428800) { throw 'FONT_CHECK_FAILED: Document XML exceeds the font inspection limit.' }
            $stream=[IO.StreamReader]::new($entry.Open())
            try { $part=Read-WordAuditXml $stream.ReadToEnd() } finally { $stream.Dispose() }
            [void]$xml.DocumentElement.AppendChild($xml.ImportNode($part.DocumentElement,$true))
            if($entry.FullName -eq 'word/fontTable.xml') { $fontTable=$part }
            if($entry.FullName -eq 'word/_rels/fontTable.xml.rels') { $fontRels=$part }
        }
        # Embedded font names are available only when their referenced part exists.
        if($fontTable -and $fontRels) {
            foreach($font in $fontTable.DocumentElement.ChildNodes) {
                foreach($embed in $font.ChildNodes) {
                    if($embed.LocalName -notmatch '^embed') { continue }
                    $id=$embed.GetAttribute('id','http://schemas.openxmlformats.org/officeDocument/2006/relationships')
                    foreach($rel in $fontRels.DocumentElement.ChildNodes) {
                        if($rel.GetAttribute('Id') -ne $id -or $rel.GetAttribute('TargetMode') -eq 'External') { continue }
                        $uri=[Uri]::new([Uri]'http://package/word/fontTable.xml',$rel.GetAttribute('Target'))
                        $entry=$zip.GetEntry([Uri]::UnescapeDataString($uri.AbsolutePath.TrimStart('/')))
                        if($entry -and $entry.Length -gt 32) { [void]$embedded.Add($font.GetAttribute('name','http://schemas.openxmlformats.org/wordprocessingml/2006/main')) }
                    }
                }
            }
        }
        return @{xml=$xml;embedded=@($embedded)}
    } finally { $zip.Dispose() }
}

function Get-WordRequiredFonts {
    param([Xml.XmlDocument]$Xml)
    $w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    $ns=[Xml.XmlNamespaceManager]::new($Xml.NameTable)
    $ns.AddNamespace('w',$w)
    $ns.AddNamespace('a','http://schemas.openxmlformats.org/drawingml/2006/main')
    $styles=@{}; $cache=@{}; $theme=@{}
    $lang=$Xml.SelectSingleNode('//w:docDefaults/w:rPrDefault/w:rPr/w:lang',$ns)
    $script='Hans'
    if($lang) {
        $language=$lang.GetAttribute('eastAsia',$w)
        if($language -match '^ja'){$script='Jpan'} elseif($language -match '^ko'){$script='Hang'} elseif($language -match '^zh-(TW|HK|MO|Hant)'){$script='Hant'}
    }
    foreach($group in @('major','minor')) {
        $node=$Xml.SelectSingleNode("//a:fontScheme/a:${group}Font",$ns)
        if(-not $node){continue}
        foreach($pair in @(@('Ascii','latin'),@('HAnsi','latin'),@('EastAsia','ea'),@('Bidi','cs'))) {
            $child=$node.SelectSingleNode('a:'+$pair[1],$ns)
            $value=if($child){$child.GetAttribute('typeface')}else{''}
            if(-not $value -and $pair[0] -eq 'EastAsia') {
                $child=$node.SelectSingleNode("a:font[@script='$script']",$ns)
                if($child){$value=$child.GetAttribute('typeface')}
            }
            if($value){$theme[$group+$pair[0]]=$value}
        }
    }
    function Merge-RunFonts($Base,$Node) {
        $result=@{}; foreach($key in $Base.Keys){$result[$key]=$Base[$key]}
        if($Node) {
            foreach($key in @('ascii','hAnsi','eastAsia','cs')) {
                $value=$Node.GetAttribute($key,$w)
                $themeValue=$Node.GetAttribute($key+'Theme',$w)
                if($themeValue -and $theme.ContainsKey($themeValue)){$value=$theme[$themeValue]}
                if($value){$result[$key]=$value}
            }
        }
        return $result
    }
    foreach($style in $Xml.SelectNodes('//w:styles/w:style',$ns)){$styles[$style.GetAttribute('styleId',$w)]=$style}
    $defaults=Merge-RunFonts @{} ($Xml.SelectSingleNode('//w:docDefaults/w:rPrDefault/w:rPr/w:rFonts',$ns))
    function Get-StyleFonts([string]$Id,[int]$Depth=0) {
        if($cache.ContainsKey($Id)){return $cache[$Id]}
        if($Depth -gt 50){throw 'FONT_CHECK_FAILED: Cyclic or excessively deep Word style inheritance.'}
        $base=@{}; $style=$styles[$Id]
        if($style) {
            $parent=$style.SelectSingleNode('w:basedOn',$ns)
            if($parent){$base=Get-StyleFonts $parent.GetAttribute('val',$w) ($Depth+1)}
            $base=Merge-RunFonts $base ($style.SelectSingleNode('w:rPr/w:rFonts',$ns))
        }
        $cache[$Id]=$base; return $base
    }
    $defaultStyle=$Xml.SelectSingleNode('//w:styles/w:style[@w:type="paragraph" and @w:default="1"]',$ns)
    $defaultId=if($defaultStyle){$defaultStyle.GetAttribute('styleId',$w)}else{'Normal'}
    $required=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach($part in $Xml.SelectNodes('//w:document|//w:hdr|//w:ftr|//w:footnotes|//w:endnotes',$ns)) {
        foreach($paragraph in $part.SelectNodes('.//w:p',$ns)) {
            # Referenced table styles may carry fonts in conditional formatting.
            $tableStyle=$paragraph.SelectSingleNode('ancestor::w:tbl[1]/w:tblPr/w:tblStyle',$ns)
            $tableFonts=@()
            if($tableStyle -and $styles.ContainsKey($tableStyle.GetAttribute('val',$w))) {
                $tableFonts=@($styles[$tableStyle.GetAttribute('val',$w)].SelectNodes('.//w:rFonts',$ns))
            }
            $pstyle=$paragraph.SelectSingleNode('w:pPr/w:pStyle',$ns)
            $id=if($pstyle){$pstyle.GetAttribute('val',$w)}else{$defaultId}
            $base=Merge-RunFonts $defaults $null
            $styleFonts=Get-StyleFonts $id
            foreach($key in $styleFonts.Keys){$base[$key]=$styleFonts[$key]}
            foreach($run in $paragraph.SelectNodes('.//w:r',$ns)) {
                if($run.SelectSingleNode('ancestor::w:del',$ns)){continue}
                $text=($run.SelectNodes('.//w:t',$ns)|ForEach-Object InnerText)-join ''
                if(-not $text.Trim()){continue}
                $fonts=Merge-RunFonts $base $null
                $rstyle=$run.SelectSingleNode('w:rPr/w:rStyle',$ns)
                if($rstyle){$extra=Get-StyleFonts $rstyle.GetAttribute('val',$w);foreach($key in $extra.Keys){$fonts[$key]=$extra[$key]}}
                $fonts=Merge-RunFonts $fonts ($run.SelectSingleNode('w:rPr/w:rFonts',$ns))
                foreach($tableFont in $tableFonts) {
                    $declared=Merge-RunFonts @{} $tableFont
                    if($text -match '[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]' -and $declared.eastAsia){[void]$required.Add($declared.eastAsia)}
                    if($text -match '[\u0021-\u024f]' -and $declared.ascii){[void]$required.Add($declared.ascii)}
                }
                if($text -match '[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]' -and $fonts.eastAsia){[void]$required.Add($fonts.eastAsia)}
                if($text -match '[\u0021-\u024f\u0370-\u052f]' -and ($fonts.ascii -or $fonts.hAnsi)) {
                    $name=if($text -match '[\u0080-\u052f]' -and $fonts.hAnsi){$fonts.hAnsi}else{$fonts.ascii}
                    if($name){[void]$required.Add($name)}
                }
                if($text -match '[\u0590-\u08ff]' -and $fonts.cs){[void]$required.Add($fonts.cs)}
            }
        }
        foreach($symbol in $part.SelectNodes('.//w:sym[@w:font]',$ns)){[void]$required.Add($symbol.GetAttribute('font',$w))}
        foreach($run in $part.SelectNodes('.//a:r',$ns)) {
            $textNode=$run.SelectSingleNode('a:t',$ns)
            if(-not $textNode -or -not $textNode.InnerText.Trim()){continue}
            foreach($kind in @('latin','ea','cs')) {
                $node=$run.SelectSingleNode('a:rPr/a:'+$kind,$ns)
                if(-not $node){$node=$run.SelectSingleNode('../a:pPr/a:defRPr/a:'+$kind,$ns)}
                if(-not $node){continue}
                $name=$node.GetAttribute('typeface')
                $aliases=@{'+mj-lt'='majorAscii';'+mn-lt'='minorAscii';'+mj-ea'='majorEastAsia';'+mn-ea'='minorEastAsia';'+mj-cs'='majorBidi';'+mn-cs'='minorBidi'}
                if($aliases.ContainsKey($name) -and $theme.ContainsKey($aliases[$name])){$name=$theme[$aliases[$name]]}
                if($name -and -not $name.StartsWith('+')){[void]$required.Add($name)}
            }
        }
    }
    return @{required_fonts=@($required|Sort-Object)}
}

function Assert-WordFonts {
    param([Xml.XmlDocument]$Xml,[string[]]$Embedded=@(),[string[]]$Available=$null)
    $audit=Get-WordRequiredFonts $Xml
    return Assert-RequiredFonts -Required $audit.required_fonts -Embedded $Embedded -Available $Available
}

function Assert-RequiredFonts {
    param([string[]]$Required,[string[]]$Embedded=@(),[string[]]$Available=$null)
    $audit=@{required_fonts=@($Required|Sort-Object -Unique)}
    if($null -eq $Available) {
        Add-Type -AssemblyName System.Drawing
        $collection=[Drawing.Text.InstalledFontCollection]::new()
        try {
            $Available=@(foreach($family in $collection.Families){$family.Name; $family.GetName(1033); $family.GetName(2052)})
        } finally {$collection.Dispose()}
    }
    $names=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach($name in @($Available)+@($Embedded)){if($name){[void]$names.Add($name.TrimStart('@').Trim())}}
    $missing=@($audit.required_fonts|Where-Object {-not $names.Contains($_.TrimStart('@').Trim())})
    if($missing.Count) {throw ('MISSING_FONTS: 当前电脑缺少源文档字体：'+($missing -join '、')+'。已停止导出，未替换字体；请安装原字体后重试。')}
    $audit.status='passed'; $audit.embedded_fonts=@($Embedded)
    return $audit
}

function Save-WordOriginalImages {
    param([Xml.XmlDocument]$Xml,[string]$Folder)
    $ns=[Xml.XmlNamespaceManager]::new($Xml.NameTable)
    $ns.AddNamespace('pkg','http://schemas.microsoft.com/office/2006/xmlPackage')
    foreach($part in $Xml.SelectNodes('//pkg:part[starts-with(@pkg:name,"/word/media/")]',$ns)) {
        $binary=$part.SelectSingleNode('pkg:binaryData',$ns)
        if(-not $binary){continue}
        $destination=Join-Path $Folder 'original-images'
        [void][IO.Directory]::CreateDirectory($destination)
        $name=[IO.Path]::GetFileName($part.GetAttribute('name',$ns.LookupNamespace('pkg')))
        [IO.File]::WriteAllBytes((Join-Path $destination $name),[Convert]::FromBase64String($binary.InnerText))
    }
}
