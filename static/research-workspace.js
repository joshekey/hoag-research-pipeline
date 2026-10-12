/* Researcher-facing suggestions, queue, and explicit action feedback. */
let suggestionSerial=0,queueSerial=0,queueOffset=0,lastSuggestionsRun=null;
const suggestionLabels={strong:'Strong evidence — verify relationship',review:'Check details — incomplete evidence',differences:'Differences found — investigate'};
function clearSuggestions(){
 ++suggestionSerial;$('suggestion-list').replaceChildren();$('suggestion-status').textContent='Select a study to see report suggestions.';
}
async function loadSuggestions(){
 const study=uid,generation=reviewRequest,serial=++suggestionSerial;
 if(!study)return;
 $('suggestion-status').textContent='Loading precomputed suggestions…';$('suggestion-list').setAttribute('aria-busy','true');
 try{
  const data=await api('/api/study/'+encodeURIComponent(study)+'/suggestions');
  if(study!==uid||generation!==reviewRequest||serial!==suggestionSerial)return;
  $('suggestion-list').replaceChildren();
  if(!data.ready){$('suggestion-status').textContent='Report suggestions are not ready. Use Find reports for all studies in Sources, or wait for the indexing job to finish. Manual search remains available below.';return;}
  $('suggestion-status').textContent=data.rows.length+' ranked suggestions shown. TXT: '+data.txt_status+'. SQL coverage: '+data.sql_status+'. SQL is preferred only when matching evidence is comparable; you choose the report.';
  if(!data.rows.length)$('suggestion-list').append(el('p','No indexed demographic leads were found for this study. This is not proof that no report exists. Try manual search below or check report-source coverage.','warning'));
  for(const item of data.rows){
   const card=el('div',undefined,'suggestion-card '+item.bucket);
   card.append(el('h4',item.source.toUpperCase()+' · '+suggestionLabels[item.bucket]),el('p',item.path));
   const names=['Full name: '+(item.identity.name||'unknown'),'DOB: '+(item.identity.dob||'unknown'),'Exam date: '+(item.identity.exam_date||'unknown')];
   if(item.identity.report_date)names.push('Report header date: '+item.identity.report_date+' (verify examination date)');
   if(item.identity.result_date)names.push('SQL result date: '+item.identity.result_date+' (not the examination date)');
   card.append(el('p',names.join(' | ')),el('p','Agrees: '+(item.matches.join(', ')||'No exact demographic fields; this is a surname lead.')));
   if(item.differences.length)card.append(el('p','Differences: '+item.differences.join(', '),'warning'));
   if(item.missing.length)card.append(el('p','Missing: '+item.missing.join(', '),'muted'));
   card.append(button('Compare this report',safely(async()=>{
    const request=reviewRequest,selected=uid;
    clearBrowserPreview();const serial=browserSerial;
    $('browser-identity').textContent='Loading '+item.source.toUpperCase()+' report for comparison…';
    $('browser-text').setAttribute('aria-busy','true');
    try{
     const result=await api('/api/report-browser/preview',{source:item.source,ref:item.ref});
     if(request!==reviewRequest||selected!==uid||serial!==browserSerial)return;
     if(result.source!==item.source||result.ref!==item.ref||result.hash!==item.hash)throw Error('Report changed since matching. Refresh the source index and suggestions.');
     browserSelection=result;$('browser-text').value=result.text;
     $('browser-identity').textContent='REPORT RECORD: '+item.source.toUpperCase()+' | '+names.join(' | ')+'. '+suggestionLabels[item.bucket]+'. Association target is the DICOM study in the banner above.';
     $('browser-text').scrollIntoView({behavior:'smooth'});
    }finally{if(serial===browserSerial)$('browser-text').setAttribute('aria-busy','false');}
   })));
   $('suggestion-list').append(card);
  }
 }catch(error){if(serial===suggestionSerial)$('suggestion-status').textContent='Suggestions could not load: '+error.message+'. Manual search remains available.';}
 finally{if(serial===suggestionSerial)$('suggestion-list').setAttribute('aria-busy','false');}
}
async function loadResearchQueue(){
 const serial=++queueSerial;$('research-queue-status').textContent='Refreshing review queue…';
 try{
  const data=await api('/api/research-queue?offset='+queueOffset+'&bucket='+encodeURIComponent($('research-queue-filter').value));
  if(serial!==queueSerial)return;
  $('research-queue-rows').replaceChildren();$('research-queue-counts').replaceChildren();
  if(!data.ready){$('research-queue-status').textContent='Matching has not completed. Use Find reports for all studies in Sources. You can still browse the study catalog below.';return;}
  for(const item of data.counts){const c=el('div',undefined,'card');c.append(el('strong',item.count.toLocaleString()),el('span',suggestionLabels[item.bucket]||'No suggestions — search manually'));$('research-queue-counts').append(c);}
  for(const item of data.rows){const row=el('tr');cell(row,item.name||'Unknown');cell(row,item.accession||'Unknown');cell(row,item.date||'Unknown');cell(row,suggestionLabels[item.bucket]||'No suggestions');cell(row,'').append(button('Review images and reports',()=>openStudy(item.uid,'#matching')));$('research-queue-rows').append(row);}
  $('research-queue-status').textContent=data.total+' unassociated studies in this queue. Showing '+data.rows.length+' from '+(queueOffset+1)+'. Verified associations leave this queue.';
  $('research-queue-prev').disabled=queueOffset===0;$('research-queue-next').disabled=queueOffset+100>=data.total;
 }catch(error){if(serial===queueSerial)$('research-queue-status').textContent='Queue unavailable: '+error.message+'. The study catalog remains available below.';}
}
function updateResearchProgress(){
 if(!latest)return;
 const j=latest.jobs.find(x=>['queued','running'].includes(x.state));
 const rebuilding=j&&['scan','reindex','reindex_sql','suggest_reports','match'].includes(j.kind);
 const ready=latest.settings.suggestions_ready==='1'&&!rebuilding;
 if(!ready&&lastSuggestionsRun!==null){lastSuggestionsRun=null;clearSuggestions();void loadResearchQueue();}
 $('matching-health').textContent=ready?'Suggestions ready · SQL: '+(latest.settings.suggestions_sql||'not checked')+' · TXT: '+(latest.settings.suggestions_txt||'not checked')+' · Last built: '+date(Number(latest.settings.suggestions_built)):'Suggestions not ready. Index sources or run Find reports for all studies.';
 if(ready&&lastSuggestionsRun!==latest.settings.suggestions_run){lastSuggestionsRun=latest.settings.suggestions_run;void loadResearchQueue();if(uid)void loadSuggestions();}
 $('suggest-all').disabled=active()||latest.settings.scan_complete!=='1';
 const last=latest.jobs[0],failed=last&&['failed','cancelled'].includes(last.state);
 const partial=ready&&((latest.settings.suggestions_sql||'').includes('unavailable')||(latest.settings.suggestions_txt||'').includes('reindex'));
 $('workspace-operation').textContent=j?'WORKING · Job #'+j.id+' · '+(j.phase||j.kind)+' · '+j.message+' · '+j.progress.toLocaleString()+' processed · '+elapsed(latest.now-(j.started||j.created)):failed?'ACTION NEEDED · Job #'+last.id+' '+last.state+' · '+last.message+' · Open Processing jobs for retry/details.':partial?'PARTIAL COVERAGE · '+latest.settings.suggestions_sql+' · '+latest.settings.suggestions_txt+' · Check Sources.':'READY · No job running. '+(ready?'Report suggestions are available.':'Report suggestions have not been built.');
 $('workspace-operation').classList.toggle('working',Boolean(j));
}
function setupResearchWorkspace(){
 $('suggest-all').onclick=()=>queue('suggest_reports');
 $('suggestion-refresh').onclick=safely(loadSuggestions);
 $('research-queue-refresh').onclick=safely(loadResearchQueue);
 $('research-queue-filter').onchange=safely(()=>{queueOffset=0;return loadResearchQueue();});
 $('research-queue-prev').onclick=safely(()=>{queueOffset=Math.max(0,queueOffset-100);return loadResearchQueue();});
 $('research-queue-next').onclick=safely(()=>{queueOffset+=100;return loadResearchQueue();});
}
