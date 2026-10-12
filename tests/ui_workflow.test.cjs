const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'..');
const pause=()=>new Promise(resolve=>setTimeout(resolve,0));
async function settle(){for(let i=0;i<8;i++)await pause();}

function fixture(){
 const dom=new JSDOM(fs.readFileSync(path.join(root,'templates/index.html'),'utf8'),{url:'http://synthetic.invalid/',runScripts:'outside-only'});
 const w=dom.window;w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};
 w.URL.createObjectURL=blob=>'blob:synthetic-image-'+blob.marker;w.URL.revokeObjectURL=()=>{};
 const $=id=>w.document.getElementById(id);
 const studies={
  '1.2.3':{uid:'1.2.3',subject:'SUBJECT_SYNTHETIC_A',name:'Sample^Alex',patient:'SYNTHETIC_A',accession:'EXAM_A',date:'20200102',modality:'CT',count:1,state:'candidate',report_id:null},
  '1.2.4':{uid:'1.2.4',subject:'SUBJECT_SYNTHETIC_B',name:'Example^Jordan',patient:'SYNTHETIC_B',accession:'EXAM_B',date:'20200102',modality:'CT',count:1,state:'candidate',report_id:null}
 };
 const pending=new Map();
 function response(data,ok=true){return{ok,json:async()=>structuredClone(data)};}
 function detail(uid){return{study:studies[uid],original_report:'',candidates:studies[uid].report_id?[{id:studies[uid].report_id,path:'Associated synthetic report',reason:'manual'}]:[],images:[{id:uid==='1.2.3'?11:22,metadata:JSON.stringify({SeriesInstanceUID:'SERIES_'+uid,Modality:'CT'})}]};}
 w.fetch=async(url,options)=>{
  const route=new URL(String(url),'http://synthetic.invalid/');
  if(pending.has(route.pathname)){const wait=pending.get(route.pathname);pending.delete(route.pathname);await wait;}
  if(route.pathname==='/api/status')return response({version:'1.2.0',counts:[{kind:'dicom',status:'ok',count:2},{kind:'report',status:'ok',count:2}],states:[{state:'candidate',count:2}],jobs:[],settings:{scan_complete:'1'},modalities:['CT'],sources:['/synthetic/images','/synthetic/reports'],output:'/synthetic/output',sql:[],exports:[],now:1});
  if(route.pathname==='/api/health')return response({shares:[]});
  if(route.pathname==='/api/studies')return response({rows:Object.values(studies),total:2});
  if(route.pathname==='/api/report-browser/search'){const body=JSON.parse(options.body);const id=body.q==='Example'?'1.2.4':'1.2.3';return response({groups:[{source:'txt',total:1,rows:[{source:'txt',ref:id==='1.2.3'?'31':'42',path:'Synthetic report '+id,name:studies[id].name}]}],errors:[]});}
  if(route.pathname==='/api/report-browser/preview'){const body=JSON.parse(options.body);const id=body.ref==='31'?'1.2.3':'1.2.4';return response({source:body.source,ref:body.ref,hash:'synthetic-hash',text:'SYNTHETIC REPORT '+id,identity:{name:studies[id].name}});}
  const match=route.pathname.match(/^\/api\/study\/([0-9.]+)(.*)$/);
  if(match){const uid=match[1],action=match[2];
   if(!action)return response(detail(uid));
   if(action==='/ohif-status')return response({enabled:false});
   if(action==='/sql-candidates')return response({error:'SQL matching is temporarily disabled for patient-identity verification'},false);
   if(action==='/txt-candidates')return response({rows:[{id:uid==='1.2.3'?31:42,path:'/synthetic/'+uid+'.txt',name:studies[uid].name,reason:'Last name suggestion'}],total:1,surname:studies[uid].name.split('^')[0]});
   if(action.startsWith('/txt-report/'))return response({uid,id:Number(action.split('/').pop()),text:'SYNTHETIC REPORT '+uid,conflicts:[],preview_allowed:true,name:studies[uid].name,exam_date:'20200102',message:'Unverified synthetic candidate'});
   if(action==='/report-association'){const body=JSON.parse(options.body);studies[uid].report_id=Number(body.ref);studies[uid].state='needs_review';return response({associated:true,approved:false});}
  }
  if(route.pathname.startsWith('/api/report/'))return response({text:'SYNTHETIC ORIGINAL ASSOCIATED REPORT'});
  if(route.pathname.startsWith('/api/image/'))return{ok:true,blob:async()=>({marker:route.pathname.split('/').pop()}),headers:{get:name=>({'X-Frames':'1','X-Columns':'8','X-Rows':'8'}[name])}};
  throw Error('Unexpected synthetic API '+route.pathname);
 };
 w.eval(fs.readFileSync(path.join(root,'static/report-browser.js'),'utf8')+'\n'+fs.readFileSync(path.join(root,'static/app.js'),'utf8'));
 return{dom,w,$,open(uid){const accession=studies[uid].accession;const row=[...$('study-rows').children].find(row=>row.textContent.includes(accession));return row.querySelector('button').onclick();},hold(route){let release;pending.set(route,new Promise(resolve=>{release=resolve;}));return release;}};
}

test('tabs carry the selected study through reports, association, review and export',async()=>{
 const f=fixture();try{
  await settle();await f.open('1.2.3');await settle();
  assert.equal(f.w.location.hash,'#studies');assert.match(f.$('current-study-banner').textContent,/EXAM_A/);
  assert.equal(f.$('browser-query').value,'Sample');
  f.$('next-reports').click();await settle();
  assert.equal(f.w.location.hash,'#matching');assert.equal(f.$('matching').hidden,false);
  f.$('browser-rows').querySelector('button').click();await settle();
  assert.equal(f.$('browser-text').value,'SYNTHETIC REPORT 1.2.3');
  f.$('browser-confirm').click();f.$('browser-note').value='Verified synthetic identity and examination';f.$('browser-associate').click();await settle();
  assert.equal(f.w.location.hash,'#review');assert.equal(f.$('report-choice').value,'31');
  assert.equal(f.$('export-selected-study').disabled,true);
  f.$('next-export').click();await settle();assert.equal(f.w.location.hash,'#packages');
  assert.match(f.$('export-selected').textContent,/EXAM_A/);assert.match(f.$('export-destination').textContent,/synthetic\/output/);
 }finally{f.dom.window.close();}
});

test('late report responses never appear under a different selected study',async()=>{
 const f=fixture();try{
  await settle();await f.open('1.2.3');await settle();
  const release=f.hold('/api/report-browser/preview');
  f.$('browser-rows').querySelector('button').click();await pause();
  const next=f.open('1.2.4');
  assert.equal(f.$('browser-text').value,'');assert.equal(f.$('image').hasAttribute('src'),false);
  await next;await settle();release();await settle();
  assert.match(f.$('current-study-banner').textContent,/EXAM_B/);
  assert.equal(f.$('browser-text').value,'');assert.equal(f.$('browser-query').value,'Example');
  assert.equal(f.$('image').getAttribute('src'),'blob:synthetic-image-22');
 }finally{f.dom.window.close();}
});

test('late study details cannot overwrite a newer selection and keyboard tabs work',async()=>{
 const f=fixture();try{
  await settle();const release=f.hold('/api/study/1.2.3');
  const first=f.open('1.2.3');await pause();await f.open('1.2.4');release();await first;await settle();
  assert.match(f.$('current-study-banner').textContent,/EXAM_B/);
  const images=f.w.document.querySelector('#primary-navigation a[href="#studies"]');
  images.dispatchEvent(new f.w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));await settle();
  assert.equal(f.w.location.hash,'#matching');
  assert.equal(f.w.document.querySelector('#primary-navigation a[href="#matching"]').getAttribute('aria-selected'),'true');
 }finally{f.dom.window.close();}
});

test('unchecking a loading report cancels the pending selection',async()=>{
 const f=fixture();try{
  await settle();await f.open('1.2.3');await settle();
  const release=f.hold('/api/report-browser/preview');
  const checkbox=f.$('browser-rows').querySelector('input');
  checkbox.click();await pause();checkbox.click();release();await settle();
  assert.equal(checkbox.checked,false);assert.equal(f.$('browser-text').value,'');
  assert.equal(f.$('browser-associate').disabled,true);
 }finally{f.dom.window.close();}
});

test('Reports is accessible without selecting a DICOM study',async()=>{
 const f=fixture();try{await settle();f.w.location.hash='#matching';await settle();assert.equal(f.$('matching').hidden,false);f.$('browser-search').click();await settle();f.$('browser-rows').querySelector('button').click();await settle();assert.match(f.$('browser-text').value,/SYNTHETIC REPORT/);assert.equal(f.$('browser-associate').disabled,true);}finally{f.dom.window.close();}
});
