// Built renderer + real Python workspace service. Requires the isolated harness.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const MARK = 'RESEARCH_DESKTOP_RESULT ';

async function renderer(config) {
  const { app, BrowserWindow } = require('electron');
  app.setPath('userData', path.join(config.proof, 'userdata')); app.disableHardwareAcceleration();
  process.env.BELLOMBERG_LAUNCH_ID = 'isolated-workspace'; process.env.BELLOMBERG_DESKTOP_API_URL = config.origin;
  let window; const errors = [], blocked = [], scenarios = [];
  const js = (fn, ...args) => window.webContents.executeJavaScript(`(${fn.toString()})(...${JSON.stringify(args)})`);
  const wait = async (fn, label, timeout = 20000, args = []) => { const end = Date.now()+timeout; while(Date.now()<end) { if(await js(fn,...args))return; await new Promise(r=>setTimeout(r,100)); } throw Error('Timeout '+label+': '+await js(()=>document.querySelector('.ti-research')?.innerText)); };
  const field = async (label,value,kind) => js((label,value,kind)=>{
    const node=[...document.querySelectorAll('.ti-research label')].find(n=>n.textContent.startsWith(label)&&(!kind||n.querySelector(kind)));
    if(!node)throw Error('Field not found: '+label);
    const input=node.querySelector('input,textarea,select');
    const prototype=input.tagName==='SELECT'?HTMLSelectElement.prototype:input.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype,'value').set.call(input,value);
    input.dispatchEvent(new Event(input.tagName==='SELECT'?'change':'input',{bubbles:true}));
  },label,value,kind);
  const click = async text => {await wait(text=>[...document.querySelectorAll('.ti-research button')].some(b=>b.textContent.trim()===text&&!b.disabled),'button '+text,20000,[text]);await js(text=>[...document.querySelectorAll('.ti-research button')].find(b=>b.textContent.trim()===text).click(),text);};
  const checkbox = async label => js(label=>{const node=[...document.querySelectorAll('.ti-research label')].find(n=>n.textContent.includes(label));if(!node)throw Error('Checkbox missing: '+label);node.querySelector('input[type=checkbox]').click();},label);
  const event = async kind => wait(kind=>[...document.querySelectorAll('.ti-research-history > details')].some(n=>n.querySelector('summary').textContent.includes(kind)),kind);
  const result = async (kind,afterId=0) => {
    const until=Date.now()+25000;
    while(Date.now()<until) {const state=await (await fetch(config.origin+'/__qa/state')).json();const rows=state.events[config.run];const found=rows.filter(e=>e.kind===kind&&e.id>afterId);if(found.length)return found.at(-1);await new Promise(r=>setTimeout(r,100));}
    throw Error('No persisted event: '+kind);
  };
  const capture = async (name,selector='.ti-research') => {
    await wait(()=>!document.querySelector('.ti-research [role=status]'),'settled before capture');
    await js(selector=>document.querySelector(selector).scrollIntoView({block:'start'}),selector);
    await js(async()=>{await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));});
    fs.writeFileSync(path.join(config.proof,name),(await window.capturePage(undefined,{stayHidden:true})).toPNG());
  };
  try {
    await app.whenReady();
    window=new BrowserWindow({show:false,width:1500,height:1080,webPreferences:{preload:path.join(root,'dist-electron/preload.mjs'),contextIsolation:true,nodeIntegration:false,sandbox:true,backgroundThrottling:false,additionalArguments:['--bellomberg-launch-id=isolated-workspace','--bellomberg-api-port='+new URL(config.origin).port]}});
    window.webContents.on('preload-error',(_e,_p,error)=>errors.push(String(error)));
    window.webContents.session.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*','ws://*/*','wss://*/*']},(details,callback)=>{const cancel=!details.url.startsWith(config.origin+'/');if(cancel)blocked.push(details.url);callback({cancel});});
    await window.loadURL(config.origin+'/#/agents/trade-idea?run='+config.run);
    await js(token=>{localStorage.setItem('bellomberg_token_v1',token);localStorage.setItem('bellomberg_unlocked_v1',JSON.stringify({ts:Date.now()}));localStorage.setItem('bellomberg_last_launch_id','isolated-workspace');},config.token);
    await new Promise(resolve=>{window.webContents.once('did-finish-load',resolve);window.webContents.reload();});
    await wait(()=>document.querySelector('.ti-research select')?.options.length>0,'research ready');
    if(config.family==='bank_residual_income'){
      await click('Utili e revisione');await field('Data prevista di pubblicazione','2027-02-01');await click('Congela le aspettative');
      const prepared=await result('earnings_prepare');
      assert.equal(prepared.data.expectations[0].driver,'common_net_income');assert.ok(prepared.data.expectations[0].cell);
      await wait(id=>[...document.querySelectorAll('.ti-research select option')].some(option=>option.value===String(id)),'bank preparation visible',20000,[prepared.id]);
      await field('Aspettative congelate',String(prepared.id));
      await wait(()=>document.querySelector('.ti-research').innerText.includes('Utile disponibile agli azionisti ordinari'),'localized bank metric');
      await field('URL della fonte pubblica','https://example.org/synthetic/results');await field('Data di pubblicazione','2027-02-01');
      await fetch(config.origin+'/__qa/clock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({now:'2027-02-02T12:00:00+00:00'})});
      await click('Acquisisci documenti pubblici');const acquired=await result('acquire_sources');
      await wait(id=>[...document.querySelectorAll('.ti-research select option')].some(option=>option.value===String(id)),'bank sources visible',20000,[acquired.id]);
      await field('Documenti acquisiti',String(acquired.id),'select');
      await field('Risultato pubblicato',String(prepared.data.expectations[0].value*.96).replace('.',','));
      await click('Confronta risultati e attese');
      await wait(()=>document.querySelector('.ti-research [role=alert]')?.textContent.includes('verified'),'unproven result blocked');
      const rejected=await (await fetch(config.origin+'/__qa/state')).json();
      assert.equal(rejected.events[config.run].filter(row=>row.kind==='earnings_review').length,0);
      await capture('bank-unproven-observation.png','.ti-research [role=alert]');
      await field('Risultato pubblicato',String(prepared.data.expectations[0].value*.97).replace('.',','));
      await click('Confronta risultati e attese');const reviewed=await result('earnings_review');
      assert.equal(reviewed.data.differences[0].driver,'common_net_income');assert.ok(reviewed.data.differences[0].delta<0);
      assert.equal(reviewed.data.model_impact,'requires_explicit_assumption_revision');
      assert.equal(reviewed.data.source_generation,config.generation);
      await click('Esamina versioni e osservazioni');const checked=await result('error_review');
      assert.equal(checked.data.findings[0].category,'unattributed_deviation');
      assert.ok(Object.values(checked.data.categories).every(row=>row.status==='insufficient_evidence'));
      await js(()=>{for(const details of document.querySelectorAll('.ti-research-history > details'))details.open=true;});
      await capture('bank-earnings-comparison.png','.ti-research-history');
      const state=await (await fetch(config.origin+'/__qa/state')).json();
      assert.equal(state.costs[config.run].requests,0);assert.equal(state.costs[config.run].workspace_paid_requests,0);
      assert.deepEqual(errors,[]);assert.ok(blocked.every(url=>url.startsWith('https://fonts.googleapis.com/')));
      fs.writeFileSync(path.join(config.proof,'state.json'),JSON.stringify(state,null,2));
      console.log(MARK+JSON.stringify({ok:true,family:config.family,events:state.events[config.run].length,
        scenarios:['F8 bank common-engine frozen income and workbook cell','F9 unproven amount rejected; exact later source persisted; forward assumptions unchanged','F10 deviation does not establish causality'],blocked}));
      app.exit(0);return;
    }
    const interrupted=await (await fetch(config.origin+'/__qa/saved-operation',{method:'POST'})).json();
    assert.equal(interrupted.pending[0].status,'ready_to_recover');
    await js(()=>document.querySelector('.ti-research .ti-slab-head button').click());
    await wait(()=>document.querySelector('.ti-research-pending')?.innerText.includes('Risultato salvato da recuperare'),'saved result visible');
    await capture('research-recovery-it.png','.ti-research-pending');
    await click('Recupera il risultato salvato');
    await wait(()=>!document.querySelector('.ti-research-pending'),'recovery published');
    await capture('research-it-tools.png');
    await field('Domanda sul dossier','Quale valore base?');await click('Consulta le prove');
    const answer=await result('question');assert.equal(answer.data.status,'answered');assert.ok(answer.data.citations.some(c=>c.cell));
    await wait(()=>!document.querySelector('.ti-research button[disabled]')?.textContent.includes('Consulta'),'question idle');
    await field('Domanda sul dossier','Zebre interstellari?');await click('Consulta le prove');
    await wait(()=>document.querySelector('.ti-research-history')?.innerText.includes('Prove insufficienti'),'missing answer');
    await field('Ipotesi da modificare','base:wacc');await field('Nuovo valore','0,12');await field('Nome della variante','Sensibilità personale');await field('Motivo della modifica','Tasso più alto scelto dal PM per il test dichiaratamente sintetico');
    await click('Calcola e salva variante');const variant=await result('simulate');assert.equal(variant.data.status,'ready');assert.ok(variant.data.model.generation_id);
    await wait(()=>document.querySelector('.ti-research-history')?.innerText.includes('XLSX'),'variant download');
    await field('Ricerca da confrontare',config.run);await field('Versione',variant.data.model.generation_id);
    // The first Version field selects the consulted model; compare it with the archived committee.
    await wait(value=>document.querySelector('.ti-research select')?.value===value,'variant selected',20000,[variant.data.model.generation_id]);
    await click('Confronta due versioni dello stesso titolo');const compare=await result('compare_runs');assert.ok(compare.data.values.some(row=>typeof row.delta==='number'&&Number.isFinite(row.delta)&&row.delta!==0));
    await field('Versione',config.generation);await wait(value=>document.querySelector('.ti-research select')?.value===value,'committee selected',20000,[config.generation]);
    await field('Ricerca da confrontare',config.otherRun);await click('Confronta le idee');assert.equal((await result('compare_ideas')).data.ideas.length,2);
    scenarios.push('F1 grounded/insufficient chat; F2 true common-engine personal variant; F3 exact versions; F7 ideas evidence and comparability');
    await click('Tesi e obiezioni');await field('Obiezione del PM','Verificare il margine nel prossimo bilancio');await click('Registra obiezione');const objection=await result('objection');
    await wait(()=>document.querySelector('.ti-research').innerText.includes('Verificare il margine'),'objection visible');
    await field('Obiezione',String(objection.id),'select');await field('Risposta','Conserviamo la questione aperta');await click('Registra risposta');await result('objection_reply');
    await field('Soglia','11,00');await checkbox('Attivo esplicitamente');await click('Salva condizione');await result('monitor');
    await wait(()=>[...document.querySelectorAll('.ti-research button')].some(b=>b.textContent==='Controlla sulle prove selezionate'),'monitor ready');
    await click('Controlla sulle prove selezionate');const observedCheck=await result('monitor_check');assert.equal(observedCheck.data.status,'triggered');assert.equal(observedCheck.data.value,10);assert.equal(observedCheck.data.observed_at,'2026-09-10');
    await click('Aggiorna il prezzo osservato');const refreshed=await result('refresh');assert.equal(refreshed.data.status,'ready');assert.ok(refreshed.data.invalidates_conclusion);
    await wait(()=>document.querySelector('.ti-research').innerText.includes('conclusioni e proposta operativa da rivalidare'),'invalidation visible');await capture('research-it-follow.png');
    await click('Acquisisci documenti pubblici');const catalog=await result('acquire_sources');
    await wait(id=>[...document.querySelectorAll('.ti-research select option')].some(option=>option.value===String(id)),'source catalog visible',20000,[catalog.id]);
    await field('Documenti acquisiti',String(catalog.id),'select');await click('Aggiorna dai documenti verificati');
    const documentRefresh=await result('refresh',refreshed.id);assert.equal(documentRefresh.data.kind,'documents');assert.equal(documentRefresh.data.status,'ready');assert.ok(documentRefresh.data.model.generation_id);
    scenarios.push('F4 dated price/common generation invalidates conclusion; F5 explicit observed manual monitor; F6 append-only objection and response');
    await click('Utili e revisione');await field('Data prevista di pubblicazione','2027-02-01');await click('Congela le aspettative');const prepared=await result('earnings_prepare');assert.ok(prepared.data.expectations.length);
    await wait(id=>[...document.querySelectorAll('.ti-research select option')].some(option=>option.value===String(id)), 'persisted earnings preparation visible',20000,[prepared.id]);
    await field('Aspettative congelate',String(prepared.id));await field('Risultato pubblicato',String(prepared.data.expectations[0].value*.97).replace('.',','));await field('URL della fonte pubblica','https://example.org/synthetic/results');await field('Data di pubblicazione','2027-02-01');
    await fetch(config.origin+'/__qa/clock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({now:'2027-02-02T12:00:00+00:00'})});
    await click('Acquisisci documenti pubblici');const earningsCatalog=await result('acquire_sources',catalog.id);
    await wait(id=>[...document.querySelectorAll('.ti-research select option')].some(option=>option.value===String(id)),'earnings catalog visible',20000,[earningsCatalog.id]);
    await field('Documenti acquisiti',String(earningsCatalog.id),'select');
    await checkbox('Rivedi anche');
    await field('Ipotesi da modificare','base:wacc');await field('Nuovo valore','0,13');
    await field('Motivo della modifica','Revisione esplicita del tasso dopo risultati sintetici inferiori alle attese');
    await click('Confronta risultati e attese');const reviewed=await result('earnings_review');assert.ok(reviewed.data.differences.length);
    assert.ok(reviewed.data.model_impact.values.some(row=>row.delta<0));
    assert.ok(reviewed.data.model.generation_id!==config.generation);
    await click('Esamina versioni e osservazioni');const errorReview=await result('error_review');
    assert.equal(errorReview.data.status,'observational_review');assert.equal(errorReview.data.findings[0].category,'unattributed_deviation');
    assert.ok(Object.values(errorReview.data.categories).every(row=>row.status==='insufficient_evidence'));
    await capture('research-it-earnings.png');scenarios.push('F8 frozen sourced expectations; F9 later observed same-period result; F10 honest historical review');
    await click('Tesi e obiezioni');await click('Controlla sulle prove selezionate');
    const staleCheck=await result('monitor_check',observedCheck.id);assert.equal(staleCheck.data.status,'stale');assert.equal(staleCheck.data.alert,null);scenarios.push('F5 later check marks the unchanged dated quote stale, without a new alert');
    await click('Costi ed esportazione');await click('Crea pacchetto privato');assert.equal((await result('export')).data.scope,'private');
    await wait(()=>[...document.querySelectorAll('.ti-research button')].some(b=>b.textContent.includes('ZIP')),'private artifact');
    await click('Anteprima condivisibile');await wait(()=>document.querySelector('.ti-research').innerText.includes('Contenuti esclusi'),'share preview');
    await capture('research-it-export-preview.png');
    await checkbox('Ho letto le esclusioni');await click('Crea estratto condivisibile');
    await wait(()=>document.querySelector('.ti-research-history')?.innerText.includes('export'),'shared result',40000);
    let receipt;const end=Date.now()+40000;while(Date.now()<end){receipt=(await (await fetch(config.origin+'/__qa/state')).json()).events[config.run].filter(row=>row.kind==='export').find(row=>row.data.scope==='shareable');if(receipt)break;await new Promise(r=>setTimeout(r,200));}assert.ok(receipt,'shared export persisted');
    await capture('research-it-costs-exports.png');scenarios.push('F11 measured phase ledger; F12 preview + private exact bytes + rebuilt public package');
    const state=await (await fetch(config.origin+'/__qa/state')).json();assert.equal(state.costs[config.run].requests,0);assert.equal(state.costs[config.run].workspace_paid_requests,0);
    await fetch(config.origin+'/preferences',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({language:'en'})});
    await new Promise(resolve=>{window.webContents.once('did-finish-load',resolve);window.webContents.reload();});
    await wait(()=>document.querySelector('.ti-research')?.innerText.includes('Work with this research'),'English');
    await capture('research-en-tools.png');
    for(const width of [1500,1180,900,720]){window.setSize(width,1080);await new Promise(r=>setTimeout(r,120));assert.equal(await js(()=>document.documentElement.scrollWidth<=innerWidth),true,'overflow '+width);await capture('research-en-'+width+'.png');}
    assert.deepEqual(errors,[]);assert.ok(blocked.every(url=>url.startsWith('https://fonts.googleapis.com/')),JSON.stringify(blocked));
    fs.writeFileSync(path.join(config.proof,'state.json'),JSON.stringify(state,null,2));
    console.log(MARK+JSON.stringify({ok:true,scenarios,events:state.events[config.run].length,blocked}));app.exit(0);
  }catch(error){fs.writeFileSync(path.join(config.proof,'failure.txt'),String(error.stack||error));if(window)fs.writeFileSync(path.join(config.proof,'failure.png'),(await window.capturePage(undefined,{stayHidden:true})).toPNG());console.error(error);app.exit(1);}
}

async function runner(){
  const args=Object.fromEntries(process.argv.slice(2).reduce((a,value,i,all)=>{if(value.startsWith('--'))a.push([value.slice(2),all[i+1]]);return a;},[]));
  if(!args.origin||!args.fixture||!args.proof)throw Error('--origin --fixture --proof required');
  assert.equal(new URL(args.origin).hostname,'127.0.0.1');
  const health=await (await fetch(args.origin+'/health')).json();assert.equal(health.version,'isolated-workspace-fixture');
  const fixture=JSON.parse(fs.readFileSync(args.fixture,'utf8'));
  const workspace=await (await fetch(args.origin+'/trade-ideas/runs/'+fixture.runs[0]+'/workspace',{headers:{'X-BB-Token':fixture.token}})).json();
  const config={origin:args.origin,proof:path.resolve(args.proof),run:fixture.runs[0],otherRun:fixture.runs[1],token:fixture.token,generation:workspace.model.generation_id,family:fixture.family};
  fs.mkdirSync(config.proof,{recursive:true});const env={...process.env,RESEARCH_DESKTOP_CONFIG:JSON.stringify(config)};delete env.ELECTRON_RUN_AS_NODE;
  const child=spawn(require('electron'),[__filename],{cwd:root,env,windowsHide:true});let output='';child.stdout.on('data',data=>{output+=data;process.stdout.write(data);});child.stderr.on('data',data=>{output+=data;process.stderr.write(data);});
  const code=await new Promise(resolve=>child.on('close',resolve));fs.writeFileSync(path.join(config.proof,'output.log'),output);assert.equal(code,0,'renderer failed');assert.ok(output.includes(MARK));
}
if(process.versions.electron)renderer(JSON.parse(process.env.RESEARCH_DESKTOP_CONFIG));else runner().catch(error=>{console.error(error);process.exitCode=1;});
