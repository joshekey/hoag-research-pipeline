/* Report identity is independent of study selection; requests never imply a match. */
let browserSerial=0,browserSelection=null,browserOffset=0,associationBusy=false;
function clearBrowserPreview(){
 ++browserSerial;browserSelection=null;
 $('browser-text').value='';$('browser-identity').textContent='No report preview selected.';
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
   box.checked=true;browserSelection=result;$('browser-text').value=result.text;
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
  await openStudy(study,'#review');void loadResearchQueue();notice('Report associated. Prepare and review de-identification before approval and export.');
  }finally{associationBusy=false;$('browser-associate').disabled=!uid||!selectedStudy||selectedStudy.uid!==uid||!browserSelection||!$('browser-confirm').checked||active();}
 });
 $('reindex-catalog').onclick=safely(async()=>{if(!$('reindex-confirm').checked)throw Error('Confirm reindexing first');await queue('reindex');});
 $('reindex-sql').onclick=safely(async()=>{if(!$('reindex-confirm').checked)throw Error('Confirm reindexing first');const id=Number($('reindex-sql-file').value);if(!id)throw Error('Select an indexed SQL dump');await queue('reindex_sql',{file_id:id});});
}
