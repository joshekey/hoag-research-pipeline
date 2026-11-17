/* Report identity is independent of study selection; requests never imply a match. */
let browserSerial=0,browserSelection=null,browserOffset=0,associationBusy=false;
let browserItems=[],browserIndex=-1;
function updateReportNavigation(){
 $('browser-report-prev').disabled=browserIndex<=0;
 $('browser-report-next').disabled=browserIndex<0||browserIndex>=browserItems.length-1;
 $('browser-position').textContent=browserIndex<0?'Select a report':(browserIndex+1)+' of '+browserItems.length+' loaded reports';
}
function renderBrowserPreview(result,item){
 browserSelection=result;$('browser-text').value=result.text;$('browser-text').scrollTop=0;
 $('browser-preview-title').textContent=(item.path||result.path||'Selected report').split(/[\\/]/).pop();
 $('browser-preview-title').title=item.path||result.path||'';
 $('browser-identity').textContent='REPORT RECORD: '+result.source.toUpperCase()+' | Name: '+(result.identity?.name||item.name||'unknown')+' | DOB: '+(result.identity?.dob||'unknown')+' | Source date (verify exam): '+(result.date||result.identity?.exam_date||result.identity?.report_date||'unknown')+'. Verify against the selected exam above.';
 // Mobile stacks the panes; only then bring the reader into view.
 if(window.matchMedia?.('(max-width: 760px)').matches)$('browser-reader').scrollIntoView({block:'start'});
}
async function previewBrowserItem(index){
 const item=browserItems[index];if(!item)return;
 clearBrowserPreview();browserIndex=index;updateReportNavigation();
 const serial=browserSerial,row=$('browser-rows').children[index];
 row.querySelector('input').checked=true;row.setAttribute('aria-selected','true');
 $('browser-preview-title').textContent='Loading '+item.path.split(/[\\/]/).pop()+'…';
 $('browser-text').setAttribute('aria-busy','true');
 try{
  const result=await api('/api/report-browser/preview',{source:item.source,ref:item.ref});
  if(serial!==browserSerial)return;
  if(result.source!==item.source||String(result.ref)!==String(item.ref))throw Error('Preview does not match the selected report. Search again.');
  renderBrowserPreview(result,item);
 }catch(error){if(serial===browserSerial){$('browser-preview-title').textContent='Report could not be opened';$('browser-identity').textContent=error.message;}}
 finally{if(serial===browserSerial)$('browser-text').setAttribute('aria-busy','false');}
}
function clearBrowserPreview(){
 ++browserSerial;browserSelection=null;
 browserIndex=-1;updateReportNavigation();$('browser-preview-title').textContent='Report preview';
 $('browser-text').setAttribute('aria-busy','false');
 $('browser-text').value='';$('browser-identity').textContent='No report preview selected.';
 $('browser-confirm').checked=false;$('browser-note').value='';$('browser-associate').disabled=true;
 for(const box of $('browser-rows').querySelectorAll('input'))box.checked=false;
 for(const row of $('browser-rows').children)row.removeAttribute('aria-selected');
}
async function browseReports(){
 clearBrowserPreview();const serial=browserSerial;
 $('browser-status').textContent='Searching report sources…';$('report-browser').setAttribute('aria-busy','true');$('browser-search').disabled=true;
 try{
 const data=await api('/api/report-browser/search',{source:$('browser-source').value,q:$('browser-query').value,sort:$('browser-sort').value,offset:browserOffset});
 if(serial!==browserSerial)return;
 $('browser-rows').replaceChildren();
 browserItems=data.groups.flatMap(group=>group.rows);
 for(const group of data.groups)for(const item of group.rows){
  const row=el('tr'),box=el('input');box.type='checkbox';box.setAttribute('aria-label','Select '+item.path);
  cell(row,'').append(box);cell(row,item.source.toUpperCase());const filename=cell(row,item.path.split(/[\\/]/).pop());filename.title=item.path;cell(row,item.name||'Unknown');cell(row,item.date||'Unknown');
  const preview=async()=>{
   await previewBrowserItem(browserItems.indexOf(item));
  };
  box.onchange=safely(async()=>{if(box.checked)await preview();else clearBrowserPreview();});
  cell(row,'').append(button('Preview',safely(preview)));$('browser-rows').append(row);
 }
 $('browser-status').textContent=data.groups.map(g=>g.source.toUpperCase()+': '+g.total+' reports; showing '+g.rows.length+' from '+(browserOffset+1)).concat(data.errors).join(' | ');
 $('browser-prev').disabled=browserOffset===0;$('browser-next').disabled=!data.groups.some(g=>browserOffset+100<g.total);
 }catch(error){if(serial===browserSerial)$('browser-status').textContent='Search failed: '+error.message+'. Check source/index status and retry.';}finally{$('browser-search').disabled=false;$('report-browser').setAttribute('aria-busy','false');}
}
function setupReportBrowser(){
 const panel=$('report-browser'),split=el('div'),list=el('div'),reader=el('div');
 split.id='browser-split';list.id='browser-results';reader.id='browser-reader';
 const title=el('h3','Report preview');title.id='browser-preview-title';
 const nav=el('div',undefined,'toolbar');
 const previous=button('← Previous report',safely(()=>previewBrowserItem(browserIndex-1)));previous.id='browser-report-prev';
 const next=button('Next report →',safely(()=>previewBrowserItem(browserIndex+1)));next.id='browser-report-next';
 const position=el('span');position.id='browser-position';position.setAttribute('role','status');nav.append(previous,next,position);reader.append(title,nav);
 const table=$('browser-rows').closest('.table-wrap');table.id='browser-result-scroll';table.setAttribute('aria-label','Report search results');table.tabIndex=0;
 list.append(table);const pages=el('div',undefined,'toolbar');pages.append($('browser-prev'),$('browser-next'));list.append(pages);
 let current=$('browser-identity');while(current){const following=current.nextSibling;reader.append(current);current=following;}
 split.append(list,reader);panel.append(split);
 const suggestions=$('suggestion-list').closest('.panel'),details=el('details');details.id='browser-suggestions';details.append(el('summary','Ranked report suggestions for this exam'),suggestions);list.prepend(details);
 updateReportNavigation();
 $('browser-search').onclick=safely(()=>{browserOffset=0;return browseReports();});
 $('browser-query').onkeydown=event=>{if(event.key==='Enter')$('browser-search').click();};
 $('browser-source').onchange=$('browser-search').onclick;$('browser-sort').onchange=$('browser-search').onclick;
 $('browser-prev').onclick=safely(()=>{browserOffset=Math.max(0,browserOffset-100);return browseReports();});
 $('browser-next').onclick=safely(()=>{browserOffset+=100;return browseReports();});
 $('browser-confirm').onchange=()=>{$('browser-associate').disabled=!uid||!selectedStudy||selectedStudy.uid!==uid||!browserSelection||!$('browser-confirm').checked||active()||associationBusy;};
 $('browser-associate').onclick=safely(async()=>{
  const study=uid,serial=browserSerial,generation=reviewRequest,selection=browserSelection;
  if(!study||!selectedStudy||selectedStudy.uid!==study||!selection)throw Error('Select a DICOM study and preview a report first');
  if(reviewDirty&&!window.confirm('Changing the associated report replaces unsaved review changes. Continue?'))return;
  if(associationBusy)return;associationBusy=true;$('browser-associate').disabled=true;notice('Associating the selected report. Please wait…');
  try{await api('/api/study/'+encodeURIComponent(study)+'/report-association',{source:selection.source,ref:selection.ref,hash:selection.hash,review_revision:selectedStudy.review_revision,confirm:$('browser-confirm').checked,note:$('browser-note').value});
  if(serial!==browserSerial||study!==uid||generation!==reviewRequest)return;
  reviewDirty=false;
  await openStudy(study,'#review');void loadResearchQueue();notice('Report associated. Prepare and review de-identification before approval and export.');
  }finally{associationBusy=false;$('browser-associate').disabled=!uid||!selectedStudy||selectedStudy.uid!==uid||!browserSelection||!$('browser-confirm').checked||active();}
 });
 $('reindex-catalog').onclick=safely(async()=>{if(!$('reindex-confirm').checked)throw Error('Confirm reindexing first');await queue('reindex');});
 $('reindex-sql').onclick=safely(async()=>{if(!$('reindex-confirm').checked)throw Error('Confirm reindexing first');const id=Number($('reindex-sql-file').value);if(!id)throw Error('Select an indexed SQL dump');await queue('reindex_sql',{file_id:id});});
}
