const {test,expect}=require('@playwright/test');
async function openExam(page,id='SYNTHETIC123'){
 await page.goto('/');await page.locator('.worklist-exam').filter({hasText:id}).click();
 await expect(page.locator('#image')).toHaveJSProperty('naturalWidth',64);
}
async function draw(page,start,end){
 const canvas=page.locator('#mask-canvas');await canvas.scrollIntoViewIfNeeded();
 const b=await canvas.boundingBox();
 await page.mouse.move(b.x+b.width*start/64,b.y+b.height*start/64);await page.mouse.down();
 await page.mouse.move(b.x+b.width*end/64,b.y+b.height*end/64,{steps:6});await page.mouse.up();
}
async function masks(page){return JSON.parse(await page.locator('#masks').inputValue());}

test('100 reports remain beside images with explicit selection and confirmation reset',async({page})=>{
 const rows=Array.from({length:100},(_,i)=>({source:'txt',ref:String(i),path:'/synthetic/report-'+i+'.txt',name:'Synthetic patient'}));
 await page.route('**/api/report-browser/search',route=>route.fulfill({json:{groups:[{source:'txt',total:100,rows}],errors:[]}}));
 await page.route('**/api/report-browser/preview',route=>{const body=route.request().postDataJSON();return route.fulfill({json:{...body,hash:'synthetic',text:'Report '+body.ref+'\n\nFINDINGS\nSynthetic findings.',identity:{name:'Synthetic patient'}}});});
 await openExam(page);
 await expect(page.locator('#browser-report-choice option')).toHaveCount(101);
 await page.locator('#browser-report-choice').selectOption('0');
 await expect(page.locator('#browser-text')).toHaveValue(/Report 0/);
 const images=await page.locator('#workspace-images').boundingBox(),report=await page.locator('#workspace-reports').boundingBox();
 expect(report.x).toBeGreaterThanOrEqual(images.x+images.width);
 await page.locator('#browser-confirm').check();await page.locator('#browser-note').fill('Previous record only');
 await page.locator('#browser-report-next').click();
 await expect(page.locator('#browser-text')).toHaveValue(/Report 1/);
 await expect(page.locator('#browser-confirm')).not.toBeChecked();await expect(page.locator('#browser-note')).toHaveValue('');
 await expect(page.locator('#browser-associate')).toBeDisabled();
 await page.locator('#report-results-details > summary').click();
 const list=await page.locator('#browser-result-scroll').boundingBox();expect(list.height).toBeLessThanOrEqual(262);
 await page.locator('#report-results-details > summary').click();
 await page.screenshot({path:'test-results/single-workspace-desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.screenshot({path:'test-results/single-workspace-mobile.png',fullPage:true});
});

test('patient search and report mode keep one explicit exam context',async({page})=>{
 const errors=[];page.on('pageerror',e=>errors.push(e.message));await openExam(page);
 const banner=page.locator('#current-study-banner');await expect(banner).toContainText('ACC_TEST_123');
 await page.locator('#worklist-patient').click();await page.locator('#worklist-search').fill('DEMO-002');
 await expect(page.locator('.worklist-exam')).toHaveCount(1);await expect(banner).toContainText('ACC_TEST_123');
 await page.locator('.worklist-exam').click();await expect(banner).toContainText('EXAM_B');
 await page.locator('#draft-report-tab').click();await expect(page.locator('#workspace-images')).toBeVisible();
 await expect(page.locator('#linked-report-content')).toBeVisible();await page.reload();await expect(banner).toContainText('EXAM_B');
 await page.locator('#next-review-exam').click();await expect(banner).toContainText('ACC_TEST_123');
 expect(errors).toEqual([]);
});

test('native viewer renders native and JPEG pixels with windows and recoverable errors without OHIF',async({page})=>{
 const errors=[],ohif=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/\/ohif|\/viewer\?/.test(r.url()))ohif.push(r.url());});
 await openExam(page);
 const samples=()=>page.locator('#image').evaluate(img=>{const c=document.createElement('canvas');c.width=img.naturalWidth;c.height=img.naturalHeight;const ctx=c.getContext('2d');ctx.drawImage(img,0,0);const d=ctx.getImageData(0,0,c.width,c.height).data;return [...d].filter((_,i)=>i%4===0);});
 const native=await samples();expect(Math.max(...native)-Math.min(...native)).toBeGreaterThan(30);
 const ready=page.waitForResponse(r=>r.url().includes('/api/image/')&&r.url().includes('window=lung'));
 await page.locator('#image-window').selectOption('lung');expect((await ready).status()).toBe(200);
 await expect(page.locator('#image-viewport')).toHaveAttribute('aria-busy','false');
 expect(await samples()).not.toEqual(native);
 await page.locator('#image-window').selectOption('custom');await page.locator('#window-width').fill('0');
 await page.getByRole('button',{name:'Apply window',exact:true}).click();await expect(page.locator('#image-info')).toHaveText('Preview unavailable');
 await page.locator('#image-window').selectOption('auto');await expect(page.locator('#image')).toHaveJSProperty('naturalWidth',64);
 await page.locator('#dicom-next').click();await expect(page.locator('#dicom-position-label')).toContainText('Instance 2 / 3');
 await page.locator('.worklist-exam').filter({hasText:'DEMO-002'}).click();await expect(page.locator('#image')).toHaveJSProperty('naturalWidth',64);
 await expect(page.locator('#current-study-banner')).toContainText('EXAM_B');const jpeg=await samples();expect(Math.max(...jpeg)-Math.min(...jpeg)).toBeGreaterThan(30);
 expect(ohif).toEqual([]);expect(errors).toEqual([]);
});

test('scoped masks survive draft reload and affect only selected image and series exports',async({page})=>{
 await openExam(page);
 await expect(page.locator('#browser-report-choice option')).toHaveCount(2);
 await page.locator('#browser-report-choice').selectOption('0');await expect(page.locator('#browser-text')).toHaveValue(/Clear lungs/);
 await page.locator('#browser-confirm').check();await page.locator('#browser-associate').click();
 await expect(page.locator('#prepare')).toBeVisible();await expect(page.locator('#draw-mask')).toBeDisabled();
 await page.locator('#prepare').click();await expect(page.locator('#sanitized')).not.toHaveValue('',{timeout:30000});
 await expect(page.locator('#draw-mask')).toBeEnabled();await page.locator('#draw-mask').check();await page.locator('#zoom-in').click();
 await draw(page,8,16);await expect.poll(async()=>(await masks(page)).masks?.length).toBe(1);
 let document=await masks(page);expect(document.version).toBe(2);expect(document.masks[0]).toMatchObject({scope:'image',frame:0,rect:[8,8,8,8]});
 await page.locator('#undo-mask').click();await expect.poll(async()=>(await masks(page)).masks.length).toBe(0);await draw(page,8,16);
 await page.locator('#mask-scope').selectOption('series');await draw(page,24,32);
 await expect.poll(async()=>(await masks(page)).masks.length).toBe(2);document=await masks(page);expect(document.masks[1]).toMatchObject({scope:'series',rect:[24,24,8,8]});
 await page.locator('#dicom-next').click();await expect(page.locator('#mask-summary')).toContainText('1 on this frame');
 await page.locator('#dicom-series').selectOption({label:'2. Synthetic scout (1 instances)'});await expect(page.locator('#mask-summary')).toContainText('0 on this frame');
 await page.locator('#save-mask-draft').click();await expect(page.locator('#notice')).toContainText('Draft saved');
 await page.reload();await expect.poll(async()=>(await masks(page)).masks?.length).toBe(2);await expect(page.locator('#export-selected-study')).toBeDisabled();
 await page.locator('#review-final-button').click();await expect(page.locator('#final-review-summary')).toContainText('1 image/frame, 1 series');
 await page.locator('#images-reviewed').check();await page.locator('#report-reviewed').check();await page.locator('#review-note').fill('Verified image and series scopes against synthetic sources');
 // Editing confirmations collapses the summary to require a fresh final review.
 await page.locator('#review-final-button').click();
 await page.locator('#approve').click();await expect(page.locator('#current-study-banner')).toContainText('Approved · ready to export');
 await page.locator('#review-final-button').click();await page.locator('#export-selected-study').click();
 await expect(page.locator('#exports table')).toBeVisible({timeout:30000});
 const exported=await(await page.request.get('/__synthetic_export_pixels')).json();expect(exported).toEqual({count:4,scoped_pixels_exact:true,source_unchanged:true});
 await page.screenshot({path:'test-results/scoped-mask-export.png',fullPage:true});
});
