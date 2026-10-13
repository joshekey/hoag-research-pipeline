/* Arrange existing, verified actions into a focused study-first workspace. */
let reportTab='suggestions',studyListTab='catalog';
function activateReportTab(tab){
 reportTab=tab;
 for(const [name,id] of [['suggestions','suggestion-pane'],['search','report-discovery'],['preview','report-preview']])$(id).hidden=name!==tab;
 for(const b of document.querySelectorAll('#report-tabs button'))b.setAttribute('aria-pressed',String(b.dataset.tab===tab));
}
function activateStudyList(tab){
 studyListTab=tab;$('catalog-pane').hidden=tab!=='catalog';$('queue-pane').hidden=tab!=='queue';
 $('show-catalog').setAttribute('aria-pressed',String(tab==='catalog'));$('show-queue').setAttribute('aria-pressed',String(tab==='queue'));
}
function setupWorkbench(){
 const shell=el('section');shell.id='research-workbench';
 const browser=el('aside',undefined,'study-browser'),canvas=el('div',undefined,'image-workspace'),reports=el('aside',undefined,'report-workspace');
 shell.append(browser,canvas,reports);document.querySelector('main').insertBefore(shell,document.querySelector('footer'));
 const listHead=el('div',undefined,'pane-heading');listHead.append(el('small','STUDY LIBRARY'),el('h2','Indexed DICOM'));
 const listTabs=el('div',undefined,'local-tabs');
 const all=button('All studies',()=>activateStudyList('catalog'));all.id='show-catalog';
 const queue=button('Review queue',()=>activateStudyList('queue'));queue.id='show-queue';listTabs.append(all,queue);
 browser.append(listHead,listTabs,$('studies'));
 const queuePane=$('studies').querySelector('details');queuePane.id='queue-pane';queuePane.open=true;
 const catalog=el('div');catalog.id='catalog-pane';
 for(const child of [...$('studies').children])if(child!==queuePane)catalog.append(child);
 $('studies').append(catalog);
 const filters=catalog.querySelector('.filters'),advanced=el('details');advanced.id='catalog-advanced';advanced.append(el('summary','Date and sorting filters'));
 for(const node of [...filters.children].slice(2))advanced.append(node);filters.after(advanced);
 const oldGrid=document.querySelector('.review-grid'),imagePanel=oldGrid.firstElementChild,privacyPanel=oldGrid.lastElementChild;
 imagePanel.id='image-pane';imagePanel.removeAttribute('data-phase');
 const empty=el('div',undefined,'image-empty');empty.id='image-empty';empty.append(el('small','RESEARCH WORKSPACE'),el('h2','Start with a study'),el('p','Choose an indexed DICOM examination on the left. Its images and suggested SQL / TXT reports will open here.'));
 canvas.append($('current-study-banner'),empty,imagePanel);
 const reportHead=el('div',undefined,'pane-heading');reportHead.id='report-pane-heading';reportHead.append(el('small','REPORT MATCHING'),el('h2','Find the right report'));
 const tabs=el('div',undefined,'local-tabs');tabs.id='report-tabs';
 for(const [value,label] of [['suggestions','Suggested'],['search','Search all'],['preview','Compare']]){const b=button(label,()=>activateReportTab(value));b.dataset.tab=value;tabs.append(b);}
 const sql=el('div',undefined,'sql-readiness');sql.id='sql-readiness';sql.setAttribute('role','status');sql.textContent='Checking SQL report access…';
 const fix=button('SQL setup / rebuild',()=>{location.hash='#overview';});fix.id='sql-fix';
 reports.append(reportHead,sql,fix,tabs,$('matching'));
 const suggestions=$('suggestion-list').closest('.panel');suggestions.id='suggestion-pane';
 const source=$('report-browser'),discovery=el('div');discovery.id='report-discovery';
 const preview=el('div');preview.id='report-preview';
 let reading=false;
 for(const child of [...source.children]){
  if(child.id==='browser-identity')reading=true;
  (reading?preview:discovery).append(child);
 }
 source.append(discovery,preview);
 const diagnostic=el('article');diagnostic.id='diagnostic-report';diagnostic.setAttribute('aria-label','Diagnostic radiology report');
 const raw=$('browser-text').closest('label'),rawDetails=el('details');rawDetails.className='original-source';rawDetails.append(el('summary','Original source text / XML'),raw);preview.insertBefore(diagnostic,$('browser-confirm').closest('label'));diagnostic.after(rawDetails);renderDiagnosticReport(null);
 const privacy=el('div');privacy.id='privacy-dock';
 privacy.append($('legacy-txt-tools'),$('txt-original-panel'),privacyPanel,...document.querySelectorAll('.approval-panel,.approval-actions'));
 reports.append(privacy);
 const actions=el('div',undefined,'workbench-actions');actions.append($('next-reports'),$('next-anonymize'),$('next-export'));
 canvas.append(actions);
 shell.append(browser,canvas,reports);document.querySelector('main').insertBefore(shell,document.querySelector('footer'));
 // Operational controls stay outside the research workbench.
 const errors=$('source-errors').closest('section');errors.id='source-attention';$('administration').append(errors);
 setupOperations();
 const imageAdvanced=el('details',undefined,'image-advanced');imageAdvanced.append(el('summary','Pixel masks and image privacy tools'));
 const masks=$('masks').closest('label'),maskHelp=masks.nextElementSibling;imageAdvanced.append(masks);if(maskHelp)imageAdvanced.append(maskHelp);
 imagePanel.append(imageAdvanced);
 const ohif=$('ohif-pilot-panel');ohif.removeAttribute('data-phase');imagePanel.prepend(ohif);$('ohif-toggle').textContent='Open OHIF viewer ↗';$('ohif-toggle').title='Open this study in a separate browser tab';
 $('report-search').closest('.toolbar').hidden=true;
 $('legacy-txt-tools').open=true;
 $('study-rows').addEventListener('click',()=>activateReportTab('suggestions'));
 $('research-queue-rows').addEventListener('click',()=>activateReportTab('suggestions'));
 activateStudyList('catalog');activateReportTab('suggestions');
 $('search').placeholder='Find a patient or examination';
 $('browser-query').placeholder='Name, accession or report text';
 $('next-reports').classList.add('secondary');
 $('next-anonymize').textContent='Continue to privacy review →';
 $('next-export').textContent='Continue to export →';
 document.querySelector('footer').textContent='HOAG Research · Restricted workspace · Processing stays on this server';
 setupGuidedComparison(shell,browser,canvas);
}

function setupGuidedComparison(shell,browser,canvas){
 const drawer=el('details');drawer.id='guided-queue';drawer.open=true;
 drawer.append(el('summary','Study queue · Choose or change examination'),browser);
 const context=el('div',undefined,'guided-context');context.id='guided-context';
 context.append($('current-study-banner'));
 const identity=el('p');identity.id='export-identity';context.append(identity);
 const compare=el('div');compare.id='guided-comparison';context.append(compare);
 shell.prepend(drawer,context);
 const privacyStatus=el('div',undefined,'privacy-status');privacyStatus.id='privacy-status';privacyStatus.setAttribute('role','status');
 const prepareNow=button('Prepare sanitized report',safely(()=>prepareSelectedReport(Number(selectedStudy?.report_id))),false);prepareNow.id='prepare-associated-now';
 $('privacy-dock').prepend(privacyStatus,prepareNow);
 const originalDetails=el('details',undefined,'original-source');originalDetails.append(el('summary','Original associated report (identifying source)'),$('txt-original-panel'));prepareNow.after(originalDetails);
 $('redaction-preview').closest('details').open=true;
 $('sanitized').placeholder='The sanitized draft will appear here after preparation finishes.';
 const imageTools=el('details',undefined,'image-advanced');imageTools.append(el('summary','Choose image series or frame'));
 const seriesToolbar=$('dicom-series').closest('.toolbar');seriesToolbar.before(imageTools);imageTools.append(seriesToolbar);
 const privacyTools=$('masks').closest('details');privacyTools.append($('draw-mask').closest('label'),$('undo-mask'));
 $('image-empty').querySelector('p').textContent='Choose an examination from the study queue above. Compare its patient and exam details with an independent report before associating.';
 const skip=button('Skip to next unassociated study',safely(openNextAssociation));skip.id='guided-skip';canvas.querySelector('.workbench-actions').prepend(skip);
 const nextLabel=el('label',undefined,'check'),next=el('input');next.type='checkbox';next.id='associate-next';
 nextLabel.append(next,document.createTextNode(' After association, open the next unassociated study'));
 $('browser-associate').after(nextLabel);
 const downloads=el('div',undefined,'panel');downloads.append(el('h3','Patient identity crosswalk'),el('p','Restricted identifying data: original patient names and MRNs mapped to anonymous names and IDs. One row per completed export. Download separately from research packages. Older exports without a recorded mapping are explicitly marked.'));
 const download=button('Download patient crosswalk (CSV)',safely(async()=>{
  download.disabled=true;
  try{const response=await fetch('/api/exports/crosswalk',{method:'POST',headers:{'X-CSRF-Token':csrf}});
   if(!response.ok)throw Error('Crosswalk download failed');
   const url=URL.createObjectURL(await response.blob()),a=el('a');a.href=url;a.download='patient-crosswalk.csv';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
   notice('Restricted crosswalk downloaded. This download has been logged.');
  }finally{download.disabled=false;}
 }));download.id='download-crosswalk';downloads.append(download);$('packages').append(downloads);
 renderGuidedComparison();
}
async function openNextAssociation(afterAssociation=false){
 const current=uid,generation=reviewRequest;
 let queue=await api('/api/research-queue?offset=0&bucket='),pageOffset=0;
 if(current!==uid||generation!==reviewRequest)return;
 let index=queue.rows.findIndex(s=>s.uid===current);
 while(afterAssociation!==true&&current&&index<0&&pageOffset+100<queue.total){
  pageOffset+=100;queue=await api('/api/research-queue?offset='+pageOffset+'&bucket=');
  if(current!==uid||generation!==reviewRequest)return;index=queue.rows.findIndex(s=>s.uid===current);
 }
 let next=afterAssociation===true?queue.rows[0]:queue.rows[index+1];
 if(index>=0&&!next&&pageOffset+100<queue.total){queue=await api('/api/research-queue?offset='+(pageOffset+100)+'&bucket=');if(current!==uid||generation!==reviewRequest)return;next=queue.rows[0];}
 if(next)await openStudy(next.uid,'#studies');
 else{ $('guided-queue').open=true;notice('No other unassociated study is available in this queue. Choose a study or refresh report suggestions.'); }
}
function renderGuidedComparison(){
 const box=$('guided-comparison');if(!box)return;box.replaceChildren();
 const s=typeof selectedStudy==='undefined'?null:selectedStudy;
 $('export-identity').textContent=s?'Export name and ID: '+s.subject+' · '+(s.linked_studies??1)+' linked study/studies. MRN and issuing system determine the patient identity.':'';
 if(!s)return;
 const r=browserSelection?.identity||{},m=s.image_identity||{};
 box.append(el('h3','Verify the same patient and examination'));
 const table=el('table'),head=el('thead'),hr=el('tr');
 ['Check','Image record','Report record'].forEach(t=>hr.append(el('th',t)));head.append(hr);table.append(head);
 const body=el('tbody');
 for(const [label,left,right] of [['Patient',displayPatient(s.name),displayPatient(r.name)],['MRN / Patient ID',s.patient,r.patient_id||r.mrn],['Date of birth',m.PatientBirthDate,r.dob],['Exam date',s.date,r.exam_date],['Exam / body area',[s.modality,m.BodyPartExamined].filter(Boolean).join(' / '),[...(r.modalities||[]),...(r.anatomy||[]),browserSelection?.procedure||r.description||r.modality||r.body_part].filter(Boolean).join(' / ')],['Accession',s.accession,r.accession]]){
  const tr=el('tr');[label,left||'Not available',right||(browserSelection?'Not available — verify source':'Choose a report')].forEach(v=>tr.append(el('td',v)));body.append(tr);
 }
 table.append(body);box.append(table,el('p','Compare both original records. Missing details or differences require investigation; a suggestion is not a verified match.','muted'));
 const descriptionRow=el('tr');
 const descriptions=[...new Set([browserSelection?.exam_description,browserSelection?.procedure].filter(Boolean))].join(' / ');
 ['Study / exam description',m.StudyDescription||'Not available in DICOM',descriptions||(browserSelection?'Not available in report — verify source':'Choose a report')].forEach(value=>descriptionRow.append(el('td',value)));
 body.insertBefore(descriptionRow,body.children[4]);
 if(browserSelection?.date&&!r.exam_date)box.append(el('p','Report result date: '+browserSelection.date+' (not a verified examination date).','muted'));
}

let preparingRequest=false;
function updatePrivacyStatus(){
 const status=$('privacy-status');if(!status)return;
 const study=selectedStudy,job=latest?.jobs?.find(j=>j.kind==='prepare'&&(j.study_uid===uid||j.id===preparedJob?.id));
 const working=preparingRequest||Boolean(job&&['queued','running'].includes(job.state))||Boolean(!job&&preparedJob?.uid===uid);
 let message=!study?'Choose a study first.':!study.report_id?'Association required: choose a report, compare the patient and examination, then confirm Associate report.':working?'Preparing sanitized report — '+(job?.message||'request submitted; please wait')+'. This can take time; the draft will appear automatically.':study.last_error?'Preparation needs attention: '+study.last_error:!$('sanitized').value?'Report associated. Next: select Prepare sanitized report. No sanitized draft is ready yet.':'Sanitized draft ready below. Review the highlighted draft and every image before approval.';
 if(job&&['failed','cancelled'].includes(job.state)&&!$('sanitized').value)message='Preparation '+job.state+': '+job.message+'. Retry preparation after resolving the issue.';
 status.textContent=message;status.setAttribute('aria-busy',String(working));
 $('prepare-associated-now').disabled=!study?.report_id||active()||working;
 $('prepare-associated-now').hidden=Boolean(study?.sanitized)&&!working;
 $('sanitized').setAttribute('aria-busy',String(working));
}

async function checkSQLReadiness(){
 try{const data=await api('/api/sql-readiness'),state=data.broker;
 const details=state.available&&state.reports?'SQL ready · '+state.reports.toLocaleString()+' reports':!data.dumps.length?'SQL setup needed · No SQL dump indexed. Connect its source in Operations and include its folder in the source index.':'SQL unavailable · '+state.message+'. Rebuild the report index in Operations.';
 $('sql-readiness').textContent=details;$('sql-readiness').classList.toggle('ready',Boolean(state.available&&state.reports));$('sql-fix').hidden=Boolean(state.available&&state.reports);
 if(data.last_rebuild?.state==='failed')$('sql-readiness').textContent+=' Last rebuild failed: '+data.last_rebuild.message;
 }catch(error){$('sql-readiness').textContent='SQL status could not be checked: '+error.message;$('sql-fix').hidden=false;}
}

function setupOperations(){
 const source=$('source-settings'),head=el('div',undefined,'source-choices');
 for(const [slot,title,description] of [['imaging','DICOM images','The share holding your original studies.'],['bulk','TXT reports','The share holding text report files.'],['database','EMR export','The share holding the .sql database export.'],['output','Research output','A separate writable destination.']]){
  const card=el('div',undefined,'source-choice');card.append(el('h3',title),el('p',description),button('Connect / update',()=>{ $('mount-slot').value=slot;$('mount-form').closest('details').open=true;$('mount-form').scrollIntoView({behavior:'smooth'});$('mount-unc').focus();}));head.append(card);
 }
 source.querySelector('.section-head').after(head);
 const folder=$('folder-root').closest('details');folder.open=true;
 folder.querySelector('summary').textContent='2 · Add report folders';
 folder.querySelector('p').textContent='Select folders containing TXT reports or the SQL export. Subfolders are included. Adding reports keeps the existing DICOM catalog.';
 const intro=el('p','After connecting TXT or the EMR export share, select its folder and choose Add TXT / SQL. This preserves your indexed DICOM. Then select the discovered export and import its SQL reports.','source-guide');folder.prepend(intro);
 $('scan').hidden=true;
 const full=$('reindex-catalog').closest('.panel'),advanced=el('details');advanced.id='advanced-indexing';full.append(advanced);advanced.append(el('summary','Advanced: full scans and reindexing'),$('reindex-catalog'),$('scan-selected'),$('scan'));
 $('reindex-catalog').hidden=false;$('scan').hidden=false;full.append(advanced);
 full.querySelector('h3').textContent='Import reports from the EMR export';
 const reindexHelp=full.querySelector('p');advanced.prepend(reindexHelp);
 advanced.insertBefore($('reindex-confirm').closest('label'),$('reindex-catalog'));
 full.querySelector('h3').textContent='3 · Import SQL reports';
 full.querySelector('h3').after(el('p','Choose the discovered EMR export below to build searchable SQL reports. Existing DICOM images do not need to be indexed again.'));
 const sqlConfirm=el('label',undefined,'check'),sqlCheck=el('input');sqlCheck.type='checkbox';sqlCheck.id='sql-import-confirm';sqlConfirm.append(sqlCheck,document.createTextNode(' I understand replacing the SQL report index requires SQL reports to be associated and reviewed again.'));full.querySelector('.toolbar').before(sqlConfirm);
 const overview=$('overview'),matching=$('suggest-all').closest('.panel'),foldersPanel=folder.closest('.panel');
 source.querySelector('h2').textContent='1 · Connect sources';
 overview.before(source);
 matching.querySelector('h3').textContent='4 · Find matching reports';
 overview.append(foldersPanel,full,matching);
 foldersPanel.querySelector('p.muted').textContent='Connected sources are read-only. Choose the report source and folder below.';
 const inspect=button('Inspect export schema',async()=>{const file_id=Number($('reindex-sql-file').value);if(file_id){await queue('inspect_sql',{file_id});location.hash='#database';notice('Schema inspection queued. The table summary updates here after the job completes.');}else notice('Add the export folder first so the SQL file appears in the selector.',true);});full.append(inspect);
 // Keep everyday import actions prominent and maintenance tools in disclosures.
 intro.remove();
 folder.querySelector('summary').textContent='Choose folders';
 foldersPanel.prepend(el('h3','2 · Add TXT / SQL files'),el('p','Choose a report source, open its folder, then add it to your catalog. Your indexed DICOM stays in place.','step-description'));
 foldersPanel.querySelector('p.muted').remove();
 const paths=el('details',undefined,'connection-details');paths.append(el('summary','Connected paths'),$('sources'));foldersPanel.append(paths);
 $('match').hidden=true;
 const connections=el('details',undefined,'connection-details');connections.append(el('summary','Connection status and storage'),$('health'));source.append(connections);
 const maintenance=el('details',undefined,'connection-details');maintenance.append(el('summary','Troubleshooting and full indexing'),inspect,advanced);overview.append(maintenance);
 matching.querySelector('p:last-child').remove();
 full.id='sql-import-step';foldersPanel.id='report-folder-step';matching.id='matching-step';
 const introHead=el('div',undefined,'operations-intro');introHead.append(el('small','OPERATIONS'),el('h2','Bring your reports into the workspace'),el('p','Connect once. Add report files. Match them to the studies you already indexed.'));
 source.prepend(introHead);
 for(const b of head.querySelectorAll('button'))b.classList.add('secondary');
}
