// Isolated real Edge DOM + app.js functions. All browser network requests blocked.
const {chromium}=require('playwright');
const fs=require('fs'); const path=require('path'); const assert=require('assert/strict');
const root=path.resolve(__dirname,'..');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage(); await page.route('**/*',r=>r.abort());
 const sourceHtml=fs.readFileSync(path.join(root,'docs/index.html'),'utf8');
 // The test harness injects app.js inline; app CSP itself is tested statically below.
 const html=sourceHtml.replace(/<meta http-equiv="Content-Security-Policy"[^>]*>/i,'').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,'');
 await page.setContent(html);
 let source=fs.readFileSync(path.join(root,'docs/app.js'),'utf8');
 const start=source.indexOf('  if (document.readyState ===');
 assert(start>0);
 source=source.slice(0,start)+`window.testApp={clean,render,renderEditor,flushEdits,pushDirty,pullAll,afterSignIn,applyRemoteRows,syncNow,connectDb,
 reset:function(n,u,d){if(pushTimer)clearTimeout(pushTimer);pushTimer=null;notes=n;user=u;db=d;active=n.length?0:-1;tombstones={};busy=false;settings={};render();},
 state:function(){return {notes,user,busy,tombstones,status:ui.status.textContent};},
 stop:function(){if(pushTimer)clearTimeout(pushTimer);pushTimer=null;},
 setUser:function(u){user=u;}};})();`;
 await page.addScriptTag({content:source});
 const results=await page.evaluate(async()=>{
  const t=window.testApp, out=[];
  async function test(id,fn){try{if(!await fn())throw Error('assertion failed');out.push({id,status:'PASS'});}catch(e){out.push({id,status:'FAIL',reason:e.message});}finally{t.stop();}}
  const note=()=>({id:'A',title:'NOTE',html:'<p>original</p>',font_size:16});
  await test('WS-01 sanitizer dangerous markup',()=>{
   const clean=t.clean('<img src=x onerror="window.marker=1"><svg onload="window.marker=1"></svg><iframe src="https://example.test"></iframe><script>window.marker=1</script><p onclick="window.marker=1">safe</p><a href="javascript:void(0)">link</a>');
   const el=document.createElement('div');el.innerHTML=clean;
   return !el.querySelector('script,img,svg,iframe,a,[onclick],[onerror]')&&el.textContent==='safelink';
  });
  await test('WT-01 Korean formatting roundtrip',()=>{
   const input='<p style="text-align:center"><b>한글</b><u>밑줄</u><s>취소선</s></p><ul><li>목록</li></ul><span style="font-size:12px">작게</span>';
   const clean=t.clean(input); return clean.includes('<b>한글</b>')&&clean.includes('12px')&&clean.includes('<li>목록</li>')&&t.clean(clean)===clean;
  });
  await test('WS-02 attachment URL restriction',()=>{
   t.reset([{...note(),attachment_data:'https://example.test/track'}],{id:'user-A'},null);
   return !document.querySelector('#attach').hasAttribute('src');
  });
  await test('WT-02 failed initial pull does not create NOTE',async()=>{
   let writes=0;const db={from:()=>({select:()=>({limit:()=>Promise.resolve({data:[]}),order:()=>Promise.resolve({error:{message:'offline'}})}),upsert:()=>{writes++;throw Error('unexpected write');}})};
   t.reset([],{id:'user-A'},db);await t.afterSignIn();return t.state().notes.length===0&&writes===0&&t.state().status.includes('실패');
  });
  await test('WT-03 edit during push remains dirty',async()=>{
   let finish;const db={from:()=>({upsert:()=>({select:()=>new Promise(r=>finish=r)})})};
   t.reset([note()],{id:'user-A'},db);const pending=t.pushDirty();
   document.querySelector('#editor').innerHTML='<p>new edit</p>';t.flushEdits();
   finish({data:[{note_id:'A',updated_at:'old'}]});await pending;
   return t.state().notes[0].html.includes('new edit')&&!t.state().notes[0].synced_at;
  });
  await test('WT-04 failed upload is not success',async()=>{
   const db={from:()=>({upsert:()=>({select:()=>Promise.resolve({error:{message:'offline'}})})})};
   t.reset([note()],{id:'user-A'},db);return await t.pushDirty()===false&&!t.state().notes[0].synced_at&&t.state().status.includes('실패');
  });
  await test('WT-05 remote render preserves current edit',()=>{
   t.reset([{...note(),synced_at:'old'}],{id:'user-A'},null);
   document.querySelector('#editor').innerHTML='<p>local edit</p>';
   t.applyRemoteRows([{note_id:'A',updated_at:'remote',payload:{html:'remote edit'}}]);
   return t.state().notes[0].html.includes('local edit');
  });
  await test('WS-03 late pull after account change rejected',async()=>{
   let finish;const db={from:()=>({select:()=>({order:()=>new Promise(r=>finish=r)})})};
   t.reset([],{id:'user-A'},db);const pending=t.pullAll();t.setUser({id:'user-B'});finish({data:[{note_id:'A',payload:{html:'private A'}}]});
   let rejected=false;try{await pending;}catch{rejected=true;}return rejected&&t.state().notes.length===0;
  });
  await test('WS-04 signout clears private in-memory notes',async()=>{
   let callback;window.SIDEMEMO_CONFIG={};window.supabase={createClient:()=>({auth:{getSession:()=>new Promise(()=>{}),onAuthStateChange:f=>callback=f}})};
   t.reset([note()],{id:'user-A'},null);t.connectDb();callback('SIGNED_OUT',null);await new Promise(r=>setTimeout(r,30));
   return t.state().notes.length===0&&document.querySelector('#editor').textContent==='';
  });
  await test('WT-06 oversized/wrong-type remote payload rejected',()=>{
   t.reset([],{id:'user-A'},null);
   const base={note_id:'bad',position:0,deleted:false,updated_at:'stamp',payload:{html:'valid'}};
   const invalid=[{...base,payload:{html:{bad:true}}},{...base,payload:{font_size:-100}},
     {...base,payload:{attachment_data:'https://evil.test/pixel'}},{...base,position:10001},
     {...base,payload:{html:'x'.repeat(2*1024*1024+1)}}];
   t.applyRemoteRows(invalid);return t.state().notes.length===0;
  });
  return out;
 });
 for(const r of results)console.log(JSON.stringify(r));
 const csp=/http-equiv=["']Content-Security-Policy/i.test(sourceHtml), sri=/integrity="sha384-[^"]+"/.test(sourceHtml);
 const policyPage=await browser.newPage(); await policyPage.route('**/*',r=>r.abort()); await policyPage.setContent(sourceHtml);
 const cspResult=await policyPage.evaluate(async()=>{
   const violation=new Promise(resolve=>document.addEventListener('securitypolicyviolation',e=>resolve({directive:e.effectiveDirective,blocked:e.blockedURI}),{once:true}));
   const script=document.createElement('script');script.textContent='window.cspInlineRan=true';document.head.appendChild(script);
   const event=await Promise.race([violation,new Promise(resolve=>setTimeout(()=>resolve(null),500))]);
   return {event,ran:!!window.cspInlineRan};
 });
 const blocked=!!cspResult.event && !cspResult.ran;
 console.log(JSON.stringify({id:'WS-05 CSP enforcement and pinned CDN integrity',status:csp&&sri&&blocked?'PASS':'FAIL',reason:'header meta='+csp+', SRI='+sri+', inline policy='+JSON.stringify(cspResult)}));
 await policyPage.close();
 console.log('TOTAL '+results.length+' isolated browser cases plus CSP static/runtime case');
 await browser.close();process.exitCode=results.some(r=>r.status==='FAIL')||!csp||!sri||!blocked?1:0;
})().catch(e=>{console.error(e);process.exitCode=2;});
