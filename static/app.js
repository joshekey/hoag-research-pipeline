"use strict";
const $ = id => document.getElementById(id);
const csrf = document.querySelector('meta[name="csrf"]').content;
let uid = null, offset = 0, total = 0, imageURL = null, latest = null, zoom = 1;
let refreshing = false, listing = 0, imageRequest = 0, reviewRequest = 0, foldersInitialized = false;
const states = ['unmatched','candidate','ambiguous','conflict','review','approved','exported','needs_review'];
const labels = {unmatched:'No report match',candidate:'One candidate',ambiguous:'Multiple candidates',conflict:'Identity conflict',review:'Ready for review',approved:'Approved',exported:'Exported',needs_review:'Needs rescan / review'};
function el(tag, text, cls) { const n = document.createElement(tag); if(text !== undefined)n.textContent = text; if(cls)n.className = cls; return n; }
function button(text, action, secondary = true) { const b = el('button',text,secondary?'secondary':''); b.type='button'; b.onclick=action; return b; }
function notice(text,error=false) { $('notice').textContent=text; $('notice').className=error?'error':''; }
async function api(path,body) { const res=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body)}:{}); const data=await res.json(); if(!res.ok)throw Error(data.error||'Request failed');return data; }
function safely(fn) { return async()=>{try{await fn();}catch(e){notice(e.message,true);}}; }
function cell(row,text,cls) { const c=el('td',text??'',cls); row.append(c); return c; }
function bytes(n) { if(n==null)return 'Not measured';const units=['B','KiB','MiB','GiB','TiB'];let i=0;while(n>=1024&&i<4){n/=1024;i++;}return n.toFixed(i?1:0)+' '+units[i]; }
function date(n) { return n?new Date(n*1000).toLocaleString():'Not yet'; }
function elapsed(n) { const seconds=Math.max(0,Math.floor(n));return Math.floor(seconds/3600)+'h '+Math.floor(seconds%3600/60)+'m '+seconds%60+'s'; }
function active() { return latest?.jobs.some(j=>['queued','running'].includes(j.state)); }
function stateCount(state) { return latest.states.filter(x=>x.state===state).reduce((n,x)=>n+x.count,0); }
async function refresh() {
 if(refreshing)return;refreshing=true;
 try {
  const s=await api('/api/status');latest=s;
  $('cards').replaceChildren();
  for(const [label,value] of [['DICOM files',s.counts.filter(x=>x.kind==='dicom'&&x.status==='ok').reduce((n,x)=>n+x.count,0)],['Text reports',s.counts.filter(x=>x.kind==='report'&&x.status==='ok').reduce((n,x)=>n+x.count,0)],['Studies',s.states.reduce((n,x)=>n+x.count,0)],['Approved',stateCount('approved')]]) {const c=el('div',undefined,'card');c.append(el('strong',value.toLocaleString()),el('span',label));$('cards').append(c);}
  $('sources').replaceChildren(...[...s.sources.map(x=>'Source: '+x),'Output: '+s.output].map(p=>el('p',p)));
  if(!$('folder-root').options.length)s.sources.forEach((path,index)=>{const o=el('option',path);o.value=index;$('folder-root').append(o);});
  if(!foldersInitialized){foldersInitialized=true;try{const saved=JSON.parse(s.settings.scan_folders||'null');if(Array.isArray(saved))for(const folder of saved)chosenFolders.set(folderKey(folder.root,folder.relative),folder);renderSelected();}catch(e){notice('Previous folder selection could not be restored.',true);}}
  const currentModality=$('filter-modality').value;
  $('filter-modality').replaceChildren(el('option','All modalities'));$('filter-modality').firstChild.value='';
  s.modalities.forEach(value=>{const o=el('option',value||'Unknown');o.value=value;$('filter-modality').append(o);});$('filter-modality').value=currentModality;
  renderActivity();renderJobs();renderQueues();renderExports();renderSQL(s.sql);
  for(const id of ['scan','scan-selected','match','prepare-batch','export-batch','prepare','approve','export','mount-submit'])$(id).disabled=active()||(id==='scan-selected'&&!chosenFolders.size);
  await list();
 } catch(e){notice(e.message,true);}finally{refreshing=false;}
}
function renderActivity() {
 const j=latest.jobs.find(x=>['queued','running'].includes(x.state));const ready=latest.settings.scan_complete==='1';
 $('activity').replaceChildren();
 if(j){const age=latest.now-(j.started||j.created);$('activity').append(el('small','PROCESSING'),el('h3',j.phase||j.kind),el('p',j.message));let detail=j.progress.toLocaleString()+' processed in this phase | Elapsed '+elapsed(age);if(j.phase==='Scanning files'&&age>0)detail+=' | '+(j.progress/age).toFixed(1)+' files/sec';$('activity').append(el('p',detail,'muted'));if(j.kind==='scan')$('activity').append(el('p','File counts are not study counts. The catalog appears during Building studies. Total archive size is unknown, so no percentage or ETA is estimated.','muted'));}
 else $('activity').append(el('h3',ready?'Catalog ready for matching and review':'A successful scan is required'),el('p','Last successful scan: '+date(Number(latest.settings.last_scan)||null)));
 const counts=latest.counts.filter(x=>x.status==='error').reduce((n,x)=>n+x.count,0);if(counts)$('activity').append(el('p',counts+' source files could not be indexed. Check encoding, permissions or DICOM support.','warning'));
 const step=j?.kind==='scan'?1:!ready?0:stateCount('approved')?4:stateCount('review')?3:2;
 [...$('workflow').children].forEach((n,i)=>{n.classList.toggle('current',i===step);if(i===step)n.setAttribute('aria-current','step');else n.removeAttribute('aria-current');});
}
function renderJobs() {
 $('job-rows').replaceChildren();const filter=$('job-filter').value;
 for(const j of latest.jobs.filter(j=>!filter||(filter==='active'?['queued','running'].includes(j.state):j.state===filter))) {
  const tr=el('tr');[j.id,j.kind,j.state,j.progress.toLocaleString()].forEach(v=>cell(tr,v));const detail=cell(tr,j.message);detail.append(el('span',(j.phase?j.phase+' | ':'')+elapsed((j.finished||latest.now)-(j.started||j.created)), 'sub'));const actions=cell(tr,'');
  if(['queued','running'].includes(j.state)){const b=button(j.cancel_requested?'Stopping safely...':'Cancel safely',safely(async()=>{const result=await api('/api/jobs/'+j.id+'/cancel',{});notice(result.message);await refresh();}));b.disabled=!!j.cancel_requested;actions.append(b);}
  if(['failed','cancelled'].includes(j.state)){const b=button('Retry',safely(async()=>{const r=await api('/api/jobs/'+j.id+'/retry',{});notice('Retry queued as job #'+r.id);await refresh();}));b.disabled=active();actions.append(b);}
  $('job-rows').append(tr);
 }
}
function renderQueues() {
 $('match-queues').replaceChildren();for(const state of ['unmatched','candidate','ambiguous','conflict']){const c=el('div',undefined,'card');c.append(el('strong',stateCount(state).toLocaleString()),el('span',labels[state]),button('Open queue',()=>{$('filter-state').value=state;offset=0;list().catch(e=>notice(e.message,true));$('studies').scrollIntoView({behavior:'smooth'});}));$('match-queues').append(c);}
}
async function list() {
 const generation=++listing;const query=new URLSearchParams({paged:'1',q:$('search').value,offset,state:$('filter-state').value,modality:$('filter-modality').value,from:$('filter-from').value,to:$('filter-to').value,sort:$('filter-sort').value,direction:$('filter-direction').value});
 const result=await api('/api/studies?'+query);if(generation!==listing)return;
 total=result.total;if(offset>=total&&offset>0){offset=Math.max(0,Math.ceil(total/100)-1)*100;return list();}
 $('study-rows').replaceChildren();for(const s of result.rows){const tr=el('tr');const c=cell(tr,s.name||s.patient||s.subject);c.append(el('span',s.subject,'sub'));[s.accession,s.date,s.modality,s.count].forEach(v=>cell(tr,v));cell(tr,labels[s.state]||s.state,'status');cell(tr,'').append(button('Review',()=>openStudy(s.uid)));$('study-rows').append(tr);}
 if(!result.rows.length){const tr=el('tr');const c=cell(tr,active()?'No studies visible yet. Check the processing phase above.':'No studies match these filters. Clear filters or run an index.');c.colSpan=7;$('study-rows').append(tr);}
 $('catalog-summary').textContent=total.toLocaleString()+' matching studies'+(total?' | Showing '+(offset+1)+'-'+Math.min(offset+100,total):'');$('page-number').value=Math.floor(offset/100)+1;$('page-count').textContent='of '+Math.max(1,Math.ceil(total/100));$('page-number').max=Math.max(1,Math.ceil(total/100));$('previous').disabled=offset===0;$('next').disabled=offset+100>=total;
}
async function queue(kind,payload={}) {try{const r=await api('/api/jobs',{kind,payload});notice('Job #'+r.id+' queued.');await refresh();}catch(e){notice(e.message,true);} }
async function openStudy(value) {
 const generation=++reviewRequest;uid=value;
 try{const d=await api('/api/study/'+encodeURIComponent(value));if(generation!==reviewRequest)return;
 $('sql-candidate-list').replaceChildren();$('sql-candidate-original').value='';$('sql-candidate-summary').textContent='Select Find SQL candidates to inspect SQL-backed reports. No match is approved automatically.';
 $('review').hidden=false;$('review-title').textContent=(d.study.accession||'No accession')+' | '+d.study.subject;$('review-state').textContent=labels[d.study.state]||d.study.state;
 if(d.study.last_error)notice(d.study.last_error,true);
 $('original').value=d.original_report;$('sanitized').value=d.study.sanitized||'';$('masks').value=d.study.redactions||'[]';$('review-note').value=d.study.review_note||'';$('images-reviewed').checked=false;$('report-reviewed').checked=false;
 $('report-choice').replaceChildren();d.candidates.forEach(addReport);if(d.study.report_id)$('report-choice').value=d.study.report_id;
 $('image-choice').replaceChildren();d.images.forEach((image,i)=>{const o=el('option','Instance '+(i+1)+' | '+JSON.parse(image.metadata).Modality);o.value=image.id;$('image-choice').append(o);});$('frame').value=0;
 highlight();$('review').scrollIntoView({behavior:'smooth'});if(!d.original_report&&d.candidates.length)await candidateText();if(d.images.length)await viewImage();
 }catch(e){notice(e.message,true);}
}
async function loadSQLCandidates() {
 const study=uid;
 if(!study)return;
 const data=await api('/api/study/'+encodeURIComponent(study)+'/sql-candidates');
 if(study!==uid)return;
 $('sql-candidate-original').value='';
 $('sql-candidate-list').replaceChildren();
 $('sql-candidate-summary').textContent=data.total+' name/DOB candidate(s); showing up to '+data.candidates.length+'. No clinical association approved.';
 data.candidates.forEach((item,i)=>{
   const line=el('div',undefined,'card');
   const evidence='Candidate '+(i+1)+' | Exam date verified: '+(item.exam_date_verified?'yes':'no')+
     ' | Result date matches: '+(item.result_date_matches?'yes':'no')+
     ' | Modality: '+item.modality+' | Anatomy: '+item.anatomy;
   line.append(el('p',evidence));
   line.append(button('Preview original SQL report',safely(async()=>{
     const selected=uid;
     const response=await api('/api/study/'+encodeURIComponent(selected)+'/sql-candidate/'+item.token);
     if(uid===selected)$('sql-candidate-original').value=response.text;
   })));
   $('sql-candidate-list').append(line);
 });
}
function addReport(r) {const o=el('option',(r.reason?r.reason+' - ':'Manual selection - ')+r.path);o.value=r.id;$('report-choice').append(o);}
async function candidateText() {const selected=$('report-choice').value;const study=uid;if(!selected){$('original').value='';return;}const report=await api('/api/report/'+selected);if(selected===$('report-choice').value&&study===uid)$('original').value=report.text;}
function highlight() {
 const text=$('sanitized').value;const parts=text.split('[REDACTED]');$('redaction-preview').replaceChildren();parts.forEach((part,i)=>{if(i)$('redaction-preview').append(el('mark','[REDACTED]'));$('redaction-preview').append(document.createTextNode(part));});$('redaction-summary').textContent=(parts.length-1)+' proposed redactions. Review the original and sanitized report; edits do not approve it.';
}
async function viewImage() {
 const generation=++imageRequest;try{const res=await fetch('/api/image/'+$('image-choice').value+'?frame='+$('frame').value);if(!res.ok){const d=await res.json();throw Error(d.error||'Preview unavailable');}const blob=await res.blob();if(generation!==imageRequest)return;if(imageURL)URL.revokeObjectURL(imageURL);imageURL=URL.createObjectURL(blob);$('image').src=imageURL;$('image-info').textContent=res.headers.get('X-Columns')+' x '+res.headers.get('X-Rows')+' pixels | '+res.headers.get('X-Frames')+' frames';$('frame').max=Number(res.headers.get('X-Frames'))-1;
 }catch(e){$('image').removeAttribute('src');$('mask-canvas').width=0;notice(e.message,true);}
}
function masks() {const value=JSON.parse($('masks').value);if(!Array.isArray(value)||value.some(r=>!Array.isArray(r)||r.length!==4||r.some(v=>!Number.isInteger(v)||v<0)||r[2]===0||r[3]===0))throw Error('Masks must be [[x,y,width,height]] with positive sizes');return value;}
function drawMasks() {const canvas=$('mask-canvas'),image=$('image');if(!image.naturalWidth)return;canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;const ctx=canvas.getContext('2d');ctx.clearRect(0,0,canvas.width,canvas.height);try{for(const [x,y,w,h] of masks()){ctx.fillStyle='rgba(255,90,90,.4)';ctx.fillRect(x,y,w,h);ctx.strokeStyle='#ff7777';ctx.lineWidth=Math.max(1,canvas.width/350);ctx.strokeRect(x,y,w,h);}}catch(e){notice(e.message,true);} }
function applyZoom() {const stage=$('image-stage');stage.style.width=(zoom*100)+'%';$('zoom-value').textContent=Math.round(zoom*100)+'%';}
let dragStart=null;
function point(event) {const c=$('mask-canvas'),r=c.getBoundingClientRect();return [Math.max(0,Math.min(c.width,Math.round((event.clientX-r.left)*c.width/r.width))),Math.max(0,Math.min(c.height,Math.round((event.clientY-r.top)*c.height/r.height)))];}
$('mask-canvas').onpointerdown=event=>{if(!$('draw-mask').checked)return;event.preventDefault();dragStart=point(event);$('mask-canvas').setPointerCapture(event.pointerId);};
$('mask-canvas').onpointerup=event=>{if(!dragStart)return;const end=point(event),start=dragStart;dragStart=null;const rectangle=[Math.min(start[0],end[0]),Math.min(start[1],end[1]),Math.abs(end[0]-start[0]),Math.abs(end[1]-start[1])];if(rectangle[2]&&rectangle[3]){try{const all=masks();all.push(rectangle);$('masks').value=JSON.stringify(all);$('images-reviewed').checked=false;drawMasks();}catch(e){notice(e.message,true);}}};
$('mask-canvas').onpointercancel=()=>{dragStart=null;};
$('image').onload=()=>{drawMasks();applyZoom();};$('zoom-in').onclick=()=>{zoom=Math.min(4,zoom+.25);applyZoom();};$('zoom-out').onclick=()=>{zoom=Math.max(.25,zoom-.25);applyZoom();};$('zoom-fit').onclick=()=>{zoom=1;applyZoom();};$('undo-mask').onclick=safely(()=>{const all=masks();all.pop();$('masks').value=JSON.stringify(all);$('images-reviewed').checked=false;drawMasks();});$('masks').onchange=()=>{$('images-reviewed').checked=false;drawMasks();};
async function health() {try{const data=await api('/api/health');$('health').replaceChildren();for(const share of data.shares){const c=el('div',undefined,'card');c.append(el('h3',share.slot),el('p',share.path),el('p',share.message,share.ok?'good':'warning'));if(share.ok)c.append(el('p',bytes(share.free)+' free / '+bytes(share.total)));$('health').append(c);if(share.output)$('export-space').textContent=share.ok?'Output capacity: '+bytes(share.free)+' free of '+bytes(share.total):'Output unavailable: '+share.message;}}catch(e){notice(e.message,true);} }
function renderExports() {$('exports').replaceChildren();if(!latest.exports.length){$('exports').textContent='No completed packages yet.';return;}const table=el('table');const header=el('tr');['Package destination','Created','Size','Validation',''].forEach(x=>header.append(el('th',x)));table.append(header);for(const record of latest.exports){const tr=el('tr');cell(tr,record.folder,'package-path');cell(tr,date(record.created));cell(tr,bytes(record.bytes));const c=cell(tr,record.validation);if(record.validated)c.append(el('span',date(record.validated),'sub'));const b=button('Verify checksums',()=>queue('validate_export',{export_id:record.id}));b.disabled=active();cell(tr,'').append(b);table.append(tr);}$('exports').append(table,el('p','Showing the latest 100 completed packages.','muted'));}
function renderSQL(files) {
 const opened=new Set([...$('sql').querySelectorAll('details[open]')].map(n=>n.dataset.key));$('sql').replaceChildren();if(!files.length){$('sql').textContent='No SQL dumps found in the selected folders.';return;}const search=$('schema-search').value.toLowerCase();
 for(const file of files){const metadata=JSON.parse(file.metadata),block=el('div',undefined,'schema-block');block.append(el('h3',file.path),el('p','Detected format: '+metadata.possible_type));const b=button('Inspect table and column names',()=>queue('inspect_sql',{file_id:file.id}));b.disabled=active();block.append(b);if(metadata.schema){block.append(el('p',metadata.schema.method+(metadata.schema.limited?' - inspection limit reached':''),'muted'));const tables=metadata.schema.tables.filter(t=>(t.table+' '+t.columns.join(' ')).toLowerCase().includes(search));for(const table of tables){const detail=el('details');detail.dataset.key=file.id+':'+table.table;detail.open=!!search||opened.has(detail.dataset.key);detail.append(el('summary',table.table+' ('+table.columns.length+' columns)'));const columns=el('div',undefined,'schema-columns');for(const column of table.columns)columns.append(el('span',column,table.potential_link_fields.includes(column)?'field useful':'field'));detail.append(columns);block.append(detail);}if(!tables.length)block.append(el('p','No matching supported table definitions found in the inspected portion.'));}else block.append(el('p','Inspect this dump to discover potential accession, study UID, report or file path fields.','muted'));$('sql').append(block);}
}
$('mount-form').onsubmit=async event=>{event.preventDefault();if(active()){notice('Wait for the current job before changing mounts.',true);return;}const body={slot:$('mount-slot').value,unc:$('mount-unc').value,username:$('mount-user').value,domain:$('mount-domain').value,password:$('mount-password').value,confirm:$('mount-confirm').checked};$('mount-password').value='';$('mount-submit').disabled=true;notice('Connecting share. Please wait; existing connections may be reconnected.');try{const result=await api('/api/mount',body);notice(result.message);$('mount-confirm').checked=false;await health();await refresh();}catch(e){notice(e.message,true);}finally{body.password='';$('mount-submit').disabled=active();}};
$('health-refresh').onclick=health;$('refresh').onclick=()=>{refresh();health();};$('scan').onclick=()=>queue('scan');$('match').onclick=()=>queue('match');
for(const id of ['search','filter-state','filter-modality','filter-from','filter-to','filter-sort','filter-direction'])$(id).onchange=safely(()=>{offset=0;return list();});
$('filter-clear').onclick=safely(()=>{for(const id of ['search','filter-state','filter-modality','filter-from','filter-to'])$(id).value='';offset=0;return list();});
$('previous').onclick=safely(()=>{offset=Math.max(0,offset-100);return list();});$('next').onclick=safely(()=>{offset+=100;return list();});$('page-number').onchange=safely(()=>{const page=Number($('page-number').value);offset=(Math.max(1,Math.min(Math.ceil(total/100)||1,Math.floor(page)||1))-1)*100;return list();});
$('sql-candidate-refresh').onclick=safely(loadSQLCandidates);
$('prepare').onclick=()=>queue('prepare',{uid,report_id:Number($('report-choice').value)});$('view').onclick=viewImage;$('image-choice').onchange=()=>{$('frame').value=0;viewImage();};$('report-choice').onchange=safely(async()=>{$('report-reviewed').checked=false;$('sanitized').value='';highlight();await candidateText();});
$('find-report').onclick=safely(async()=>{const reports=await api('/api/reports?q='+encodeURIComponent($('report-search').value));$('report-choice').replaceChildren();reports.forEach(addReport);$('report-reviewed').checked=false;$('sanitized').value='';highlight();await candidateText();});
$('sanitized').oninput=()=>{$('report-reviewed').checked=false;highlight();};
$('approve').onclick=safely(async()=>{await api('/api/study/'+encodeURIComponent(uid)+'/approve',{text:$('sanitized').value,rectangles:masks(),note:$('review-note').value,images_reviewed:$('images-reviewed').checked,report_reviewed:$('report-reviewed').checked});notice('Study approved for export.');await openStudy(uid);await refresh();});
$('export').onclick=()=>queue('export',{uid});$('reload-review').onclick=()=>openStudy(uid);$('prepare-batch').onclick=()=>queue('prepare_candidates');$('export-batch').onclick=()=>queue('export_approved');$('job-filter').onchange=()=>{if(latest)renderJobs();};$('schema-search').oninput=()=>{if(latest)renderSQL(latest.sql);};
for(const state of states){const o=el('option',labels[state]);o.value=state;$('filter-state').append(o);}
const chosenFolders=new Map();let folderRelative='.',folderOffset=0;
function folderKey(root,relative){return root+':'+relative;}
function selectFolder(root,relative,checked){const key=folderKey(root,relative);if(checked)chosenFolders.set(key,{root,relative});else chosenFolders.delete(key);renderSelected();}
function renderSelected(){$('folder-selected').replaceChildren();for(const [key,folder] of chosenFolders){const line=document.createElement('p');line.textContent=$('folder-root').options[folder.root].textContent+' / '+folder.relative+' ';const remove=document.createElement('button');remove.textContent='Remove';remove.className='secondary';remove.onclick=()=>{chosenFolders.delete(key);renderSelected();browseFolders();};line.append(remove);$('folder-selected').append(line);}$('scan-selected').disabled=!chosenFolders.size;}
async function browseFolders(append=false){try{const root=Number($('folder-root').value);const data=await api('/api/folders?root='+root+'&relative='+encodeURIComponent(folderRelative)+'&offset='+folderOffset);$('folder-location').textContent=folderRelative;if(!append){$('folder-list').replaceChildren();addFolder(root,folderRelative,'Include this folder and all subfolders',false);}for(const child of data.children)addFolder(root,child.relative,child.name,true);$('folder-more').hidden=folderOffset+data.children.length>=data.total;}catch(e){notice(e.message,true);}}
function addFolder(root,relative,title,open){const row=document.createElement('div');row.className='toolbar';const label=document.createElement('label');label.className='check';const input=document.createElement('input');input.type='checkbox';input.checked=chosenFolders.has(folderKey(root,relative));input.onchange=()=>selectFolder(root,relative,input.checked);label.append(input,document.createTextNode(' '+title));row.append(label);if(open){const button=document.createElement('button');button.textContent='Open';button.className='secondary';button.onclick=()=>{folderRelative=relative;folderOffset=0;browseFolders();};row.append(button);}$('folder-list').append(row);}
$('folder-open').onclick=()=>{folderRelative='.';folderOffset=0;browseFolders();};$('folder-root').onchange=$('folder-open').onclick;
$('folder-up').onclick=()=>{folderRelative=folderRelative.includes('/')?folderRelative.slice(0,folderRelative.lastIndexOf('/')):'.';folderOffset=0;browseFolders();};
$('folder-more').onclick=()=>{folderOffset+=200;browseFolders(true);};$('scan-selected').onclick=()=>queue('scan',{folders:[...chosenFolders.values()]});

$('errors-refresh').onclick=safely(async()=>{const rows=await api('/api/source-errors');$('source-errors').replaceChildren();for(const row of rows)$('source-errors').append(el('p',row.path+' | '+row.kind+' | '+(JSON.parse(row.metadata).error||'Check source format, encoding and access')));if(!rows.length)$('source-errors').textContent='No source errors in the active inventory.';});
refresh();health();setInterval(refresh,10000);setInterval(health,60000);
