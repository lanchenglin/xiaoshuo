'use strict';
// Separate UI module: no framework, no external scripts, no untrusted innerHTML.
function acqField(parent, label, name, value, type='text', min=null, max=null){
 const wrap=node('label',label),input=node(type==='textarea'?'textarea':'input');
 input.name=name;if(type!=='textarea')input.type=type;else input.rows=2;
 input.value=value;input.defaultValue=value;if(min!==null)input.min=min;if(max!==null)input.max=max;
 wrap.append(input);parent.append(wrap);return input;
}
function acqSelect(parent,label,name,choices){
 const wrap=node('label',label),select=node('select');select.name=name;
 choices.forEach(([value,text],i)=>{const o=node('option',text);o.value=value;o.defaultSelected=i===0;select.append(o);});wrap.append(select);parent.append(wrap);return select;
}
function acqCheck(parent,label,name){const wrap=node('label'),input=node('input');input.type='checkbox';input.name=name;wrap.append(input,document.createTextNode(label));parent.append(wrap);return input;}
const acqForm=$('create-form'),acqDetails=node('details');acqDetails.open=true;
acqDetails.append(node('summary','搜索与整本下载'));
const acqGrid=node('div',undefined,'form-grid');
acqSelect(acqGrid,'资料获取方式','acquisition_mode',[['books','按目录下载整本参考书'],['pages','原有网页采样 / 仅导入本地TXT']]);
acqSelect(acqGrid,'下载后如何研究','analysis_mode',[['full','全文分段分析（更多token）'],['sample','开头/中间/结尾采样（省token）']]);
acqField(acqGrid,'需要通过的参考书数量','book_count',3,'number',1,10);
acqSelect(acqGrid,'下载后端','download_backend',[['auto','自动选择已配置下载器'],['native','内置公开HTML目录下载'],['lncrawl','Lightnovel Crawler（需另配）']]);
acqField(acqGrid,'每本最多章节','book_max_chapters',3000,'number',1,10000);
acqField(acqGrid,'每本下载字符上限','book_max_chars',8000000,'number',1000,30000000);
acqField(acqGrid,'全部参考书下载字符上限','books_max_chars',20000000,'number',1000,100000000);
acqDetails.append(acqGrid);
const queries=acqField(acqDetails,'搜索词（每行一条；可留空直接使用网址）','search_queries','','textarea');
queries.placeholder='例如：都市悬疑 完结 小说 排行榜。搜索需配置 SEARXNG_URL。';
const acqChecks=node('div',undefined,'checks');
acqCheck(acqChecks,'仅搜索下载，暂不调用AI（完成后可一键继续写作）','acquire_only');
acqCheck(acqChecks,'明确允许使用不完整参考书（默认不允许）','allow_partial');
acqDetails.append(acqChecks,node('p','目录下载有独立容量限制；缺章会保留并尝试下一候选，不会截取开篇冒充全书。目录已列章节齐全，也不表示作者已经完结。','small muted'));
acqForm.insertBefore(acqDetails,acqForm.querySelector('[name=chapter_count]').closest('.form-grid'));
function acquisitionFormValues(form){
 const f=form.elements;
 return {mode:f.acquisition_mode.value,queries:f.search_queries.value.split(/\n/).map(x=>x.trim()).filter(Boolean),book_count:Number(f.book_count.value),backend:f.download_backend.value,analysis:f.analysis_mode.value,max_chapters:Number(f.book_max_chapters.value),max_book_chars:Number(f.book_max_chars.value),max_total_chars:Number(f.books_max_chars.value),allow_partial:f.allow_partial.checked,acquire_only:f.acquire_only.checked};
}
const referenceBox=node('div');referenceBox.id='reference-books';$('sources').before(referenceBox);
const retry=node('details');retry.id='acquisition-retry';retry.hidden=true;retry.append(node('summary','调整采集要求并重试（保留已下载章节）'));
const retryGrid=node('div',undefined,'form-grid');
for(const [id,label,min,max] of [['acq-book-count','需要通过的参考书数量',1,10],['acq-max-chapters','每本最多章节',1,10000],['acq-book-chars','每本下载字符上限',1000,30000000],['acq-total-chars','所有参考书下载字符上限',1000,100000000]])acqField(retryGrid,label,id,'','number',min,max).id=id;
retry.append(retryGrid);acqField(retry,'搜索词（每行一条）','acq-queries','','textarea').id='acq-queries';
acqCheck(retry,'明确允许部分章节资料（不是完整本书）','acq-allow-partial').id='acq-allow-partial';
const saveAcq=node('button','保存采集要求','secondary');retry.append(saveAcq,node('p','仅在尚未开始拆书时可修改；完成后点击从检查点继续。换成新的作品网址请新建任务。','small muted'));$('sources').after(retry);
action(saveAcq,async()=>{
 const a={...state.detail.project.config.acquisition,book_count:Number($('acq-book-count').value),max_chapters:Number($('acq-max-chapters').value),max_book_chars:Number($('acq-book-chars').value),max_total_chars:Number($('acq-total-chars').value),allow_partial:$('acq-allow-partial').checked,queries:$('acq-queries').value.split(/\n/).map(x=>x.trim()).filter(Boolean)};
 const r=await api(`/projects/${state.project}/acquisition`,{method:'PUT',body:JSON.stringify(a)});toast(r.note);await loadDetail();
});
function renderReferenceBooks(d){
 const p=d.project,a=p.config.acquisition||{},books=d.reference_books||[];
 clear(referenceBox);
 const names={pending:'等待下载',downloading:'下载中',complete_snapshot:'已发现目录齐全',partial:'不完整',failed:'下载失败'};
 for(const b of books){
  const m=b.manifest,row=node('div',undefined,'source-card');
  row.append(node('strong',b.title+' · '+(names[b.status]||b.status)),node('p',`${b.backend} · ${b.downloaded}/${m.expected_chapters||0}章 · ${m.work_completion==='completed'?'页面标注已完结':m.work_completion==='ongoing'?'页面标注连载':'完结状态未知'}`));
  if(m.catalogue_issues?.length)row.append(node('p',m.catalogue_issues.join('；')));
  const bar=node('div',undefined,'button-row');
  for(const [kind,label] of [['txt','下载参考TXT'],['manifest','下载完整性清单']]){
   const button=node('button',label,'secondary');button.disabled=!m.checked_at;
   button.addEventListener('click',()=>{window.location.href=`/api/projects/${p.id}/books/${b.id}/export/${kind}`;});bar.append(button);
  }
  const inspect=node('button','查看章节/缺章','quiet');action(inspect,async()=>{const rows=await api(`/projects/${p.id}/books/${b.id}/chapters`);$('artifact-title').textContent=b.title+' · 下载目录';$('artifact-content').textContent=JSON.stringify(rows,null,2);setTab('research');});bar.append(inspect);row.append(bar);referenceBox.append(row);
 }
 const canEdit=a.mode==='books'&&!['queued','running'].includes(p.status)&&!d.artifacts.some(x=>x.key.startsWith('research/'));
 retry.hidden=!canEdit;
 if(canEdit){for(const [id,value] of [['acq-book-count',a.book_count],['acq-max-chapters',a.max_chapters],['acq-book-chars',a.max_book_chars],['acq-total-chars',a.max_total_chars],['acq-queries',(a.queries||[]).join('\n')]])if(document.activeElement!==$(id))$(id).value=value??'';if(document.activeElement!==$('acq-allow-partial'))$('acq-allow-partial').checked=!!a.allow_partial;}
 if(a.acquire_only&&p.status.startsWith('completed')){
  const b=node('button','用这些参考开始写书','primary');action(b,async()=>{await api(`/projects/${p.id}/continue-writing`,{method:'POST'});await loadDetail();});$('run-controls').append(b);
 }
 if(a.acquire_only){const accepted=d.sources.filter(x=>x.url.startsWith('book:')&&x.status==='ready').length;$('metric-progress').textContent=`${accepted} / ${a.book_count} 本`;$('progress').value=accepted;$('progress').max=a.book_count;$('page-subtitle').textContent='仅采集任务 · 不调用模型 · 完成后可继续写作';}
}
const previousRender=render;
render=function(d){previousRender(d);renderReferenceBooks(d);};
