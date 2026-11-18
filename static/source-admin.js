/* Source administration: inspect, test in isolation, then explicitly reconnect. */
let sourceAdminSerial=0,mountBusy=false,mountTested='',mountRecords=[],sourceHealth=[];
function mountBody(){return {slot:$('mount-slot').value,unc:$('mount-unc').value,username:$('mount-user').value,domain:$('mount-domain').value,password:$('mount-password').value,confirm:$('mount-confirm').checked};}
function mountSignature(){const b=mountBody();delete b.confirm;return JSON.stringify(b);}
function updateMountActions(){
 if(!$('mount-test'))return;
 $('mount-test').disabled=mountBusy||active();
 $('mount-submit').disabled=mountBusy||active()||!mountTested||mountTested!==mountSignature()||!$('mount-confirm').checked;
 for(const field of $('mount-form').querySelectorAll('input,select'))field.disabled=mountBusy;
 updateReviewControls();
}
function editMount(record){
 if(mountBusy)return;
 mountTested='';$('mount-password').value='';$('mount-confirm').checked=false;
 for(const [id,key] of [['mount-slot','slot'],['mount-unc','unc'],['mount-user','username'],['mount-domain','domain']])$(id).value=record[key]||'';
 $('mount-test-status').textContent='Editing '+record.slot+'. Enter the password and test before saving. Existing credentials are never displayed.';
 $('mount-test-folders').replaceChildren();
 $('mount-form').closest('details').open=true;location.hash='#source-settings';$('mount-unc').focus();updateMountActions();
}
async function loadSourceAdmin(){
 const serial=++sourceAdminSerial;
 const results=await Promise.allSettled([api('/api/source-coverage'),api('/api/mounts')]);
 if(serial!==sourceAdminSerial)return;
 const box=$('source-admin');box.replaceChildren();
 const coverage=results[0].status==='fulfilled'?results[0].value:null;
 if(results[1].status==='fulfilled')mountRecords=results[1].value.mounts;
 else {mountRecords=[];box.append(el('p','Mount details unavailable: '+results[1].reason.message,'warning'));}
 $('health').hidden=Boolean(coverage);
 if(!coverage){box.append(el('p','Scan coverage unavailable. Refresh connections.','warning'));return;}
 box.append(el('p','Last successful catalog scan: '+date(Number(coverage.last_scan))+' · '+(coverage.scan_complete?'Ready':'Rescan required. Counts below may describe the previous source.')));
 for(const source of coverage.sources){
  const card=el('div',undefined,'panel source-card'),record=mountRecords.find(x=>x.path===source.root);
  card.append(el('h3',record?.slot==='imaging'?'DICOM imaging':record?.slot==='bulk'?'TXT / bulk':record?.slot==='database'?'SQL database':'Source '+(source.index+1)),el('p',record?.unc||'Remote share not available / locally managed source'),el('p',source.root,'muted'));
  appendSourceHealth(card,source.root);
  card.append(el('p',(source.patients||0)+' patient IDs · '+source.studies+' studies · '+(source.images||0)+' DICOM files · '+source.files+' files indexed'));
  card.append(el('p','Unreadable files: '+(source.errors||0)+' · Ignored files: '+(source.ignored||0)+'. Counts cover indexed folders, not the entire remote share.'));
  card.append(el('p','Last successful folder scope: '+(!coverage.last_scan?'Not scanned':source.folders===null?'Entire source':source.folders.length?source.folders.join(', '):'Not included in the last scan')));
  if(record?.editable)card.append(button('Edit connection',()=>editMount(record)));
  else card.append(el('p','Connection managed outside this app. Contact the server administrator.','muted'));
  card.append(button('Browse / change scan folders',()=>{$('folder-root').value=String(source.index);$('folder-open').closest('details').open=true;location.hash='#overview';$('folder-open').click();}));
  if(source.errors)card.append(button('Show indexing errors',()=>{$('source-errors').closest('details').open=true;$('errors-refresh').click();location.hash='#administration';$('source-errors').scrollIntoView();}));
  box.append(card);
 }
 const output=mountRecords.find(x=>x.slot==='output')||sourceHealth.find(x=>x.output);
 if(output){const c=el('div',undefined,'panel');c.append(el('h3','Research output'),el('p',output.unc||'Not connected'));appendSourceHealth(c,output.path);if(output.editable)c.append(button('Edit output connection',()=>editMount(output)));box.append(c);}
 updateMountActions();
}
function setupSourceAdmin(){
 const box=el('div');box.id='source-admin';$('health').after(box);
 const status=el('p');status.id='mount-test-status';status.setAttribute('role','status');
 const folders=el('ul');folders.id='mount-test-folders';
 const test=button('Test connection & preview folders',safely(async()=>{
  if(mountBusy||active())return;
  const body=mountBody(),signature=mountSignature();body.action='test';
  mountBusy=true;mountTested='';updateMountActions();status.textContent='Testing a separate read-only connection. Your current share stays connected…';folders.replaceChildren();
  try{const result=await api('/api/mount',body);if(signature!==mountSignature())return;mountTested=signature;status.textContent=result.message+' Top-level folders (up to 50; not a patient count):';for(const name of result.folders)folders.append(el('li',name));if(!result.folders.length)folders.append(el('li','No top-level folders found. Files may be at the share root.'));}
  catch(error){status.textContent=error.message;}
  finally{body.password='';mountBusy=false;updateMountActions();}
 }));test.id='mount-test';$('mount-submit').before(test,status,folders);$('mount-submit').textContent='Save & reconnect share';
 $('mount-form').addEventListener('input',event=>{if(event.target.id!=='mount-confirm'){mountTested='';status.textContent='Settings changed. Test this connection before saving.';folders.replaceChildren();}updateMountActions();});
 $('mount-slot').addEventListener('change',()=>{const record=mountRecords.find(x=>x.slot===$('mount-slot').value);if(record)editMount(record);});
 $('choose-output').onclick=()=>editMount(mountRecords.find(x=>x.slot==='output')||{slot:'output'});
 $('mount-form').onsubmit=async event=>{
  event.preventDefault();if(mountBusy||active()||!mountTested||mountTested!==mountSignature()||!$('mount-confirm').checked)return;
  if(bodySlotNeedsReviewWarning()&&!window.confirm('Changing this source invalidates scan readiness and approvals. Discard unsaved review edits and continue?'))return;
  const body=mountBody();mountBusy=true;mountTested='';updateMountActions();status.textContent='Saving connection. Source changes invalidate scan readiness and approvals. Keep this page open…';
  try{const result=await api('/api/mount',body);status.textContent=result.message;notice(result.message);await refresh();await health();}
  catch(error){status.textContent=error.message;notice(error.message,true);}
  finally{if(body.slot!=='output'){reviewDirty=false;uid=null;clearActiveStudy();updateSelectedStudyBanner(null);showWorkspaceScreen();try{sessionStorage.removeItem('hoag-selected-exam');}catch(_error){}}body.password='';$('mount-password').value='';$('mount-confirm').checked=false;mountBusy=false;updateMountActions();}
 };
 updateMountActions();
}

function bodySlotNeedsReviewWarning(){return $('mount-slot').value!=='output'&&reviewDirty;}

function appendSourceHealth(card,path){const h=sourceHealth.find(x=>x.path===path);if(!h){card.append(el('p','Connection health unavailable','warning'));return;}card.append(el('p',h.message,h.ok?'good':'warning'));if(h.ok)card.append(el('p',bytes(h.free)+' free / '+bytes(h.total)));}
