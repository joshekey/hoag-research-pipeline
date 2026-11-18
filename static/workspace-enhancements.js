/* Review aids never substitute for the explicit privacy approval. */
function updateWorkspaceChecklist(){
 const box=$('review-checklist');if(!box)return;
 const s=selectedStudy,approved=['approved','exported'].includes(s?.state);
 box.replaceChildren();
 const job=latest?.jobs.find(j=>j.kind==='export'&&j.study_uid===uid);
 $('workspace-export-status').textContent=job?'Export #'+job.id+' · '+job.state+' · '+job.message:!s?'Select an exam':reviewDirty?'Export blocked: save and approve your changes':s.state==='approved'?(active()?'Export waits for the current job':'Ready to export'):s.state==='exported'?'Export completed. Packages are listed below.':'Export blocked: complete privacy review and approve this exam';
 for(const [label,done] of [['Report linked',Boolean(s?.report_id)],['Privacy draft saved',Boolean(s?.sanitized)&&!reviewDirty],['Images reviewed',approved||$('images-reviewed').checked],['Report reviewed',approved||$('report-reviewed').checked],['Masks saved',Boolean(s?.sanitized)&&!reviewDirty]])box.append(el('span',(done?'✓ ':'○ ')+label,done?'good':'muted'));
 let masks=[];try{masks=maskEntries();}catch(_error){}
 const displayedIds=new Set([...inspectedFrames].map(f=>f.split(':')[0]));
 let n=0;for(const [key,series]of dicomSeries){
  const count=masks.filter(m=>m.scope==='exam'||m.series_uid===key).length;
  const displayed=series.images.filter(i=>displayedIds.has(String(i.id))).length;
  const option=$('dicom-series').options[n++];if(option)option.textContent=n+'. '+series.description+' · '+series.images.length+' images · '+displayed+' displayed · '+count+' masks';
 }
 if($('viewer-tool-state'))$('viewer-tool-state').textContent=$('draw-mask').checked?'Rectangle tool active · Drag on the image · Scope: '+($('mask-scope').value==='series'?'Entire series':'This image/frame'):'Navigation mode · ← / → images · M rectangle tool · Esc cancel drawing';
}
function setupWorkspaceEnhancements(){
 const exportStatus=el('p');exportStatus.id='workspace-export-status';exportStatus.setAttribute('role','status');$('export-selected-study').after(exportStatus);
 const checklist=el('div',undefined,'review-checklist');checklist.id='review-checklist';checklist.setAttribute('aria-label','Review checklist');$('single-workspace').before(checklist);
 const split=el('input');split.id='workspace-split';split.type='range';split.min='35';split.max='70';split.value='55';
 const label=el('label','Image pane width');label.className='workspace-split-control';label.append(split);checklist.before(label);
 try{const saved=Number(localStorage.getItem('hoag-pane-width'));if(saved>=35&&saved<=70)split.value=saved;}catch(_error){}
 function resize(){cancelMaskDrag();$('single-workspace').style.gridTemplateColumns=split.value+'fr '+(100-Number(split.value))+'fr';applyZoom();try{localStorage.setItem('hoag-pane-width',split.value);}catch(_error){}}
 split.oninput=resize;resize();
 const tool=el('p');tool.id='viewer-tool-state';tool.setAttribute('role','status');$('image-viewport').before(tool);
 for(const id of ['draw-mask','mask-scope','images-reviewed','report-reviewed'])$(id).addEventListener('change',updateWorkspaceChecklist);
 document.addEventListener('keydown',event=>{
  if(event.ctrlKey||event.metaKey||event.altKey||event.target.closest('input,textarea,select,button,[contenteditable="true"]')||!selectedStudy||$('review').hidden||$('study-workspace').hidden)return;
  if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();navigateStack(event.key==='ArrowLeft'?-1:1);}
  else if(event.key.toLowerCase()==='m'&&!$('draw-mask').disabled){event.preventDefault();$('draw-mask').click();}
  else if(event.key==='Escape'){cancelMaskDrag();$('draw-mask').checked=false;drawMasks();updateWorkspaceChecklist();}
 });
 const filters=el('details');filters.append(el('summary','Filter exams'));
 for(const [id,labelText,type]of [['worklist-modality','Modality','select'],['worklist-state','Review status','select'],['worklist-from','From date','date'],['worklist-to','To date','date']]){
  const label=el('label',labelText),input=el(type==='select'?'select':'input');input.id=id;if(type!=='select')input.type=type;else{const o=el('option','All');o.value='';input.append(o);}label.append(input);filters.append(label);input.onchange=()=>{worklistOffset=0;void loadExamWorklist();};
 }
 for(const state of states){const o=el('option',state==='review'?'Needs privacy review':state.replaceAll('_',' '));o.value=state;filters.querySelector('#worklist-state').append(o);}
 filters.append(button('Clear filters',()=>{for(const id of ['worklist-modality','worklist-state','worklist-from','worklist-to'])$(id).value='';worklistOffset=0;void loadExamWorklist();}));$('worklist-search').after(filters);
 updateWorkspaceChecklist();
}
