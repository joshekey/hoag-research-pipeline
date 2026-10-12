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
 const listHead=el('div',undefined,'pane-heading');listHead.append(el('small','01 / STUDIES'),el('h2','Indexed DICOM'));
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
 const reportHead=el('div',undefined,'pane-heading');reportHead.append(el('small','02 / REPORTS'),el('h2','Find the right report'));
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
 imagePanel.append($('ohif-pilot-panel'));
 $('report-search').closest('.toolbar').hidden=true;
 $('legacy-txt-tools').open=true;
 $('study-rows').addEventListener('click',()=>activateReportTab('suggestions'));
 $('research-queue-rows').addEventListener('click',()=>activateReportTab('suggestions'));
 activateStudyList('catalog');activateReportTab('suggestions');
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
}
