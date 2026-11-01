const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let jobs = [], files = [], lastJobs = '';
const deletingJobs = new Set();
const labels = {queued:'排队中',converting:'正在转换',checking:'正在核对',done:'导出完成',failed:'导出失败'};
const verdictLabels={auto_match:'自动核对通过',within_tolerance:'低于 2%，已忽略',preserved:'内嵌 PDF 原样保留',reference_match:'参考核对一致',difference:'发现疑似差异',review:'核对未完成'};
const verdictColors={auto_match:'good',within_tolerance:'good',preserved:'good',reference_match:'good',difference:'bad',review:'warn'};
function select(list) { files = Array.from(list); $('#selection').textContent = files.length ? `已选择 ${files.length} 个文件 · ${files.map(f=>f.name).join('、')}` : '点击选择，或把文件拖到这里'; }
$('#source').addEventListener('change', e => select(e.target.files));
for (const type of ['dragenter','dragover']) $('#drop').addEventListener(type,e=>{e.preventDefault();$('#drop').classList.add('drag');});
for (const type of ['dragleave','drop']) $('#drop').addEventListener(type,e=>{e.preventDefault();$('#drop').classList.remove('drag');if(type==='drop')select(e.dataTransfer.files);});
$('#upload').addEventListener('submit',async e=>{
  e.preventDefault();$('#error').textContent='';
  if(!files.length){$('#error').textContent='请先选择源文件';return;}
  const reference=$('#reference').files[0];
  if(reference&&files.length!==1){$('#error').textContent='附带参考 PDF 时，请每次提交一个源文件，确保对应关系正确。';return;}
  $('#submit').disabled=true;
  try{
    for(const file of files){
      if(file.size+(reference?.size||0)>100*1024*1024)throw Error(`${file.name}：上传总量超过 100 MB`);
      const form=new FormData();form.append('source',file);form.append('engine',$('#engine').value);if(reference)form.append('reference',reference);
      const response=await PDFSwitch.request('/api/jobs',{method:'POST',body:form});const result=await response.json();if(!response.ok)throw Error(result.error||'上传失败');
    }
    select([]);$('#source').value='';$('#reference').value='';
  }catch(err){$('#error').textContent=err.message;}finally{$('#submit').disabled=!PDFSwitch.connected;await refresh();}
});
async function refresh(){
  if(!PDFSwitch.connected)return;
  try{
    const response=await PDFSwitch.request('/api/jobs');if(!response.ok)throw Error('无法读取任务');jobs=await response.json();
    if($('#error').textContent.startsWith('服务连接失败：'))$('#error').textContent=''; const signature=JSON.stringify(jobs);if(signature===lastJobs)return;lastJobs=signature;
    $('#total').textContent=jobs.length;$('#task-count').textContent=jobs.length;
    $('#running').textContent=jobs.filter(j=>['queued','converting','checking'].includes(j.status)).length;
    $('#matched').textContent=jobs.filter(j=>['reference_match','auto_match','within_tolerance'].includes(j.report?.verdict)).length;
    $('#review').textContent=jobs.filter(j=>j.status==='failed'||['difference','review'].includes(j.report?.verdict)).length;
    $('#empty').hidden=jobs.length>0;
    $('#jobs').innerHTML=jobs.map(j=>{
      const v=j.report?.verdict;
      return `<tr><td><span class="filetype">${esc(j.extension.slice(1).toUpperCase())}</span>${esc(j.name)}<small>${esc(j.engine_used||'等待引擎处理')} · ${new Date(j.created*1000).toLocaleString('zh-CN')}</small></td><td><span class="tag ${j.status==='failed'?'bad':j.status==='done'?'good':'busy'}">${labels[j.status]||esc(j.status)}</span></td><td><span class="tag ${verdictColors[v]||'warn'}">${verdictLabels[v]||'尚未核对'}</span><small>${esc(j.status==='failed'?j.message:j.report?`${j.report.pages} 页 · ${j.report.summary}`:j.message)}</small></td><td>${j.status==='done'?`<a class="action" href="#" data-artifact="/files/${j.id}/output.pdf" target="_blank" rel="noopener">查看 PDF</a><button class="plain" data-report="${j.id}">核对报告 ↗</button><br><button class="plain" data-retry="${j.id}">重新导出核对 ↻</button>`:j.status==='failed'?`<button class="plain" data-retry="${j.id}">重试转换 ↻</button>`:''}<br><button class="plain delete-task" data-delete="${j.id}" ${!['done','failed'].includes(j.status)||deletingJobs.has(j.id)?'disabled':''} title="${['done','failed'].includes(j.status)?'仅从网页删除，保留文件夹和全部文件':'任务处理中，完成后可删除'}">${deletingJobs.has(j.id)?'正在删除…':'删除'}</button></td></tr>`;
    }).join('');
  }catch(err){$('#error').textContent='服务连接失败：'+err.message;}
}
$('#jobs').addEventListener('click',async e=>{
  const remove=e.target.dataset.delete;
  if(remove){
    const job=jobs.find(j=>j.id===remove);if(!job||deletingJobs.has(remove))return;

    deletingJobs.add(remove);e.target.disabled=true;e.target.textContent='正在删除…';
    try{
      const response=await PDFSwitch.request(`/api/jobs/${remove}`,{method:'DELETE'});const result=await response.json();
      if(!response.ok)throw Error(result.error||'删除失败');

      $('#error').textContent='';
    }catch(err){$('#error').textContent=err.message;}
    finally{deletingJobs.delete(remove);lastJobs='';await refresh();}
    return;
  }
  const id=e.target.dataset.report;if(id)openReport(jobs.find(j=>j.id===id));
  const retry=e.target.dataset.retry;
  if(retry){e.target.disabled=true;try{const response=await PDFSwitch.request(`/api/jobs/${retry}/retry`,{method:'POST'});const result=await response.json();if(!response.ok)throw Error(result.error||'重试失败');$('#error').textContent='';}catch(err){$('#error').textContent=err.message;}finally{await refresh();}}
});
function openReport(j){
  PDFSwitch.revokeURLs();
  const r=j.report;$('#report-title').textContent=j.name+' · 核对报告';
  $('#report-body').innerHTML=`<div class="report-summary"><strong>${esc(r.summary)}</strong><br>输出 ${r.pages} 页${r.reference_pages?` / 参考 ${r.reference_pages} 页`:''} · ${esc(j.engine_used)}<br><a class="action" href="#" data-artifact="/files/${j.id}/output.pdf" download="${esc(j.name)}.pdf">下载 PDF</a><a class="action" href="#" data-artifact="/files/${j.id}/job.json" download="report.json">下载 JSON 报告</a></div><details class="limitations"><summary>核对范围与检测精度</summary><p>${r.limitations.map(esc).join('<br>')}</p></details>${r.checks.map(c=>`<p>${esc(c.name)}：${esc(c.result)}</p>`).join('')}${r.comparisons.map(p=>`<div class="page-card"><strong>第 ${p.page} 页 · ${p.difference?'发现差异':p.visual_difference_ignored?'低于 2%，已忽略':'已执行项目一致'}</strong><p class="muted">尺寸 / 旋转：${p.size_match?'一致':'不同'} · ${p.missing_characters===null?'文字与图形按页面图像核对':`${esc(p.text_check)} · 少 ${p.missing_characters} 个字符，多 ${p.extra_characters} 个字符`} · 视觉差异：${p.changed_ratio===null?'未执行 / 尺寸不同':(p.changed_ratio*100).toFixed(4)+'%'}</p>${p.images?`<div class="images">${Object.entries(p.images).map(([key,name])=>`<figure><figcaption>${({reference:'参考页面',output:'导出页面',diff:'差异标记（红色）'})[key]}</figcaption><a href="#" data-artifact="/files/${j.id}/previews/${name}" target="_blank" rel="noopener"><img loading="lazy" data-artifact="/files/${j.id}/previews/${name}" alt="第 ${p.page} 页${esc(key)}"></a></figure>`).join('')}</div>`:''}</div>`).join('')}`;
  if(r.source_audit){const a=r.source_audit;const block=document.createElement('div');block.className='report-summary';block.innerHTML=`<strong>源内容清单检查</strong><p>${esc(a.summary)}</p>${a.missing_characters?`<p>未找到的字符数量：${a.missing_characters} · 示例：${esc(a.missing_sample)}</p>`:''}${a.expected_pages?`<p>源页数 / 画板数：${a.expected_pages}，PDF 页数：${a.actual_pages}</p>`:''}${a.missing_fonts?.length?`<p>缺少字体：${a.missing_fonts.map(esc).join('、')}</p>`:''}${a.missing_links?.length?`<p>缺少链接图片：${a.missing_links.map(esc).join('、')}</p>`:''}${a.sheets?.length?`<p>表格清单：${a.sheets.map(s=>`${esc(s.name)}（${s.visible?'可见':'隐藏'}，已检查 ${s.cells?.length||0} 个单元格，打印区域外 ${s.outside_print_area||0} 个）`).join('；')}</p>`:''}<p class="limitations">${(a.notes||[]).map(esc).join('<br>')}</p>`;$('#report-body').prepend(block);}
  if(r.scope){const scope=document.createElement('p');scope.className='report-summary';scope.textContent='本次核对范围：'+r.scope;$('#report-body').prepend(scope);}
  if(r.visual_ignore_below_ratio){const policy=document.createElement('p');policy.className='report-summary';policy.textContent='当前规则：每页视觉差异低于 2% 直接忽略；达到或超过 2% 仍提示。页数、尺寸和文字等检查单独判定。';$('#report-body').prepend(policy);}
  const cards=$('#report-body').querySelectorAll('.page-card');
  r.comparisons.forEach((p,i)=>{if(!cards[i])return;if(p.objects!==undefined){const text=document.createElement('p');text.className='muted';text.textContent=`源对象清单：${p.objects} 个 · 标记区域：${p.region_count||0} 处${p.source_blank&&p.output_blank?' · 原软件和 PDF 均为空白页':''}`;cards[i].prepend(text);}if(p.regions?.length){const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='查看差异坐标与关联对象';details.append(summary);p.regions.forEach((region,index)=>{const text=document.createElement('p');text.className='muted';text.textContent=`区域 ${index+1}：距左 ${(region.x*100).toFixed(1)}%，距上 ${(region.y*100).toFixed(1)}%，宽 ${(region.width*100).toFixed(1)}%，高 ${(region.height*100).toFixed(1)}%；关联 ${region.related_objects?.length||0} 个对象。`;details.append(text);});cards[i].append(details);}if(p.image_labels){const captions=cards[i].querySelectorAll('figcaption');Object.keys(p.images||{}).forEach((key,index)=>{if(captions[index])captions[index].textContent=p.image_labels[key]||key;});}});
  $('#report-dialog').showModal();
  PDFSwitch.hydrateImages($('#report-body'));
}
$('#close').onclick=()=>$('#report-dialog').close();$('#refresh').onclick=refresh;
async function loadCapabilities(){try{const r=await PDFSwitch.request('/api/capabilities');const c=await r.json();const entries={...c.native,'AI 兼容 PDF 读取':c.ai_pdf_compatible,LibreOffice:c.libreoffice,'视觉核对工具':c.rasterizer};$('#capabilities').innerHTML=Object.entries(entries).map(([n,ok])=>`<div>${esc(n)}<small class="${ok?'ready':''}">${esc(c.native_details?.[n]||(ok?'● 已检测到':'未检测到'))}</small></div>`).join('');}catch{$('#capabilities').textContent='环境检测失败';}}
function clearConnection(){
  jobs=[];lastJobs='';deletingJobs.clear();
  $('#jobs').replaceChildren();$('#empty').hidden=false;
  for(const id of ['total','task-count','running','matched','review'])$('#'+id).textContent='0';
  $('#capabilities').textContent='连接本机后，检测已安装的设计与办公软件。';
  $('#report-dialog').close();
}
window.addEventListener('pdfswitch:connected',()=>{lastJobs='';$('#error').textContent='';refresh();loadCapabilities();});
window.addEventListener('pdfswitch:disconnected',clearConnection);
if(PDFSwitch.connected){refresh();loadCapabilities();}
setInterval(()=>{if(PDFSwitch.connected&&!document.hidden)refresh();},2500);
