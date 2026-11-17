"use strict";
const $ = id => document.getElementById(id);
const csrf = document.querySelector('meta[name="csrf"]').content;
let uid = null, offset = 0, total = 0, imageURL = null, latest = null, zoom = 1;
let refreshing = false, listing = 0, imageRequest = 0, reviewRequest = 0, foldersInitialized = false;
let dicomSeries = new Map(), dicomFrames = new Map(), inspectedFrames = new Set();
let imageReady=false,imageController=null,imageError='';
let workspaceReportMode='find',workspaceModeRequest=0;
const states = ['unmatched','candidate','ambiguous','conflict','review','approved','exported','needs_review'];
const labels = {unmatched:'No indexed TXT match · SQL not checked',candidate:'One candidate',ambiguous:'Multiple candidates',conflict:'Identity conflict',review:'Ready for review',approved:'Approved',exported:'Exported',needs_review:'Needs rescan / review'};
function el(tag, text, cls) { const n = document.createElement(tag); if(text !== undefined)n.textContent = text; if(cls)n.className = cls; return n; }
function button(text, action, secondary = true) { const b = el('button',text,secondary?'secondary':''); b.type='button'; b.onclick=action; return b; }
function notice(text,error=false) { $('notice').textContent=text; $('notice').className=error?'error':''; }
async function api(path,body) {
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),path==='/api/mount'?130000:90000);
 try{const options=body?{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body)}:{};options.signal=controller.signal;
 const res=await fetch(path,options),data=await res.json();if(!res.ok)throw Error(data.error||'Request failed');return data;
 }catch(error){if(error.name==='AbortError')throw Error('Request timed out. Check Processing jobs and source connections before retrying; the server action may still be running.');throw error;}finally{clearTimeout(timer);}
}

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
  if($('folder-root').options.length!==s.sources.length){const current=$('folder-root').value;$('folder-root').replaceChildren();s.sources.forEach((path,index)=>{const o=el('option',path);o.value=index;$('folder-root').append(o);});$('folder-root').value=current||'0';}
  if(!foldersInitialized){foldersInitialized=true;try{const saved=JSON.parse(s.settings.scan_folders||'null');if(Array.isArray(saved))for(const folder of saved)chosenFolders.set(folderKey(folder.root,folder.relative),folder);renderSelected();}catch(e){notice('Previous folder selection could not be restored.',true);}}
  const currentModality=$('filter-modality').value;
  $('filter-modality').replaceChildren(el('option','All modalities'));$('filter-modality').firstChild.value='';
  s.modalities.forEach(value=>{const o=el('option',value||'Unknown');o.value=value;$('filter-modality').append(o);});$('filter-modality').value=currentModality;
  renderActivity();renderJobs();renderQueues();renderExports();renderSQL(s.sql);
  for(const id of ['scan','scan-selected','match','prepare-batch','export-batch','prepare','approve','export','mount-submit'])$(id).disabled=active()||(id==='scan-selected'&&!chosenFolders.size)||(id==='prepare'&&!$('report-choice').value);
  $('release-version').textContent='v'+s.version;updateExportSelection();updateResearchProgress();
  const savedSQL=$('reindex-sql-file').value;$('reindex-sql-file').replaceChildren(el('option','Select SQL dump'));$('reindex-sql-file').firstChild.value='';
  for(const item of s.sql||[]){const o=el('option',item.path);o.value=item.id;$('reindex-sql-file').append(o);}$('reindex-sql-file').value=savedSQL;
  $('reindex-catalog').disabled=active();$('reindex-sql').disabled=active();
  $('browser-associate').disabled=active()||associationBusy||!uid||!selectedStudy||selectedStudy.uid!==uid||!browserSelection||!$('browser-confirm').checked;
  await list();
  await loadExamWorklist();
  await finishPreparation();
  await finishSelectedExport();
 } catch(e){notice(e.message,true);$('workspace-operation').textContent='CONNECTION PROBLEM · '+e.message+' · Refresh or check dashboard service status.';}finally{refreshing=false;}
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
 $('study-rows').replaceChildren();for(const s of result.rows){const tr=el('tr');const c=cell(tr,s.name||s.patient||s.subject);c.append(el('span',s.subject,'sub'));[s.accession,s.date,s.modality,s.count].forEach(v=>cell(tr,v));cell(tr,labels[s.state]||s.state,'status');cell(tr,'').append(button('View images',()=>openStudy(s.uid)));$('study-rows').append(tr);}
 if(!result.rows.length){const tr=el('tr');const c=cell(tr,active()?'No studies visible yet. Check the processing phase above.':'No studies match these filters. Clear filters or run an index.');c.colSpan=7;$('study-rows').append(tr);}
 $('catalog-summary').textContent=total.toLocaleString()+' matching studies'+(total?' | Showing '+(offset+1)+'-'+Math.min(offset+100,total):'');$('page-number').value=Math.floor(offset/100)+1;$('page-count').textContent='of '+Math.max(1,Math.ceil(total/100));$('page-number').max=Math.max(1,Math.ceil(total/100));$('previous').disabled=offset===0;$('next').disabled=offset+100>=total;
}
let selectedExportJob=null;
async function queue(kind,payload={}){try{const r=await api('/api/jobs',{kind,payload});if(kind==='export'&&payload.uid)selectedExportJob={id:r.id,uid:payload.uid};notice('Job #'+r.id+' queued.');await refresh();return r;}catch(e){notice(e.message,true);return null;}}
async function finishSelectedExport(){
 if(!selectedExportJob)return;
 const job=latest.jobs.find(j=>j.id===selectedExportJob.id);
 if(!job||['queued','running'].includes(job.state))return;
 const target=selectedExportJob;selectedExportJob=null;
 if(target.uid!==uid)return;
 if(job.state!=='completed'){notice('Export '+job.state+': '+job.message,true);return;}
 if(!reviewDirty&&!reviewSaving)await openStudy(target.uid,'#packages');
 notice('Export completed. The package is listed below.');
}
async function openOHIFStatus(study){
 const generation=reviewRequest;
 $('ohif-pilot-panel').hidden=false;$('ohif-host').hidden=true;
 $('ohif-iframe').removeAttribute('src');
 $('ohif-status').textContent='Checking viewer availability…';$('ohif-toggle').disabled=true;
 $('ohif-new-window').hidden=true;
 try{
  const info=await api('/api/study/'+encodeURIComponent(study)+'/ohif-status');
  if(uid!==study||generation!==reviewRequest)return;
  $('ohif-status').textContent=info.reason||(info.enabled?'Viewer available.':'Viewer unavailable. Check Administration for source readiness and viewer setup.');
  $('ohif-toggle').disabled=!info.enabled;$('ohif-toggle').textContent='Open OHIF viewer';
  $('ohif-new-window').hidden=!info.enabled;
  $('ohif-new-window').href='/viewer?StudyInstanceUIDs='+encodeURIComponent(study);
 }catch(error){if(uid===study&&generation===reviewRequest)$('ohif-status').textContent='Viewer check failed: '+error.message;}
}
const WORKSPACE_SCREENS=new Set(['overview','studies','review','matching','packages','administration','source-settings','jobs','historical-emr-review','database','help']);
function showWorkspaceScreen(){
 const screen=(window.location.hash||'#review').slice(1);
 const workflow=['studies','matching','review','packages'].includes(screen)||!WORKSPACE_SCREENS.has(screen);
 for(const id of WORKSPACE_SCREENS){const node=$(id);if(node)node.hidden=workflow||id!==screen;}
 $('study-workspace').hidden=!workflow;$('activity').hidden=!['overview','jobs'].includes(screen);$('workflow').hidden=true;
 if(screen==='overview')$('source-settings').hidden=false;
 if(workflow){$('review').hidden=false;$('matching').hidden=false;$('packages').hidden=false;}
 $('exam-empty').hidden=Boolean(selectedStudy)||!workflow;
 $('primary-navigation').hidden=true;
 for(const n of document.querySelectorAll('[data-phase]'))n.hidden=false;
 for(const id of ['ohif-pilot-panel','txt-candidate-panel','review-report-overview','sql-candidate-panel','next-reports','next-anonymize','next-export'])$(id).hidden=true;
 if($('workspace-report-mode'))setWorkspaceReportMode(workspaceReportMode);
 $('review').querySelector('.section-head').hidden=true;
 if(screen==='packages'&&$('workspace-final'))$('workspace-final').open=true;
 updateExportSelection();
}
window.addEventListener('hashchange',showWorkspaceScreen);
function makeStudyReviewPrimary(){
 setupExamWorkspace();
}
function updateSelectedStudyBanner(study){
 const banner=$('current-study-banner');
 if(!banner)return;
 banner.replaceChildren();
 if(!study){
   banner.textContent=uid?'Loading selected exam…':'No exam selected. Choose an exam from the review queue or patient search.';
   $('exam-action-context').textContent='';renderWorklistSelection();
   return;
 }
 const accession=study.accession||'No accession';
 const modality=study.modality||'Unknown modality';
 banner.append(el('span','SELECTED PATIENT','selection-label'),el('strong',(study.name||study.patient||study.subject).replaceAll('^',' '),'selection-patient'),el('span','Patient ID: '+(study.patient||'Unknown')+' · DOB: '+examDate(study.dob),'selection-detail'),el('span','SELECTED EXAM · '+accession,'selection-exam'),el('span',modality+' · '+examDate(study.date)+' · '+(study.count||0)+' images','selection-detail'),el('span',examState(study)+' · Original patient information','selection-state'));
 $('exam-action-context').textContent='Actions apply to '+accession+' · '+(study.patient||study.subject);
 renderWorklistSelection();
}
async function openStudy(value, target='#studies') {
 if(reviewDirty&&!window.confirm('Discard unsaved review changes and open this exam?'))return;
 const generation=++reviewRequest,modeRequest=workspaceModeRequest;uid=value;
 clearActiveStudy();
 window.location.hash=target;
 updateSelectedStudyBanner(null);
 showWorkspaceScreen();
 $('sql-patient-name').value='';
 try{const d=await api('/api/study/'+encodeURIComponent(value));if(generation!==reviewRequest)return;
 $('review-report-match-results').replaceChildren();
 $('review-report-match-status').textContent='Searching TXT and SQL reports for this selected study…';
 $('sql-candidate-list').replaceChildren();$('sql-candidate-original').value='';$('sql-candidate-summary').textContent='Select Find SQL candidates to inspect SQL-backed reports. No match is approved automatically.';
 const identity=d.images.length?JSON.parse(d.images[0].metadata):{};
 selectedStudy={...d.study,dob:identity.PatientBirthDate||'',issuer:identity.IssuerOfPatientID||''};updateSelectedStudyBanner(selectedStudy);
 try{sessionStorage.setItem('hoag-selected-exam',value);}catch(_error){}
 void loadSuggestions();$('browser-query').value=(d.study.name||'').split('^')[0].split(',')[0];browserOffset=0;void browseReports().catch(e=>notice(e.message,true));
 $('sql-patient-name').value=d.study.name||'';
 showWorkspaceScreen();
 $('review').hidden=false;$('review-title').textContent=(d.study.accession||'No accession')+' | '+d.study.subject;$('review-state').textContent=labels[d.study.state]||d.study.state;
 if(d.study.last_error)notice(d.study.last_error,true);
 $('original').value=d.original_report;$('sanitized').value=d.study.sanitized||'';$('masks').value=d.study.redactions||'[]';$('review-note').value=d.study.review_note||'';$('images-reviewed').checked=false;$('report-reviewed').checked=false;
 $('report-choice').replaceChildren();d.candidates.forEach(addReport);if(d.study.report_id)$('report-choice').value=d.study.report_id;
 $('legacy-txt-tools').open=!['review','approved','exported'].includes(d.study.state);
 updateLegacyTXTAvailability(d.candidates.length);
 setupDicomSeries(d.images);
 // Independent browser replaces patient-filtered reconciliation.
 // The native image viewer is the primary viewer; no OHIF requests are issued.
 if(modeRequest===workspaceModeRequest)setWorkspaceReportMode(d.study.report_id?'draft':'find');highlight();showWorkspaceScreen();if(!d.original_report&&d.candidates.length)await candidateText();if(d.images.length)await viewImage();
 }catch(e){if(generation===reviewRequest){uid=null;clearActiveStudy();updateSelectedStudyBanner(null);showWorkspaceScreen();try{sessionStorage.removeItem('hoag-selected-exam');}catch(_error){}notice('Could not open exam: '+e.message,true);}}
}
let reconcileSerial=0;
async function reconcileStudyReports(detail, study, generation){
 const serial=++reconcileSerial;
 const isCurrent=()=>generation===reviewRequest&&uid===study&&serial===reconcileSerial;
 const status=$('review-report-match-status'),box=$('review-report-match-results');
 const refreshButton=$('reconcile-study-reports');
 if(!isCurrent())return;
 box.replaceChildren();
 const txtCount=detail.candidates.length;
 box.append(el('p',txtCount
  ? txtCount+' indexed TXT candidate(s); verify each in the TXT selector.'
  : 'No indexed TXT report candidate. SQL search is independent.'));
 status.textContent='Reconciliation in progress: checking SQL broker for this selected study…';
 refreshButton.disabled=true;
 try{
   const sqlTotal=await loadSQLCandidates();
   if(!isCurrent())return;
   box.append(el('p',sqlTotal
    ? sqlTotal+' SQL candidate(s) available — NOT an approved study/report match. See details below.'
    : 'SQL broker search completed: no SQL candidates found.'));
   status.textContent=txtCount||sqlTotal
    ? 'Candidate reports found. Verify patient identity, exam date, modality and anatomy before association.'
    : 'TXT and SQL searches completed. No candidates found in these two sources.';
 }catch(_error){
   if(!isCurrent())return;
   const held=String(_error?.message||'').includes('temporarily disabled');
   const message=held
    ? 'SQL candidate review is temporarily disabled while we verify patient identity. No SQL report is shown.'
    : 'SQL lookup unavailable or failed; SQL matching status is UNKNOWN, not zero.';
   box.append(el('p',message));
   $('sql-candidate-summary').textContent=message;
   status.textContent=held
    ? 'TXT search completed. SQL candidate results are on safety hold.'
    : 'TXT search completed; SQL lookup could not be verified. Retry reconciliation.';
 }finally{
   if(isCurrent())refreshButton.disabled=false;
 }
}
$('reconcile-study-reports').onclick=safely(async()=>{
 const study=uid;
 if(!study)return;
 const generation=reviewRequest;
 const detail=await api('/api/study/'+encodeURIComponent(study));
 if(study!==uid||generation!==reviewRequest)return;
 await reconcileStudyReports(detail,study,generation);
});
async function loadSQLDraft(){
 if(!uid)return;
 const study=uid;
 const generation=reviewRequest;
 const result=await api('/api/study/'+encodeURIComponent(study)+'/sql-draft');
 if(study!==uid||generation!==reviewRequest)return;
 $('sql-draft-text').value=result.prepared?(result.draft||''):'';
 $('sql-draft-status').textContent=result.prepared
  ? 'Sanitized SQL draft available. Manual review required; NOT approved/exportable.'
  : 'No prepared SQL draft for this study.';
}
async function loadSQLCandidates() {
 const study=uid;
 const generation=reviewRequest;
 if(!study)return;
 const confirmedName=$('sql-patient-name').value.trim();
 if(!confirmedName)throw Error('Enter the patient name from the selected DICOM study before SQL search.');
 const data=await api('/api/study/'+encodeURIComponent(study)+'/sql-candidates?patient_name='+encodeURIComponent(confirmedName));
 if(study!==uid||generation!==reviewRequest)return 0;
 $('sql-draft-text').value='';
 $('sql-draft-status').textContent='';
 $('sql-candidate-original').value='';
 $('sql-candidate-readable').value='';
 $('sql-candidate-format-status').textContent='';
 $('sql-candidate-list').replaceChildren();
 $('sql-candidate-summary').textContent=data.total+' name-matched candidates (showing '+data.candidates.length+'). Score is a heuristic, not a probability or approval.';
 try{await loadSQLDraft();}catch(_error){
   if(study===uid)$('sql-draft-status').textContent='SQL draft lookup unavailable; candidate search results remain review-only.';
 }
 if(study!==uid||generation!==reviewRequest)return 0;
 const labels={name:'Patient name',dob:'DOB',exam_date:'Examination date',exam_type:'Modality and anatomy'};
 data.candidates.forEach((item,i)=>{
   const line=el('div',undefined,'card');
   line.append(el('h4','Candidate '+(i+1)+' — '+item.score+'/100 evidence points'));
   for(const key of ['name','dob','exam_date','exam_type']){
     line.append(el('p',(labels[key]||key)+': '+(item.evidence?.[key]||'unknown')));
   }
   if(item.result_date_matches)line.append(el('p','SQL RESULT DATE matches DICOM StudyDate; this is not independently verified exam date.','warning'));
   if(item.conflicts?.length)line.append(el('p','CONFLICTS: '+item.conflicts.join(', '),'warning'));
   line.append(button('Preview original SQL report',safely(async()=>{
     const selected=study;
     if(uid!==selected||generation!==reviewRequest||$('sql-patient-name').value.trim()!==confirmedName)
       throw Error('Selected DICOM study or patient entry changed; reconcile again.');
     const response=await api('/api/study/'+encodeURIComponent(selected)+'/sql-candidate/'+item.token);
     if(uid===selected&&generation===reviewRequest&&$('sql-patient-name').value.trim()===confirmedName){
       $('sql-candidate-original').value=response.text;
       $('sql-candidate-readable').value=response.formatted || response.text;
       $('sql-candidate-format-status').textContent=response.format_status==='xml'
         ? 'Formatted XML report preview; compare original XML for completeness.'
         : response.format_status==='unparsed'
           ? 'Could not safely format this document. Showing original source; do not confirm without reviewing it.'
           : 'Plain-text source shown without alteration.';
     }
   })));
   const note=el('textarea');note.rows=2;note.placeholder='Explain the verified exam date, relationship and any missing/conflicting evidence (15+ characters).';
   const dateCheck=el('input');dateCheck.type='checkbox';
   const conflictCheck=el('input');conflictCheck.type='checkbox';
   const labelDate=el('label');labelDate.append(dateCheck,document.createTextNode(' I independently verified the actual examination date and study/report association.'));
   const labelConflict=el('label');labelConflict.append(conflictCheck,document.createTextNode(' I reviewed all missing or conflicting evidence; differences are documented.'));
   line.append(note,labelDate,labelConflict);
   line.append(button('Record manual association (NOT export approval)',safely(async()=>{
     if(uid!==study||generation!==reviewRequest)throw Error('Study selection changed; reconcile again');
     if(!dateCheck.checked||!conflictCheck.checked)throw Error('Complete both confirmations.');
     const result=await api('/api/study/'+encodeURIComponent(study)+'/sql-association',
       {token:item.token,note:note.value,date_verified:dateCheck.checked,
        conflicts_acknowledged:conflictCheck.checked,confirm:true});
     if(uid===study)notice(result.recorded?
       'SQL association recorded for audit. Report is NOT prepared, approved or exportable.':
       'Association not recorded.');
   })));
   $('sql-candidate-list').append(line);
 });
 return data.total;
}
function updateLegacyTXTAvailability(count){
 const select=$('report-choice'),prepare=$('prepare'),status=$('legacy-txt-availability');
 const available=select.options.length>0&&Boolean(select.value);
 prepare.disabled=!available||active();
 status.textContent=count>0
  ? count+' report option(s) available. Prepare the explicitly associated report after verifying the relationship.'
  : available
    ? 'Manual report selections require explicit association in Reports.'
    : 'Associate a report in Reports before preparation.';
}
function addReport(r) {const o=el('option',(r.reason?r.reason+' - ':'Manual selection - ')+r.path);o.value=r.id;$('report-choice').append(o);}
async function candidateText(){const selected=$('report-choice').value,study=uid,generation=reviewRequest;if(!selected){$('original').value='';return;}const report=await api('/api/report/'+selected);if(generation===reviewRequest&&selected===$('report-choice').value&&study===uid)$('original').value=report.text;}

function highlight() {
 const text=$('sanitized').value;const parts=text.split('[REDACTED]');$('redaction-preview').replaceChildren();parts.forEach((part,i)=>{if(i)$('redaction-preview').append(el('mark','[REDACTED]'));$('redaction-preview').append(document.createTextNode(part));});$('redaction-summary').textContent=(parts.length-1)+' proposed redactions. Review the original and sanitized report; edits do not approve it.';
}
function setupDicomSeries(images){
 dicomSeries=new Map();dicomFrames=new Map();inspectedFrames=new Set();++imageRequest;
 $('images-reviewed').checked=false;
 images.forEach((item,i)=>{
  const meta=JSON.parse(item.metadata||'{}'),key=String(meta.SeriesInstanceUID||'unclassified');
  if(!dicomSeries.has(key))dicomSeries.set(key,{description:String(meta.SeriesDescription||meta.Modality||'Series'),images:[]});
  dicomSeries.get(key).images.push({id:item.id,sop:meta.SOPInstanceUID,series:key,label:'Instance '+(i+1)});
 });
 for(const series of dicomSeries.values())series.images.forEach((image,index)=>{image.label='Instance '+(index+1);});
 const selector=$('dicom-series');selector.replaceChildren();
 let n=0;for(const [key,series] of dicomSeries){
  const option=el('option',(++n)+'. '+series.description+' ('+series.images.length+' instances)');
  option.value=key;selector.append(option);
 }
 showSeries();
}
function currentSeries(){return dicomSeries.get($('dicom-series').value);}
function showSeries(){
 const series=currentSeries();$('image-choice').replaceChildren();
 for(const item of series?.images||[]){const o=el('option',item.label);o.value=item.id;$('image-choice').append(o);}
 $('frame').value=0;updateStack();
}
function updateStack(){
 const items=currentSeries()?.images||[],index=Math.max(0,items.findIndex(i=>String(i.id)===$('image-choice').value));
 const count=dicomFrames.get(String(items[index]?.id))||1;
 const frame=Math.max(0,Math.min(count-1,Number($('frame').value)||0));
 $('frame').value=frame;$('frame').max=count-1;
 const bar=$('dicom-position');bar.min=1;bar.max=Math.max(items.length,1);bar.value=items.length?index+1:1;bar.disabled=!items.length;
 $('dicom-position-label').textContent=items.length?'Instance '+(index+1)+' / '+items.length+' · Frame '+(frame+1)+' / '+count:'No images';
 $('dicom-prev').disabled=!items.length||(index===0&&frame===0);
 $('dicom-next').disabled=!items.length||(index===items.length-1&&frame===count-1);
 const shown=new Set([...inspectedFrames].map(x=>x.split(':')[0])).size;
 const total=[...dicomSeries.values()].reduce((n,v)=>n+v.images.length,0);
 $('dicom-review-progress').textContent='Displayed '+shown+' / '+total+' instances; '+inspectedFrames.size+' frames displayed. Total frame counts are unknown until images are opened. Displayed is not clinically reviewed.';
}
function navigateStack(delta){
 const items=currentSeries()?.images||[];if(!items.length)return;
 let i=Math.max(0,items.findIndex(x=>String(x.id)===$('image-choice').value));
 let frame=(Number($('frame').value)||0)+delta;
 if(frame>= (dicomFrames.get(String(items[i].id))||1)){if(i===items.length-1)return;i++;frame=0;}
 else if(frame<0){if(i===0)return;i--;frame=(dicomFrames.get(String(items[i].id))||1)-1;}
 $('image-choice').value=String(items[i].id);$('frame').value=frame;updateStack();void viewImage();
}
async function viewImage() {
 const generation=++imageRequest,study=uid,id=$('image-choice').value,frame=Number($('frame').value)||0;
 imageController?.abort();imageController=new AbortController();
 cancelMaskDrag();imageReady=false;imageError='';$('image').removeAttribute('src');$('mask-canvas').width=0;$('image-info').textContent='Loading selected image…';
 $('image-viewport').setAttribute('aria-busy','true');updateReviewControls();
 if(!id){$('image-viewport').setAttribute('aria-busy','false');return;}
 try{
  const windowQuery=new URLSearchParams({frame,study_uid:study,window:$('image-window').value});
  if($('image-window').value==='custom'){windowQuery.set('center',$('window-center').value);windowQuery.set('width',$('window-width').value);}
  const res=await fetch('/api/image/'+encodeURIComponent(id)+'?'+windowQuery,{cache:'no-store',signal:imageController.signal});
  if(!res.ok){const err=await res.json();throw Error(err.error||'Preview unavailable');}
  const blob=await res.blob();
  if(generation!==imageRequest||study!==uid)return;
  const frames=Number(res.headers.get('X-Frames'))||1;
  dicomFrames.set(String(id),frames);
  if(imageURL)URL.revokeObjectURL(imageURL);
  imageURL=URL.createObjectURL(blob);
  $('image').onload=()=>{
   if(generation!==imageRequest||study!==uid)return;
   imageReady=true;$('image-viewport').setAttribute('aria-busy','false');updateReviewControls();
   inspectedFrames.add(String(id)+':'+frame);drawMasks();applyZoom();updateStack();
  };
  $('image').onerror=()=>{if(generation===imageRequest&&study===uid){imageReady=false;imageError='Image could not be displayed';$('image-viewport').setAttribute('aria-busy','false');updateReviewControls();notice(imageError+'. Choose View to retry.',true);}};
  $('image').src=imageURL;
  $('image-info').textContent=res.headers.get('X-Columns')+' x '+res.headers.get('X-Rows')+' pixels | '+frames+' frames';
  updateStack();
 }catch(e){
  if(generation!==imageRequest||study!==uid)return;
  imageError=e.message;$('image-info').textContent='Preview unavailable';$('image-viewport').setAttribute('aria-busy','false');updateReviewControls();
  $('image').removeAttribute('src');$('mask-canvas').width=0;notice(e.message,true);
 }
}
function masks(){
 const doc=JSON.parse($('masks').value);
 const entries=Array.isArray(doc)?doc.map(rect=>({scope:'exam',rect})):doc?.version===2&&Array.isArray(doc.masks)?doc.masks:null;
 if(!entries||entries.length>100)throw Error('Masks must be legacy rectangles or a version 2 mask document (up to 100 masks).');
 for(const m of entries){if(!['exam','series','image'].includes(m.scope)||!Array.isArray(m.rect)||m.rect.length!==4||m.rect.some(v=>!Number.isSafeInteger(v)||v<0)||!m.rect[2]||!m.rect[3])throw Error('Invalid mask rectangle or scope');}
 return doc;
}
function maskEntries(){const doc=masks();return Array.isArray(doc)?doc.map(rect=>({scope:'exam',rect})):doc.masks;}
function writeMasks(entries){$('masks').value=JSON.stringify({version:2,masks:entries});}
function currentImage(){return currentSeries()?.images.find(i=>String(i.id)===$('image-choice').value);}
function visibleMaskEntries(){const image=currentImage();return maskEntries().filter(m=>m.scope==='exam'||m.series_uid===image?.series&&(m.scope==='series'||m.sop_uid===image?.sop&&m.frame===(Number($('frame').value)||0)));}
function newMask(rect){
 const image=currentImage();if(!image?.sop||!image?.series)throw Error('Image identity unavailable. Reload the exam before masking.');
 const mask={scope:$('mask-scope').value,series_uid:image.series,rect};
 if(mask.scope==='image'){mask.sop_uid=image.sop;mask.frame=Number($('frame').value)||0;}
 return mask;
}
function drawMasks(preview=null) {
 const canvas=$('mask-canvas'),image=$('image');if(!imageReady||!image.naturalWidth)return;
 canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;
 const ctx=canvas.getContext('2d');ctx.clearRect(0,0,canvas.width,canvas.height);
 try{
  for(const rect of [...visibleMaskEntries().map(m=>m.rect),...(preview?[preview]:[])]){
   const [x,y,w,h]=rect;ctx.fillStyle='rgba(0,0,0,.75)';ctx.fillRect(x,y,w,h);
   ctx.strokeStyle=rect===preview?'#F9902D':'#72d8c3';ctx.lineWidth=Math.max(1,canvas.width/350);ctx.strokeRect(x,y,w,h);
  }
 }catch(e){notice(e.message,true);}
}
function applyZoom(){
 const stage=$('image-stage'),image=$('image'),viewport=$('image-viewport');
 const height=parseFloat(getComputedStyle(viewport).maxHeight)||650;
 const fit=image.naturalWidth&&image.naturalHeight?Math.min(viewport.clientWidth,height*image.naturalWidth/image.naturalHeight):viewport.clientWidth;
 stage.style.width=fit?Math.round(fit*zoom)+'px':'100%';$('zoom-value').textContent=Math.round(zoom*100)+'%';
}
window.addEventListener('resize',()=>{cancelMaskDrag();applyZoom();drawMasks();});
let dragStart=null;
function cancelMaskDrag(){dragStart=null;}
function point(event) {
 const c=$('mask-canvas'),r=c.getBoundingClientRect();
 return [Math.max(0,Math.min(c.width,Math.round((event.clientX-r.left)*c.width/r.width))),Math.max(0,Math.min(c.height,Math.round((event.clientY-r.top)*c.height/r.height)))];
}
function dragRectangle(end){const start=dragStart.point;return [Math.min(start[0],end[0]),Math.min(start[1],end[1]),Math.abs(end[0]-start[0]),Math.abs(end[1]-start[1])];}
function canDrawMask(){return reviewEditable()&&imageReady&&$('draw-mask').checked;}
$('mask-canvas').onpointerdown=event=>{
 if(!canDrawMask()||event.button!==0)return;
 event.preventDefault();dragStart={point:point(event),request:imageRequest,study:uid,pointer:event.pointerId};
 $('mask-canvas').setPointerCapture(event.pointerId);
};
$('mask-canvas').onpointermove=event=>{
 if(dragStart?.pointer!==event.pointerId)return;
 if(!canDrawMask()||dragStart.request!==imageRequest||dragStart.study!==uid){cancelMaskDrag();drawMasks();return;}
 drawMasks(dragRectangle(point(event)));
};
$('mask-canvas').onpointerup=event=>{
 if(dragStart?.pointer!==event.pointerId)return;
 const valid=canDrawMask()&&dragStart.request===imageRequest&&dragStart.study===uid;
 const rectangle=dragRectangle(point(event));cancelMaskDrag();
 if(valid&&rectangle[2]&&rectangle[3]){
  try{const all=maskEntries();if(all.length>=100)throw Error('Maximum 100 masks per exam. Undo a mask before adding another.');all.push(newMask(rectangle));writeMasks(all);$('images-reviewed').checked=false;markReviewDirty();}
  catch(e){notice(e.message,true);}
 }
 drawMasks();
};
$('mask-canvas').onpointercancel=$('mask-canvas').onlostpointercapture=()=>{cancelMaskDrag();drawMasks();};
$('draw-mask').onchange=()=>{cancelMaskDrag();drawMasks();updateMaskControls();};
function updateMaskControls(){
 const editable=reviewEditable(),ready=editable&&imageReady;
 $('draw-mask').disabled=!ready;$('undo-mask').disabled=!editable||$('masks').value==='[]';
 $('mask-canvas').style.pointerEvents=ready&&$('draw-mask').checked?'auto':'none';
 const status=$('mask-status');if(!status)return;
 const locked=['approved','exported'].includes(selectedStudy?.state);
 status.textContent=!selectedStudy?'Select an exam to review its images.':locked?'Review locked. Reopen the review to change pixel masks.':active()||reviewSaving?'Processing. Mask editing resumes when the operation finishes.':!editable?'To enable masks: link a report on the right, then prepare its privacy draft.':!imageReady?(imageError?'Image unavailable: '+imageError+'. Choose View to retry.':'Loading image. Drawing is disabled until it is ready.'):$('draw-mask').checked?'Drag over the image to cover identifying text. Release to add the rectangle; Undo removes the last mask.':'Enable Draw pixel mask, then drag over the image.';
 let count,shown;try{count=maskEntries().length;shown=visibleMaskEntries().length;}catch(_error){count='Invalid';shown=0;}
 $('mask-summary').textContent=count+' masks total · '+shown+' on this frame · '+(reviewDirty?'Unsaved changes — Save draft to keep your work.':locked?'Saved with approval.':editable?'Draft saved on server. Approval is still required for export.':'Prepare a review draft to begin.');
 $('mask-review-step').hidden=editable||locked;
 $('mask-scope').disabled=!editable;
 renderMaskList();
}
$('zoom-in').onclick=()=>{cancelMaskDrag();zoom=Math.min(4,zoom+.25);applyZoom();drawMasks();};
$('zoom-out').onclick=()=>{cancelMaskDrag();zoom=Math.max(.25,zoom-.25);applyZoom();drawMasks();};
$('zoom-fit').onclick=()=>{cancelMaskDrag();zoom=1;applyZoom();drawMasks();};
$('undo-mask').onclick=safely(()=>{if(!reviewEditable())return;const all=maskEntries();all.pop();writeMasks(all);$('images-reviewed').checked=false;markReviewDirty();drawMasks();});
$('masks').oninput=()=>{cancelMaskDrag();$('images-reviewed').checked=false;markReviewDirty();drawMasks();};
$('masks').onchange=$('masks').oninput;
async function health() {try{const data=await api('/api/health');$('health').replaceChildren();for(const share of data.shares){const c=el('div',undefined,'card');c.append(el('h3',share.slot),el('p',share.path),el('p',share.message,share.ok?'good':'warning'));if(share.ok)c.append(el('p',bytes(share.free)+' free / '+bytes(share.total)));$('health').append(c);if(share.output)$('export-space').textContent=share.ok?'Output capacity: '+bytes(share.free)+' free of '+bytes(share.total):'Output unavailable: '+share.message;}}catch(e){notice(e.message,true);} }
function renderExports() {$('exports').replaceChildren();if(!latest.exports.length){$('exports').textContent='No completed packages yet.';return;}const table=el('table');const header=el('tr');['Package destination','Created','Size','Validation',''].forEach(x=>header.append(el('th',x)));table.append(header);for(const record of latest.exports){const tr=el('tr');cell(tr,record.folder,'package-path');cell(tr,date(record.created));cell(tr,bytes(record.bytes));const c=cell(tr,record.validation);if(record.validated)c.append(el('span',date(record.validated),'sub'));const b=button('Verify checksums',()=>queue('validate_export',{export_id:record.id}));b.disabled=active();cell(tr,'').append(b);table.append(tr);}$('exports').append(table,el('p','Showing the latest 100 completed packages.','muted'));}
function renderSQL(files) {
 const opened=new Set([...$('sql').querySelectorAll('details[open]')].map(n=>n.dataset.key));$('sql').replaceChildren();if(!files.length){$('sql').textContent='No SQL dumps found in the selected folders.';return;}const search=$('schema-search').value.toLowerCase();
 for(const file of files){const metadata=JSON.parse(file.metadata),block=el('div',undefined,'schema-block');block.append(el('h3',file.path),el('p','Detected format: '+metadata.possible_type));const b=button('Inspect table and column names',()=>queue('inspect_sql',{file_id:file.id}));b.disabled=active();block.append(b);if(metadata.schema){block.append(el('p',metadata.schema.method+(metadata.schema.limited?' - inspection limit reached':''),'muted'));const tables=metadata.schema.tables.filter(t=>(t.table+' '+t.columns.join(' ')).toLowerCase().includes(search));for(const table of tables){const detail=el('details');detail.dataset.key=file.id+':'+table.table;detail.open=!!search||opened.has(detail.dataset.key);detail.append(el('summary',table.table+' ('+table.columns.length+' columns)'));const columns=el('div',undefined,'schema-columns');for(const column of table.columns)columns.append(el('span',column,table.potential_link_fields.includes(column)?'field useful':'field'));detail.append(columns);block.append(detail);}if(!tables.length)block.append(el('p','No matching supported table definitions found in the inspected portion.'));}else block.append(el('p','Inspect this dump to discover potential accession, study UID, report or file path fields.','muted'));$('sql').append(block);}
}
$('mount-form').onsubmit=async event=>{event.preventDefault();if(active()){notice('Wait for the current job before changing mounts.',true);return;}const body={slot:$('mount-slot').value,unc:$('mount-unc').value,username:$('mount-user').value,domain:$('mount-domain').value,password:$('mount-password').value,confirm:$('mount-confirm').checked};$('mount-password').value='';$('mount-submit').disabled=true;notice('Connecting share. Please wait; existing connections may be reconnected.');try{const result=await api('/api/mount',body);notice(result.message);$('mount-confirm').checked=false;await refresh();await health();}catch(e){notice(e.message,true);}finally{body.password='';$('mount-submit').disabled=active();}};
$('health-refresh').onclick=health;$('refresh').onclick=()=>{refresh();health();};$('scan').onclick=()=>queue('scan');$('match').onclick=()=>queue('suggest_reports');
for(const id of ['search','filter-state','filter-modality','filter-from','filter-to','filter-sort','filter-direction'])$(id).onchange=safely(()=>{offset=0;return list();});
$('filter-clear').onclick=safely(()=>{for(const id of ['search','filter-state','filter-modality','filter-from','filter-to'])$(id).value='';offset=0;return list();});
$('previous').onclick=safely(()=>{offset=Math.max(0,offset-100);return list();});$('next').onclick=safely(()=>{offset+=100;return list();});$('page-number').onchange=safely(()=>{const page=Number($('page-number').value);offset=(Math.max(1,Math.min(Math.ceil(total/100)||1,Math.floor(page)||1))-1)*100;return list();});
$('ohif-toggle').onclick=()=>{
 if(!uid)return;
 const host=$('ohif-host'),viewer=$('ohif-iframe');
 if(host.hidden){viewer.src='/viewer?StudyInstanceUIDs='+encodeURIComponent(uid);host.hidden=false;$('ohif-toggle').textContent='Close OHIF viewer';$('ohif-status').textContent='Loading OHIF. Series and images load inside the viewer. If it stalls, use Check viewer again or the image preview below.';}
 else{viewer.removeAttribute('src');host.hidden=true;$('ohif-toggle').textContent='Open OHIF viewer';}
};
$('ohif-retry').onclick=()=>{if(uid)void openOHIFStatus(uid);};
$('ohif-iframe').addEventListener('load',()=>{if(!$('ohif-host').hidden)$('ohif-status').textContent='Viewer page loaded. Confirm that the series and images render below; page loading alone does not confirm image retrieval.';});
$('sql-candidate-refresh').onclick=safely(loadSQLCandidates);
$('sql-draft-prepare').onclick=safely(async()=>{
  if(!uid)throw Error('Open a study first');
  if(!window.confirm('Prepare a sanitized SQL draft for review only? This does NOT approve the study or authorize export.'))return;
  const selected=uid;
  const result=await api('/api/study/'+encodeURIComponent(selected)+'/sql-draft',{confirm:true});
  if(selected!==uid)return;
  $('sql-draft-text').value=result.draft||'';
  $('sql-draft-status').textContent=result.prepared
    ? 'Draft prepared for manual de-identification review. NOT approved or exportable.'
    : 'SQL draft unavailable.';
});
$('prepare').onclick=()=>{
 const reportID=Number($('report-choice').value);
 if(!uid||!Number.isSafeInteger(reportID)||reportID<=0){notice('Associate a valid report before preparation.',true);return;}
 void prepareSelectedReport(reportID);
};
$('view').onclick=viewImage;
$('dicom-series').onchange=()=>{showSeries();void viewImage();};
$('image-choice').onchange=()=>{$('frame').value=0;updateStack();void viewImage();};
$('frame').onchange=()=>{updateStack();void viewImage();};
$('dicom-prev').onclick=()=>navigateStack(-1);
$('dicom-next').onclick=()=>navigateStack(1);
$('dicom-position').oninput=()=>{
 const item=currentSeries()?.images[Number($('dicom-position').value)-1];
 if(item){$('image-choice').value=String(item.id);$('frame').value=0;updateStack();void viewImage();}
};
$('image-viewport').tabIndex=0;
$('image-viewport').addEventListener('wheel',event=>{
 if(event.ctrlKey||$('draw-mask').checked||!currentSeries()||event.deltaY===0)return;
 event.preventDefault();navigateStack(event.deltaY>0?1:-1);
},{passive:false});
$('image-viewport').addEventListener('keydown',event=>{
 if(['ArrowRight','ArrowDown'].includes(event.key)){event.preventDefault();navigateStack(1);}
 if(['ArrowLeft','ArrowUp'].includes(event.key)){event.preventDefault();navigateStack(-1);}
});$('report-choice').onchange=safely(async()=>{updateLegacyTXTAvailability($('report-choice').options.length);$('report-reviewed').checked=false;$('sanitized').value='';highlight();await candidateText();});
$('find-report').onclick=safely(async()=>{const reports=await api('/api/reports?q='+encodeURIComponent($('report-search').value));$('report-choice').replaceChildren();reports.forEach(addReport);updateLegacyTXTAvailability(0);$('report-reviewed').checked=false;$('sanitized').value='';highlight();await candidateText();});
$('sanitized').oninput=()=>{$('report-reviewed').checked=false;markReviewDirty();highlight();};
$('review-note').oninput=markReviewDirty;
for(const id of ['images-reviewed','report-reviewed'])$(id).onchange=markReviewDirty;
function reviewEditable(){return selectedStudy?.state==='review'&&!reviewSaving&&!active();}
function markReviewDirty(){reviewDirty=true;updateExportSelection();}
function updateReviewControls(){
 const editable=reviewEditable(),locked=['approved','exported'].includes(selectedStudy?.state);
 for(const id of ['sanitized','masks','review-note'])$(id).readOnly=!editable;
 for(const id of ['draw-mask','undo-mask','images-reviewed','report-reviewed'])$(id).disabled=!editable;
 for(const id of ['report-choice','find-report'])$(id).disabled=locked||reviewSaving||active();
 $('prepare').disabled=locked||reviewSaving||active()||!$('report-choice').value;
 $('approve').disabled=!editable;
 $('save-review-draft').disabled=!editable||!reviewDirty;
 $('save-mask-draft').disabled=!editable||!reviewDirty;
 $('reopen-review').hidden=!locked;
 $('reopen-review').disabled=reviewSaving||active();
 if($('review-final-button')){$('review-final-button').disabled=!selectedStudy||active()||reviewSaving;renderFinalSummary();}
 updateMaskControls();
}
async function saveReviewDraft(){
 if(!reviewEditable()||!reviewDirty)return;
 const study=uid,generation=reviewRequest;
 const body={review_revision:selectedStudy.review_revision,text:$('sanitized').value,rectangles:masks(),note:$('review-note').value};
 reviewSaving=true;cancelMaskDrag();updateExportSelection();
 try{
  const result=await api('/api/study/'+encodeURIComponent(study)+'/review-draft',body);
  if(study!==uid||generation!==reviewRequest)return;
  selectedStudy={...selectedStudy,sanitized:body.text,redactions:JSON.stringify(body.rectangles),review_note:body.note,review_revision:result.review_revision};
  reviewDirty=false;notice('Draft saved. Continue reviewing; saving does not approve or export this exam.');
 }finally{reviewSaving=false;updateExportSelection();}
}
$('reopen-review').onclick=safely(async()=>{
 const study=uid,generation=reviewRequest,revision=selectedStudy?.review_revision;
 if(reviewSaving||!revision)return;
 reviewSaving=true;updateExportSelection();
 try{
  await api('/api/study/'+encodeURIComponent(study)+'/reopen-review',{review_revision:revision});
  if(study!==uid||generation!==reviewRequest)return;
  await openStudy(study,'#review');notice('Approval revoked. Existing text and masks are preserved; make corrections and approve again.');
 }finally{reviewSaving=false;updateExportSelection();}
});
$('approve').onclick=safely(async()=>{
 const study=uid,generation=reviewRequest,revision=selectedStudy?.review_revision;
 if(!reviewEditable()||!revision)return;
 const body={review_revision:revision,text:$('sanitized').value,rectangles:masks(),note:$('review-note').value,images_reviewed:$('images-reviewed').checked,report_reviewed:$('report-reviewed').checked};
 reviewSaving=true;updateExportSelection();
 try{
  await api('/api/study/'+encodeURIComponent(study)+'/approve',body);
  if(study!==uid||generation!==reviewRequest)return;
  reviewDirty=false;
  await openStudy(study,'#review');notice('Study approved for export.');await refresh();
 }finally{reviewSaving=false;updateExportSelection();}
});
$('export').onclick=()=>queue('export',{uid});$('reload-review').onclick=()=>openStudy(uid,'#review');$('prepare-batch').onclick=()=>queue('prepare_candidates');$('export-batch').onclick=()=>queue('export_approved');$('job-filter').onchange=()=>{if(latest)renderJobs();};$('schema-search').oninput=()=>{if(latest)renderSQL(latest.sql);};
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

let historicalViewerUrl='';
let historicalSelectionSerial=0;
let historicalSelectedToken='';
function clearHistoricalSelection(){
 ++historicalSelectionSerial;
 historicalSelectedToken='';
 historicalViewerUrl='';
 $('historical-emr-detail').hidden=true;
 $('historical-emr-report').value='';
 $('historical-ohif-host').hidden=true;
 $('historical-ohif-iframe').removeAttribute('src');
 $('historical-ohif-toggle').hidden=true;
}
$('historical-ohif-toggle').onclick=()=>{
 const host=$('historical-ohif-host'),frame=$('historical-ohif-iframe');
 if(!historicalViewerUrl||!historicalSelectedToken)return;
 if(host.hidden){
   frame.src=historicalViewerUrl;host.hidden=false;
   $('historical-ohif-toggle').textContent='Close historical OHIF viewer';
 }else{
   frame.removeAttribute('src');host.hidden=true;
   $('historical-ohif-toggle').textContent='Open this historical study in OHIF';
 }
};
async function loadHistoricalEMR(){
 const panel=$('historical-emr-leads');
 clearHistoricalSelection();
 const serial=historicalSelectionSerial;
 panel.replaceChildren();
 $('historical-emr-status').textContent='Loading independent historical studies; no active catalog selection is used.';
 const result=await api('/api/historical-emr/candidates');
 if(serial!==historicalSelectionSerial)return;
 $('historical-emr-status').textContent=result.count+' historical studies with separate, unconfirmed EMR reports. Choose a historical study below.';
 for(const lead of result.candidates){
  const line=el('div',undefined,'card');
  line.append(el('h4',lead.label),
    el('p','Historical study '+(lead.study_ref||'')+' | '+(lead.modality||'')+
      ' | '+(lead.study_date||'')+' | '+lead.instance_count+' images'),
    el('p','Not linked to the regular study currently selected above.'),
    button('Select THIS historical study and its EMR report',safely(async()=>{
      clearHistoricalSelection();
      const serial=historicalSelectionSerial;
      historicalSelectedToken=lead.token;
      $('historical-emr-status').textContent='Verifying selected historical study '+lead.study_ref+' and its report…';
      const preview=await api('/api/historical-emr/candidate/'+encodeURIComponent(lead.token));
      if(serial!==historicalSelectionSerial||historicalSelectedToken!==lead.token)return;
      if(preview.historical_study_ref!==lead.study_ref)throw Error('Historical study identity changed; reload candidates.');
      $('historical-emr-title').textContent='Historical study '+preview.historical_study_ref+' — '+preview.report_ref;
      $('historical-emr-evidence').textContent='Evidence: '+preview.evidence.join(' + ')+'. Not independently confirmed by reviewer.';
      $('historical-emr-dicom').textContent='DICOM patient: '+preview.dicom_patient_name+
       ' | DICOM study date: '+preview.dicom_study_date+' | Modality: '+preview.dicom_modality+
       ' | '+preview.dicom_instances+' historical images.';
      $('historical-emr-identity').textContent='EMR extracted patient: '+preview.emr_patient_name+
       ' | DICOM patient: '+preview.dicom_patient_name+
       '. Compare against the full report and OHIF headers. Do not associate if they disagree.';
      $('historical-emr-report').value=preview.report_text;
      historicalViewerUrl=preview.ohif_available?preview.viewer_url:'';
      $('historical-ohif-toggle').hidden=!historicalViewerUrl;
      $('historical-ohif-toggle').textContent='Open THIS historical study in OHIF';
      $('historical-emr-status').textContent='Selected historical study '+preview.historical_study_ref+
       '. The regular active DICOM review is independent.';
      $('historical-emr-detail').hidden=false;
      $('historical-emr-detail').scrollIntoView({behavior:'smooth'});
    })));
  panel.append(line);
 }
}
$('historical-emr-refresh').onclick=safely(loadHistoricalEMR);
$('errors-refresh').onclick=safely(async()=>{const rows=await api('/api/source-errors');$('source-errors').replaceChildren();for(const row of rows)$('source-errors').append(el('p',row.path+' | '+row.kind+' | '+(JSON.parse(row.metadata).error||'Check source format, encoding and access')));if(!rows.length)$('source-errors').textContent='No source errors in the active inventory.';});
let reviewDirty=false,reviewSaving=false;
let selectedStudy=null,txtSerial=0,txtOffset=0,txtTotal=0,txtSelected=null,preparedJob=null;
function clearActiveStudy(){
 if($('workspace-final'))$('workspace-final').open=false;
 imageController?.abort();
 cancelMaskDrag();if($('mask-scope'))$('mask-scope').value='image';if($('image-window')){$('image-window').value='auto';$('window-center').hidden=$('window-width').hidden=true;}imageReady=false;zoom=1;$('draw-mask').checked=false;applyZoom();
 reviewDirty=false;
 clearSuggestions();clearBrowserPreview();$('browser-note').value='';selectedStudy=null;txtSelected=null;txtOffset=0;++txtSerial;++imageRequest;++reconcileSerial;
 if(imageURL){URL.revokeObjectURL(imageURL);imageURL=null;}
 $('image').removeAttribute('src');$('mask-canvas').width=0;
 $('ohif-iframe').removeAttribute('src');$('ohif-host').hidden=true;$('ohif-pilot-panel').hidden=true;
 dicomSeries=new Map();dicomFrames=new Map();inspectedFrames=new Set();
 for(const id of ['original','sanitized','sql-candidate-original','sql-candidate-readable','sql-draft-text','sql-draft-status','review-note','txt-note'])$(id).value='';
 for(const id of ['image-choice','dicom-series','report-choice','sql-candidate-list','txt-candidate-rows','review-report-match-results'])$(id).replaceChildren();
 for(const id of ['images-reviewed','report-reviewed','txt-confirm'])$(id).checked=false;
 $('masks').value='[]';$('txt-associate').disabled=true;
 $('review-title').textContent='Loading selected study…';$('review-state').textContent='';$('image-info').textContent='';
 $('txt-preview-evidence').textContent='';$('txt-coverage').textContent='Loading selected study…';
 $('sql-candidate-format-status').textContent='';$('sql-draft-status').textContent='';
 clearHistoricalSelection();updateExportSelection();
}
function setupWorkflowPanels(){
 const saveDraft=button('Save draft',safely(saveReviewDraft));saveDraft.id='save-review-draft';
 $('approve').before(saveDraft);
 $('export').hidden=true;
 $('administration').querySelector('.panel').append($('prepare-batch'),$('export-batch'));
 const imagePanel=document.querySelector('.review-grid > .panel');
 const maskHelp=el('div',undefined,'mask-help');
 const maskStatus=el('p');maskStatus.id='mask-status';maskStatus.setAttribute('role','status');
 const maskSummary=el('p');maskSummary.id='mask-summary';
 const reviewStep=button('Choose report / prepare draft',()=>{setWorkspaceReportMode(selectedStudy?.report_id?'draft':'find');$('workspace-reports').scrollIntoView({block:'start'});});reviewStep.id='mask-review-step';
 const saveMasks=button('Save draft',safely(saveReviewDraft));saveMasks.id='save-mask-draft';
 maskHelp.append(maskStatus,maskSummary,reviewStep,saveMasks);$('image-viewport').before(maskHelp);
 const coordinates=el('details');coordinates.append(el('summary','Advanced mask document'));
 const maskLabel=$('masks').closest('label'),maskExplanation=maskLabel.nextElementSibling;
 maskLabel.before(coordinates);coordinates.append(maskLabel,maskExplanation);

 const matchBody=el('div');matchBody.id='matching-reports';
 matchBody.append(...$('matching').childNodes);
 const matchImages=el('div');matchImages.id='matching-images';
 const imageComparison=el('details');imageComparison.id='matching-image-comparison';imageComparison.append(el('summary','Compare exam images'),matchImages);
 $('matching').append(imageComparison,matchBody);
 imagePanel.dataset.phase='images privacy';
 const reportPanel=document.querySelector('.review-grid > .panel:nth-child(2)');
 reportPanel.dataset.phase='privacy';
 $('review').querySelector('.section-head').dataset.phase='privacy';
 for(const id of ['txt-candidate-panel','review-report-overview','sql-candidate-panel'])$(id).dataset.phase='reports';
 for(const id of ['legacy-txt-tools'])$(id).dataset.phase='privacy';
 $('legacy-txt-tools').open=true;
 $('ohif-pilot-panel').dataset.phase='images';
 for(const n of document.querySelectorAll('.approval-panel,.approval-actions'))n.dataset.phase='privacy';
 $('next-reports').onclick=()=>{location.hash='#matching';};
 $('next-anonymize').onclick=()=>{location.hash='#review';};
 $('next-export').onclick=()=>{location.hash='#packages';};
 const tabs=[...document.querySelectorAll('#primary-navigation a')];
 tabs.forEach((tab,index)=>tab.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const i=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[i].focus();location.hash=tabs[i].getAttribute('href');}));
 $('txt-search').onclick=safely(()=>{txtOffset=0;return loadTXTCandidates(true);});
 $('txt-sort').onchange=$('txt-search').onclick;
 $('txt-prev').onclick=safely(()=>{txtOffset=Math.max(0,txtOffset-100);return loadTXTCandidates(true);});
 $('txt-next').onclick=safely(()=>{txtOffset+=100;return loadTXTCandidates(true);});
 $('txt-confirm').onchange=()=>{$('txt-associate').disabled=!txtSelected||!$('txt-confirm').checked||active();};
 $('txt-associate').onclick=safely(async()=>{
  const study=uid,report=txtSelected,generation=reviewRequest;
  if(!report)throw Error('Preview and select one report first');
  await api('/api/study/'+encodeURIComponent(study)+'/txt-association',{report_id:report,note:$('txt-note').value,confirm:$('txt-confirm').checked,review_revision:selectedStudy?.review_revision});
  if(uid!==study||generation!==reviewRequest)return;
  await openStudy(study,'#review');
  $('legacy-txt-tools').open=true;
  notice('TXT report associated. Continue to Anonymize & review and prepare the draft.');
  location.hash='#review';
 });
 $('choose-output').onclick=()=>{$('mount-slot').value='output';$('mount-form').closest('details').open=true;location.hash='#source-settings';};
 $('export-selected-study').onclick=()=>{if(uid&&!reviewDirty&&!reviewSaving&&selectedStudy?.state==='approved')queue('export',{uid});};
}
async function loadTXTCandidates(manual=false){
 const study=uid,generation=reviewRequest,serial=++txtSerial;if(!study)return;
 const query=new URLSearchParams({sort:$('txt-sort').value,offset:txtOffset});
 if(manual)query.set('q',$('txt-query').value);
 $('txt-coverage').textContent='Searching indexed TXT reports for this study…';
 try{
  const data=await api('/api/study/'+encodeURIComponent(study)+'/txt-candidates?'+query);
  if(serial!==txtSerial||study!==uid||generation!==reviewRequest)return;
  if(!manual)$('txt-query').value=data.surname;
  txtTotal=data.total;txtSelected=null;$('txt-associate').disabled=true;$('txt-confirm').checked=false;
  $('txt-candidate-rows').replaceChildren();
  for(const item of data.rows){
   const row=el('tr'),select=el('input');select.type='checkbox';select.setAttribute('aria-label','Select '+item.path);
   cell(row,'').append(select);cell(row,item.path);cell(row,item.name||'No explicit patient name');cell(row,item.reason);
   const preview=async()=>{
    txtSelected=null;$('txt-confirm').checked=false;$('txt-associate').disabled=true;$('original').value='';
    $('txt-preview-evidence').textContent='Checking source identity and report…';
    const selectedSerial=++txtSerial;
    const result=await api('/api/study/'+encodeURIComponent(study)+'/txt-report/'+item.id);
    if(generation!==reviewRequest||study!==uid||selectedSerial!==txtSerial)return;
    for(const box of $('txt-candidate-rows').querySelectorAll('input'))box.checked=false;
    $('txt-preview-evidence').textContent=result.message+(result.conflicts.length?' | '+result.conflicts.join(', '):' | Report name: '+(result.name||'unknown')+' | Exam date: '+(result.exam_date||'unknown'));
    if(result.preview_allowed){select.checked=true;txtSelected=item.id;$('original').value=result.text;}else notice(result.message,true);
   };
   select.onchange=safely(async()=>{if(select.checked)await preview();else{++txtSerial;txtSelected=null;$('txt-confirm').checked=false;$('txt-associate').disabled=true;$('original').value='';}});
   cell(row,'').append(button('Preview',safely(preview)));$('txt-candidate-rows').append(row);
  }
  $('txt-coverage').textContent=data.total+' TXT candidates · Last name: '+(data.surname||'not structured; use a manual search')+' · Showing '+data.rows.length+' · Suggestions require manual verification. SQL coverage is shown separately.';
  $('txt-prev').disabled=txtOffset===0;$('txt-next').disabled=txtOffset+100>=txtTotal;
 }catch(error){if(serial===txtSerial&&study===uid&&generation===reviewRequest)$('txt-coverage').textContent='TXT search unavailable: '+error.message;}
}
async function prepareSelectedReport(reportID){
 if((reviewDirty||selectedStudy?.state==='review')&&!window.confirm('Preparing a new draft replaces saved and unsaved review text and masks. Continue?'))return;
 const study=uid,result=await queue('prepare',{uid:study,report_id:reportID});
 if(result){preparedJob={id:result.id,uid:study};if(uid===study)reviewDirty=false;}
}
async function finishPreparation(){
 if(!preparedJob)return;const job=latest.jobs.find(j=>j.id===preparedJob.id);
 if(!job||['queued','running'].includes(job.state))return;
 const target=preparedJob;preparedJob=null;
 if(target.uid!==uid)return;
 if(job.state!=='completed'){notice(job.message,true);return;}
 await openStudy(target.uid,'#review');notice('Draft ready. Review the sanitized report and every image/frame before approval.');
}
function updateExportSelection(){
 $('export-destination').textContent='Current writable output: '+(latest?.output||'Check source configuration');
 const s=selectedStudy;$('export-selected').textContent=s?'Selected: '+(s.accession||s.subject)+' | '+(s.name||s.patient||s.subject)+' | State: '+(labels[s.state]||s.state):'No study selected. Start with Images.';
 $('export-selected-study').disabled=!s||s.state!=='approved'||reviewDirty||reviewSaving||active();
 updateReviewControls();
}

function setWorkspaceReportMode(mode){
 workspaceReportMode=mode;
 if(!$('workspace-report-mode'))return;
 $('matching').hidden=mode!=='find';$('linked-report-content').hidden=mode!=='draft';
 $('find-report-tab').setAttribute('aria-pressed',String(mode==='find'));
 $('draft-report-tab').setAttribute('aria-pressed',String(mode==='draft'));
}
function renderMaskList(){
 const list=$('mask-list');if(!list)return;list.replaceChildren();
 let entries;try{entries=maskEntries();}catch(_error){return;}
 $('undo-mask').disabled=!reviewEditable()||!entries.length;
 entries.forEach((m,index)=>{
  const row=el('div',undefined,'mask-entry');
  const series=[...dicomSeries.entries()].find(([key])=>key===m.series_uid)?.[1];
  const image=series?.images.find(i=>i.sop===m.sop_uid);
  const scope=m.scope==='exam'?'Entire exam (existing mask)':m.scope==='series'?'Every image/frame in '+(series?.description||m.series_uid):(series?.description||m.series_uid)+' · '+(image?.label||m.sop_uid)+' · Frame '+(m.frame+1);
  row.append(el('span',(index+1)+'. '+scope+' · '+m.rect.join(', ')));
  const remove=button('Remove',()=>{if(!reviewEditable())return;const current=maskEntries();current.splice(index,1);writeMasks(current);$('images-reviewed').checked=false;markReviewDirty();drawMasks();});remove.disabled=!reviewEditable();remove.setAttribute('aria-label','Remove mask '+(index+1));row.append(remove);list.append(row);
 });
}
function renderFinalSummary(){
 if(!$('final-review-summary'))return;
 const s=selectedStudy;let entries=[];try{entries=maskEntries();}catch(_error){}
 $('final-review-summary').replaceChildren(el('strong',s?(s.name||s.patient)+' · '+(s.accession||s.subject):'No exam selected'),el('p','Report: '+(s?.report_id?'Linked report #'+s.report_id:'No report linked')),el('p',entries.length+' masks: '+entries.filter(m=>m.scope==='image').length+' image/frame, '+entries.filter(m=>m.scope==='series').length+' series, '+entries.filter(m=>m.scope==='exam').length+' entire exam'),el('p','Destination: '+(latest?.output||'Not configured')));
 $('workspace-save-state').textContent=reviewSaving?'Saving…':reviewDirty?'Unsaved changes':s?.state==='approved'?'Approved · ready to export':s?.state==='exported'?'Exported':s?.state==='review'?'Draft saved on server · not approved':s?.report_id?'Report linked · prepare privacy draft':'Choose and verify a report';
 $('review-final-button').disabled=!s||!['review','approved','exported'].includes(s.state)||active()||reviewSaving;
}
function setupSingleWorkspace(){
 const saveDraft=$('save-review-draft');
 const grid=document.querySelector('.review-grid'),imagePanel=grid.children[0],reportPanel=grid.children[1];
 grid.id='single-workspace';imagePanel.id='workspace-images';imagePanel.querySelector('h3').textContent='Images & pixel masking';
 const imageControls=$('dicom-series').closest('.toolbar'),seriesLabel=$('dicom-series').closest('label');
 const jump=el('details');jump.id='image-jump';jump.append(el('summary','Jump to instance / frame'));seriesLabel.after(jump);jump.append($('image-choice').closest('label'),$('frame').closest('label'),$('view'));
 const viewerHelp=$('image-viewport').previousElementSibling;
 $('image-viewport').after($('dicom-position').closest('.dicom-scroll-controls'),$('image-info'),$('dicom-review-progress'),viewerHelp);

 const right=el('div');right.id='workspace-reports';
 const modes=el('div',undefined,'toolbar');modes.id='workspace-report-mode';
 const find=button('Find / change report',()=>{++workspaceModeRequest;setWorkspaceReportMode('find');});find.id='find-report-tab';
 const draft=button('Linked report & privacy draft',()=>{++workspaceModeRequest;setWorkspaceReportMode('draft');});draft.id='draft-report-tab';modes.append(find,draft);
 const linked=el('div');linked.id='linked-report-content';linked.append($('legacy-txt-tools'),reportPanel);
 right.append(modes,$('matching'),linked);grid.append(right);
 $('matching-image-comparison').remove();
 const select=el('select');select.id='browser-report-choice';select.setAttribute('aria-label','Choose a report to preview');
 select.onchange=safely(()=>select.value===''?clearBrowserPreview():previewBrowserItem(Number(select.value)));
 $('browser-split').before(select);
 const all=el('details');all.id='report-results-details';all.append(el('summary','All report results & matching evidence'),$('browser-results'));$('browser-split').after(all);
 $('report-browser').querySelector('h3').textContent='Find a report';
 $('report-browser').querySelector('p').textContent='Preview a report, compare its patient and exam details with the banner, then explicitly link it.';
 const searchControls=$('browser-query').closest('.toolbar');const filters=el('details');filters.append(el('summary','Search / filter report sources'),searchControls);$('browser-status').before(filters);
 $('browser-associate').textContent='Use this report for this exam';
 $('prepare').textContent='Prepare privacy draft';
 const originalDetails=el('details');originalDetails.append(el('summary','Linked original report'),$('original').closest('label'));reportPanel.querySelector('h3').after(originalDetails);
 const scopeLabel=el('label','Apply NEW masks to');const scope=el('select');scope.id='mask-scope';
 for(const [value,label]of [['image','This image / frame only'],['series','Every image and frame in this series']]){const o=el('option',label);o.value=value;scope.append(o);}scopeLabel.append(scope);$('image-viewport').after(scopeLabel);
 scope.onchange=()=>{cancelMaskDrag();drawMasks();};
 const masksList=el('div');masksList.id='mask-list';scopeLabel.after(masksList);
 const advanced=$('masks').closest('details');advanced.querySelector('p').textContent='Version 2 stores scoped masks. Legacy rectangle lists retain their entire-exam scope. Color masking and defacing are unsupported.';
 const windowControls=el('div',undefined,'toolbar');const preset=el('select');preset.id='image-window';preset.setAttribute('aria-label','Image window preset');
 for(const [value,label]of [['auto','Auto contrast'],['dicom','DICOM window'],['lung','CT lung'],['soft','CT soft tissue'],['bone','CT bone'],['custom','Custom window']]){const o=el('option',label);o.value=value;preset.append(o);}
 const center=el('input');center.id='window-center';center.type='number';center.value='40';center.setAttribute('aria-label','Window center');
 const width=el('input');width.id='window-width';width.type='number';width.min='1';width.value='400';width.setAttribute('aria-label','Window width');
 windowControls.append(preset,center,width,button('Apply window',()=>void viewImage()));$('image-viewport').before(windowControls);
 preset.onchange=()=>{center.hidden=width.hidden=preset.value!=='custom';void viewImage();};center.hidden=width.hidden=true;
 const finish=el('details');finish.id='workspace-final';finish.append(el('summary','Final review, approval & export'));
 const summary=el('div');summary.id='final-review-summary';finish.append(summary,document.querySelector('.approval-panel'),document.querySelector('.approval-actions'),$('packages'));
 const footer=el('div',undefined,'workspace-footer');const state=el('span');state.id='workspace-save-state';state.setAttribute('role','status');const finalButton=button('Review & approve →',()=>{finish.open=true;renderFinalSummary();finish.scrollIntoView({block:'start'});},false);finalButton.id='review-final-button';
 footer.append(state,saveDraft,finalButton);$('review').append(footer,finish);
 $('review').querySelector('.section-head').hidden=true;
 $('ohif-pilot-panel').hidden=true;
 setWorkspaceReportMode('find');renderFinalSummary();
}

setupResearchWorkspace();setupReportBrowser();setupWorkflowPanels();makeStudyReviewPrimary();setupSingleWorkspace();showWorkspaceScreen();refresh();health();loadResearchQueue();setInterval(refresh,10000);setInterval(health,60000);
try{const restored=sessionStorage.getItem('hoag-selected-exam');if(restored&&/^[0-9.]+$/.test(restored))void openStudy(restored,window.location.hash||'#matching');}catch(_error){}
