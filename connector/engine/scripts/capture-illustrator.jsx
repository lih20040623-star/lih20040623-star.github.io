(function () {
    var request = __PDFSWITCH_REQUEST__;
    function encode(value) {
        if (value === null || value === undefined) return 'null';
        if (typeof value === 'string') return '"' + value.replace(/[\\"\x00-\x1f]/g,function(c){if(c==='\\')return '\\\\';if(c==='"')return '\\"';return '\\u'+('0000'+c.charCodeAt(0).toString(16)).slice(-4);}) + '"';
        if (typeof value === 'number' || typeof value === 'boolean') return String(value);
        var parts=[],key;
        if (value instanceof Array) { for(key=0;key<value.length;key++)parts.push(encode(value[key]));return '['+parts.join(',')+']'; }
        for(key in value) if(value.hasOwnProperty(key))parts.push(encode(key)+':'+encode(value[key]));
        return '{'+parts.join(',')+'}';
    }
    function visible(item) {
        var node=item;
        while(node && node.typename !== 'Document') {
            if (node.hidden === true || node.visible === false) return false;
            node=node.parent;
        }
        return true;
    }
    var oldInteraction=app.userInteractionLevel,doc=null;
    var evidence={capture_kind:'illustrator-raster',capture_scope:'visible-artboards',capture_complete:true,source_pages:[],capture_errors:[],missing_fonts:[],missing_links:[],text:''};
    try {
        app.userInteractionLevel=UserInteractionLevel.DONTDISPLAYALERTS;
        doc=app.open(new File(request.source));
        evidence.pages=doc.artboards.length;
        var directory=new Folder(request.folder+'/previews');if(!directory.exists)directory.create();
        var xmp=doc.XMPString||'',regex=/<stFnt:fontName>([^<]+)<\/stFnt:fontName>/g,match;
        while((match=regex.exec(xmp))!==null) {
            try { app.textFonts.getByName(match[1]); } catch(error) { evidence.missing_fonts.push(match[1]); }
        }
        if(evidence.missing_fonts.length)throw new Error('MISSING_FONTS: '+evidence.missing_fonts.join(', ')+'; export stopped without font substitution.');
        for(var linkIndex=0;linkIndex<doc.placedItems.length;linkIndex++) {
            try { if(!doc.placedItems[linkIndex].file.exists)evidence.missing_links.push(doc.placedItems[linkIndex].file.name); }
            catch(error) { evidence.missing_links.push('unresolved link '+(linkIndex+1)); }
        }
        for(var textIndex=0;textIndex<doc.textFrames.length;textIndex++)if(visible(doc.textFrames[textIndex]))evidence.text+=doc.textFrames[textIndex].contents+'\n';
        for(var i=0;i<Math.min(100,doc.artboards.length);i++) {
            var rect=doc.artboards[i].artboardRect,w=rect[2]-rect[0],h=rect[1]-rect[3];
            var dpi=Math.max(1,Math.floor(Math.min(120,2400*72/Math.max(w,h))));
            var entry={page:i+1,width_points:w,height_points:h,width:Math.ceil(w*dpi/72),height:Math.ceil(h*dpi/72),dpi:dpi,image:'source-'+(i+1)+'.png',objects:[],inventory_complete:true};
            try {
                for(var n=0;n<doc.pageItems.length;n++) {
                    var item=doc.pageItems[n];if(!visible(item))continue;
                    var b=item.geometricBounds;
                    if(b[2]<rect[0]||b[0]>rect[2]||b[1]<rect[3]||b[3]>rect[1])continue;
                    var object={type:item.typename,box:[(b[0]-rect[0])/w,(rect[1]-b[1])/h,(b[2]-b[0])/w,(b[1]-b[3])/h]};
                    if(item.typename==='TextFrame'){object.text=item.contents;try{object.font=item.textRange.characterAttributes.textFont.name;}catch(error){}}
                    entry.objects.push(object);
                    if(entry.objects.length>50000)throw new Error('Object inventory exceeds 50000 items.');
                }
                doc.artboards.setActiveArtboardIndex(i);
                var options=new ExportOptionsPNG24();options.artBoardClipping=true;options.antiAliasing=true;options.transparency=false;options.horizontalScale=dpi/72*100;options.verticalScale=dpi/72*100;
                doc.exportFile(new File(directory.fsName+'/source-'+(i+1)),ExportType.PNG24,options);
                if(!(new File(directory.fsName+'/'+entry.image)).exists)throw new Error('PNG preview was not created.');
            } catch(error) { evidence.capture_complete=false;entry.inventory_complete=false;delete entry.image;evidence.capture_errors.push('Artboard '+(i+1)+': '+error); }
            evidence.source_pages.push(entry);
        }
        if(doc.artboards.length>100){evidence.capture_complete=false;evidence.capture_errors.push('Source preview limited to 100 artboards.');}
        if(evidence.missing_fonts.length||evidence.missing_links.length){evidence.capture_complete=false;evidence.capture_errors.push('Missing source fonts or linked files.');}
        if(!request.keep_pdf) {
            var pdfOptions=new PDFSaveOptions();pdfOptions.preserveEditability=false;pdfOptions.artboardRange='';
            pdfOptions.colorDownsampling=0;pdfOptions.grayscaleDownsampling=0;pdfOptions.monochromeDownsampling=0;
            pdfOptions.colorDownsamplingMethod=DownsampleMethod.NODOWNSAMPLE;
            pdfOptions.grayscaleDownsamplingMethod=DownsampleMethod.NODOWNSAMPLE;
            pdfOptions.monochromeDownsamplingMethod=DownsampleMethod.NODOWNSAMPLE;
            pdfOptions.colorCompression=CompressionQuality.None;
            pdfOptions.grayscaleCompression=CompressionQuality.None;
            pdfOptions.monochromeCompression=MonochromeCompression.None;
            doc.saveAs(new File(request.target),pdfOptions);
        }
        evidence.image_quality={policy:request.keep_pdf?'embedded-pdf-preserved':'original-pixels',downsampling:false};
        return encode(evidence);
    } finally {
        if(doc)doc.close(SaveOptions.DONOTSAVECHANGES);
        app.userInteractionLevel=oldInteraction;
    }
}());
