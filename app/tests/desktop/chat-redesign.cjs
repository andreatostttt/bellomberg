// Offline, real Electron screenshots and layout contracts for the Chat redesign.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const root = path.resolve(__dirname, '../..');
async function run() {
  const { server } = require('./chat-modes.cjs').startFixture();
  const original = server.listeners('request')[0];
  server.removeAllListeners('request');
  server.on('request', (req,res) => {
    const route = new URL(req.url,'http://localhost').pathname;
    const send = body => { res.setHeader('Content-Type','application/json');res.end(JSON.stringify(body)); };
    if (req.method==='GET' && route==='/preferences') return send({language:'it',selected:true,source:'preferences'});
    if (req.method==='GET' && route==='/agents/list') return send({agents:[
      ['capo','Capo','Responsabile del portafoglio'], ['macro','Macro','Scenari macroeconomici'], ['options','Options Flow','Opzioni e volatilità'],
      ['quant','Quant','Analisi quantitativa'], ['fundamentals','Fundamentals','Valutazione e bilanci'], ['crypto','Crypto','Asset digitali'], ['eventdesk','Event Desk','Eventi e catalizzatori'],
    ].map(([id,name,role],i)=>({id,name,role,model:'fixture-model',color:['#1455ff','#008f63','#8b5cf6','#c52943','#d97706','#0891b2','#64748b'][i]})),engines:{chat:'fixture-model'}});
    if (req.method==='GET' && route==='/portfolio') return send({source:'synthetic fixture',positions:[
      {ticker:'ZZTEST',nome:'Zeta Test Synthetic',peso_pct:24.8,prezzo_live:202,prev_close:200},
      {ticker:'ACME',nome:'Acme Synthetic',peso_pct:32,prezzo_live:398,prev_close:400},
      {ticker:'ZZBETA.X',nome:'Beta Holdings Synthetic',peso_pct:13.3,prezzo_live:500,prev_close:498},
      {ticker:'SPY',nome:'SPDR S&P 500',peso_pct:12.6,prezzo_live:null,prev_close:null},
    ],n_positions:4});
    return original(req,res);
  });
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'bb-chat-redesign-'));
  const directory = path.resolve(process.env.BB_CHAT_CAPTURE || path.join(root, '../outputs/chat-redesign/after'));
  fs.mkdirSync(directory, { recursive: true });
  const config = { origin: `http://127.0.0.1:${server.address().port}`, temporary, directory,
    baseline: process.env.BB_CHAT_BASELINE === '1' };
  fs.mkdirSync(path.join(temporary, 'userdata'), {recursive:true});
  const entry = path.join(temporary, 'entry.cjs');
  fs.writeFileSync(entry, `require('electron').app.setPath('userData',${JSON.stringify(path.join(temporary,'userdata'))});require('electron').app.whenReady().then(()=>require(${JSON.stringify(__filename)}).renderer(${JSON.stringify(config)})).catch(e=>{console.error(e);require('electron').app.exit(1)})`);
  const env = { ...process.env }; delete env.ELECTRON_RUN_AS_NODE;
  const child = spawn(require('electron'), [entry], { env, stdio: ['ignore', 'pipe', 'pipe'] });
  let log = ''; child.stdout.on('data', b => log += b); child.stderr.on('data', b => log += b);
  const timer = setTimeout(() => child.kill(), 180000);
  try {
    const code = await new Promise(r => child.once('close', r));
    fs.writeFileSync(path.join(directory, 'electron.log'), log);
    console.log(log.slice(-5000)); assert.equal(code, 0);
  } finally { clearTimeout(timer); server.closeAllConnections(); server.close(); }
}
async function renderer(config) {
  const { app, BrowserWindow } = require('electron');
  fs.mkdirSync(path.join(config.temporary, 'userdata'), {recursive:true});
  app.setPath('userData', path.join(config.temporary, 'userdata'));
  process.env.BELLOMBERG_LAUNCH_ID = 'synthetic-chat-modes';
  process.env.BELLOMBERG_DESKTOP_API_URL = config.origin;
  const win = new BrowserWindow({ show: false, width: 1440, height: 900, useContentSize: true,
    webPreferences: { preload: path.join(root, 'dist-electron/preload.mjs'), contextIsolation: true,
      sandbox: true, backgroundThrottling: false,
      additionalArguments: ['--bellomberg-launch-id=synthetic-chat-modes', '--bellomberg-api-port=' + new URL(config.origin).port] } });
  win.webContents.session.webRequest.onBeforeRequest({ urls: ['http://*/*', 'https://*/*'] }, (d, cb) => cb({cancel: !d.url.startsWith(config.origin + '/')}));
  const js = (fn, ...args) => win.webContents.executeJavaScript(`(${fn.toString()})(...${JSON.stringify(args)})`);
  const pause = ms => new Promise(r => setTimeout(r, ms));
  const wait = async fn => { for (let i=0;i<200;i++) { if (await js(fn)) return; await pause(50); }
    throw new Error('Chat fixture timed out: ' + fn.toString().slice(0, 140) + ' | page: ' + (await js(() => document.body.innerText.slice(0, 600)))); };
  const click = s => js(s => { const el=document.querySelector(s); if (!el) throw Error('Missing '+s); el.click(); }, s);
  const fixture = body => fetch(config.origin + '/__fixture', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const reports = [];
  win.webContents.on('dom-ready', () => js(() => {const OriginalDate=Date;window.Date=class extends OriginalDate{constructor(...a){super(...(a.length?a:['2026-10-01T12:00:00Z']));}static now(){return new OriginalDate('2026-10-01T12:00:00Z').getTime();}};Object.defineProperty(performance,'now',{value:()=>1000});}).catch(()=>{}));
  try {
    await win.loadURL(config.origin + '/#/chat');
    // The unlock stamp uses the real clock (Node's): the page's Date is pinned to 2026-10-01 only
    // after dom-ready, while LoginGate checks its 12 h window with the real clock at mount.
    await js(now => {
      localStorage.setItem('bellomberg_token_v1', 'synthetic-chat-fixture-token');
      localStorage.setItem('bellomberg_unlocked_v1', JSON.stringify({ ts: now }));
      localStorage.setItem('bellomberg_last_launch_id', 'synthetic-chat-modes');
      localStorage.setItem('bellomberg.lingua', 'it');
    }, Date.now());
    for (const [mode, theme] of [['modern','dark'], ['modern','light']]) {
      await js((m,t) => {localStorage.setItem('bellomberg.interface-theme.v1',t);}, mode, theme);
      await new Promise(r => {win.webContents.once('did-finish-load',r);win.webContents.reload();});
      await wait(() => document.querySelectorAll('.f3d .dk').length === 7 && !document.querySelector('.f3d textarea')?.disabled);
      for (const [width,height] of [[1440,900],[2000,760]]) {
        win.setContentSize(width,height); await pause(250);
        await click('.f3d .dk'); await pause(1100);
        const capture = async state => { await pause(200);
          if (!config.baseline && mode==='modern' && theme==='dark') {
            const readability = await js(() => {
              const rgb = s => { const v=s.match(/[\d.]+/g)?.map(Number); if(!v)return null;return s.startsWith('color(srgb')?[v[0]*255,v[1]*255,v[2]*255,v[3]??1]:[v[0],v[1],v[2],v[3]??1]; };
              const mix=(a,b)=>a.slice(0,3).map((x,i)=>x*a[3]+b[i]*(1-a[3]));
              const background = el => { const chain=[];for(let p=el;p;p=p.parentElement)chain.unshift(p);return chain.reduce((bg,p)=>mix(rgb(getComputedStyle(p).backgroundColor)||[0,0,0,0],bg),[255,255,255]); };
              const lum = c=>c.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);
              const out=[];const walk=document.createTreeWalker(document.querySelector('.f3d'),NodeFilter.SHOW_TEXT);
              while(walk.nextNode()) {
                const text=walk.currentNode.textContent.trim();if(!text)continue;
                const el=walk.currentNode.parentElement;const rect=el.getBoundingClientRect();if(!rect.width||!rect.height||rect.top>=innerHeight||rect.bottom<=0||getComputedStyle(el).visibility==='hidden')continue;
                const css=getComputedStyle(el),fg=rgb(css.color),bg=background(el);if(!fg)continue;
                const a=lum(mix(fg,bg)),b=lum(bg),ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
                out.push({text:text.slice(0,65),size:parseFloat(css.fontSize),contrast:Number(ratio.toFixed(2)),class:el.className?.baseVal??el.className,decorative:!!el.closest('[aria-hidden="true"]')});
              }
              return out;
            });
            reports.push({mode,theme,width,height,state,readability});
            // aria-hidden glyphs (the ▲▼ of a change pill) are decoration, not text: size rule only.
            assert.deepEqual(readability.filter(r=>r.size<12 && !r.decorative && !(r.size>=11 && r.text===r.text.toUpperCase())),[],'minimum readable type');
            assert.deepEqual(readability.filter(r=>r.contrast<4.5),[],'WCAG AA dark text');
          }
          fs.writeFileSync(path.join(config.directory,`${mode}-${theme}-${state}-${width}x${height}.png`),(await win.webContents.capturePage()).toPNG()); };
        await capture('empty');
        const empty = await js(() => {
          const q=s=>document.querySelector(s);const box=s=>{const r=q(s).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
          return { left:box('.cL'), central:box('.cM'), right:box('.cR'), composer:box('.comp'),
            archiveGroups:q('.cL').querySelectorAll('.grp').length, overflow:document.documentElement.scrollWidth>innerWidth,
            collapsed:q('.cR').classList.contains('is-collapsed'), placeholder:q('.comp textarea').placeholder };
        });
        assert.equal(empty.overflow,false);
        if (!config.baseline && mode==='modern' && theme==='dark') assert.ok(await js(()=>document.querySelector('.bb-modern-sidebar-brand strong').getBoundingClientRect().left>=80),'macOS brand clears traffic lights');
        if (!config.baseline && mode==='modern' && theme==='dark') { assert.equal(empty.archiveGroups,0);assert.equal(empty.collapsed,true);assert.ok(empty.right.width<=56);assert.equal(empty.placeholder,'Chiedi a Capo…');
          // Nuova restyle (2026-10-02): each desk has its own icon instead of two initials.
          assert.deepEqual(await js(()=>[...document.querySelectorAll('.chat-modern-specialist-avatar')].map(e=>e.dataset.deskIcon)),['capo','macro','options','quant','fundamentals','crypto','eventdesk']);
          assert.deepEqual(await js(()=>[...document.querySelectorAll('.portfolio-question-tickers .chat-position-ticker')].map(e=>e.textContent)),['ACME','ZZTEST','ZZBETA.X','SPY']); }
        await fixture({streamMode:'held',releaseHeld:false});
        await js(() => {const el=document.querySelector('.comp textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(el,'Confronta i rischi di queste posizioni.');el.dispatchEvent(new Event('input',{bubbles:true}));});
        await click('.comp .send');await wait(()=>!!document.querySelector('.comp .send.stop'));
        await wait(()=>document.querySelector('.conv .prose')?.textContent.includes('Fixture stream'));
        await capture('streaming');
        // Response details open only on request now: a streaming answer leaves them closed.
        if (!config.baseline && mode==='modern' && theme==='dark') assert.equal(await js(()=>document.querySelector('.cR').classList.contains('is-collapsed')),true);
        await fixture({releaseHeld:true});await wait(()=>!document.querySelector('.comp .send.stop'));
        await capture('completed');
        if (!config.baseline && mode==='modern' && theme==='dark') {
          const lane=await js(()=>{const a=document.querySelector('.conv .turn').getBoundingClientRect(),b=document.querySelector('.comp .field').getBoundingClientRect();return {messageLeft:a.left,messageRight:a.right,fieldLeft:b.left,fieldRight:b.right,width:a.width};});
          assert.ok(Math.abs(lane.messageLeft-lane.fieldLeft)<=1 && Math.abs(lane.messageRight-lane.fieldRight)<=1,'messages and composer align: '+JSON.stringify(lane));assert.ok(lane.width<=1241,'reading lane is at most 1240 px: '+lane.width);
        }
        reports.push({mode,theme,width,height,empty});
      }
    }
    if (!config.baseline) {
      await click('.bb-interface-menu-toggle');await click('[data-theme-choice="dark"]');await click('.f3d .dk');
      for (const width of [1200,1000,760,560]) {
        win.setContentSize(width,900);await pause(200);
        assert.equal(await js(()=>document.documentElement.scrollWidth>innerWidth),false);
        // Under 1180 px of content the page is one column: conversation first, desks and archive below.
        if(width<1180+232) assert.ok(await js(()=>document.querySelector('.cL').getBoundingClientRect().top>=document.querySelector('.cM').getBoundingClientRect().bottom),'desks stack under the conversation at '+width);
        await click('.chat-inspector-toggle');await pause(100);assert.ok(await js(()=>document.querySelector('.cR').getBoundingClientRect().width>250));assert.equal(await js(()=>document.querySelector('.cR').classList.contains('is-collapsed')),false);
        await click('.chat-inspector-close');
        fs.writeFileSync(path.join(config.directory,`modern-dark-responsive-${width}x900.png`),(await win.webContents.capturePage()).toPNG());
      }
    }
    fs.writeFileSync(path.join(config.directory,'summary.json'),JSON.stringify(reports,null,2));console.log('CHAT_REDESIGN_OK '+reports.length+' screenshot scenarios');
  } catch(e) { console.error(e);win.destroy();app.exit(1);return; }
  win.destroy();app.exit(0);
}
if(require.main===module)run().catch(e=>{console.error(e);process.exitCode=1;});
module.exports={renderer};
