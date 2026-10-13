/* Report identity is independent of study selection; requests never imply a match. */
let browserSerial=0,browserSelection=null,browserOffset=0,associationBusy=false;
function renderDiagnosticReport(result){
 renderGuidedComparison();
 const view=$('diagnostic-report');if(!view)return;view.replaceChildren();
 if(!result){view.append(el('p','Select a report to read its diagnostic narrative.','muted'));return;}
 view.append(el('h3','Diagnostic radiology report'));
 if(result.format_status==='unparsed')view.append(el('p','This source could not be formatted. Review the original source below.','warning'));
 const text=result.formatted??result.text;
 for(const block of text.split(/\n\s*\n/)){
  const lines=block.split('\n'),heading=lines[0].replace(/:$/,'');
  if(/^(CLINICAL HISTORY|HISTORY|INDICATION|COMPARISON|TECHNIQUE|FINDINGS|IMPRESSION|CONCLUSION|RECOMMENDATION)$/.test(heading)){view.append(el('h4',heading),el('p',lines.slice(1).join('\n')));}
  else view.append(el('p',block));
 }
}
function clearBrowserPreview(){
 ++browserSerial;browserSelection=null;
 $('browser-text').value='';$('browser-identity').textContent='No report preview selected.';
 renderDiagnosticReport(null);
 $('browser-confirm').checked=false;$('browser-note').value='';$('browser-associate').disabled=true;
 for(const box of $('browser-rows').querySelectorAll('input'))box.checked=false;
}
async function browseReports(){
 clearBrowserPreview();const serial=browserSerial;
 $('browser-status').textContent='Searching report sources…';$('report-browser').setAttribute('aria-busy','true');$('browser-search').disabled=true;
 try{
 const data=await api('/api/report-browser/search',{source:$('browser-source').value,q:$('browser-query').value,sort:$('browser-sort').value,offset:browserOffset});
 if(serial!==browserSerial)return;
 $('browser-rows').replaceChildren();
 for(const group of data.groups)for(const item of group.rows){
  const row=el('tr'),box=el('input');box.type='checkbox';box.setAttribute('aria-label','Select '+item.path);
  cell(row,'').append(box);cell(row,item.source.toUpperCase());cell(row,item.path);cell(row,item.name||'Unknown');cell(row,item.date||'Unknown');
  const preview=async()=>{
   clearBrowserPreview();box.checked=true;const request=browserSerial;
   const result=await api('/api/report-browser/preview',{source:item.source,ref:item.ref});
   if(request!==browserSerial||result.source!==item.source||String(result.ref)!==String(item.ref))return;
   box.checked=true;browserSelection=result;activateReportTab('preview');$('browser-text').value=result.text;renderDiagnosticReport(result);
   $('browser-identity').textContent='REPORT RECORD: '+item.source.toUpperCase()+' | Name: '+(result.identity?.name||item.name||'unknown')+' | DOB: '+(result.identity?.dob||'unknown')+' | Source date (verify exam): '+(result.date||result.identity?.exam_date||result.identity?.report_date||'unknown')+'. Compare this source record with the selected DICOM study before association.';
  };
  box.onchange=safely(async()=>{if(box.checked)await preview();else clearBrowserPreview();});
  cell(row,'').append(button('Preview',safely(preview)));$('browser-rows').append(row);
 }
 $('browser-status').textContent=data.groups.map(g=>g.source.toUpperCase()+': '+g.total+' reports; showing '+g.rows.length+' from '+(browserOffset+1)).concat(data.errors).join(' | ');
 $('browser-prev').disabled=browserOffset===0;$('browser-next').disabled=!data.groups.some(g=>browserOffset+100<g.total);
 }catch(error){if(serial===browserSerial)$('browser-status').textContent='Search failed: '+error.message+'. Check source/index status and retry.';}finally{$('browser-search').disabled=false;$('report-browser').setAttribute('aria-busy','false');}
}
function setupReportBrowser(){
 $('browser-search').onclick=safely(()=>{browserOffset=0;return browseReports();});
 $('browser-query').onkeydown=event=>{if(event.key==='Enter')$('browser-search').click();};
 $('browser-source').onchange=$('browser-search').onclick;$('browser-sort').onchange=$('browser-search').onclick;
 $('browser-prev').onclick=safely(()=>{browserOffset=Math.max(0,browserOffset-100);return browseReports();});
 $('browser-next').onclick=safely(()=>{browserOffset+=100;return browseReports();});
 $('browser-confirm').onchange=()=>{$('browser-associate').disabled=!uid||!selectedStudy||selectedStudy.uid!==uid||!browserSelection||!$('browser-confirm').checked||active()||associationBusy;};
 $('browser-associate').onclick=safely(async()=>{
  const study=uid,serial=browserSerial,generation=reviewRequest,selection=browserSelection;
  if(!study||!selectedStudy||selectedStudy.uid!==study||!selection)throw Error('Select a DICOM study and preview a report first');
  if(associationBusy)return;associationBusy=true;$('browser-associate').disabled=true;notice('Associating the selected report. Please wait…');
  try{await api('/api/study/'+encodeURIComponent(study)+'/report-association',{source:selection.source,ref:selection.ref,hash:selection.hash,confirm:$('browser-confirm').checked,note:$('browser-note').value});
  if(serial!==browserSerial||study!==uid||generation!==reviewRequest)return;
  if($('associate-next').checked){await openNextAssociation(true);void loadResearchQueue();notice('Report associated. Privacy review and export approval remain separate.');}
  else{await openStudy(study,'#review');void loadResearchQueue();notice('Report associated. Prepare and review de-identification before approval and export.');}
  }finally{associationBusy=false;$('browser-associate').disabled=!uid||!selectedStudy||selectedStudy.uid!==uid||!browserSelection||!$('browser-confirm').checked||active();}
 });
 $('reindex-catalog').onclick=safely(async()=>{if(!$('reindex-confirm').checked)throw Error('Confirm reindexing first');await queue('reindex');});
 $('reindex-sql').onclick=safely(async()=>{if(!$('sql-import-confirm').checked)throw Error('Confirm the SQL import review requirement first');const id=Number($('reindex-sql-file').value);if(!id)throw Error('Add the export folder first, then select its SQL file');await queue('reindex_sql',{file_id:id});});
}
