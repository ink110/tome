const http = require('http');
const fs = require('fs');
const { spawn } = require('child_process');
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9333;
const args = process.argv.slice(2);
const url = args[0], out = args[1], sel = args[2] || '';
const chrome = spawn(CHROME, ['--headless=new',`--remote-debugging-port=${PORT}`,'--user-data-dir=/tmp/chrome-prof-shot','--no-sandbox','--disable-gpu','about:blank'], {stdio:'ignore'});
function getJSON(path){ return new Promise((res,rej)=>{ http.get({host:'127.0.0.1',port:PORT,path},r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>{try{res(JSON.parse(d))}catch(e){rej(e)}});}).on('error',rej); }); }
(async()=>{
  for (let i=0;i<60;i++){ try { await getJSON('/json/version'); break; } catch(e){ await new Promise(r=>setTimeout(r,200)); } }
  const targets = await getJSON('/json');
  const page = targets.find(t=>t.type==='page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id=0; const pending=new Map();
  ws.onmessage = e=>{ const m=JSON.parse(e.data); if(m.id&&pending.has(m.id)){pending.get(m.id)(m);pending.delete(m.id);} };
  function send(method,params={}){ return new Promise(res=>{const i=++id;pending.set(i,res);ws.send(JSON.stringify({id:i,method,params}));}); }
  await new Promise(r=>ws.onopen=r);
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:2,mobile:true});
  await send('Page.navigate',{url});
  await new Promise(r=>setTimeout(r,1000));
  if (sel) {
    await send('Runtime.evaluate',{expression:`document.getElementById(${JSON.stringify(sel)}).scrollIntoView({block:'start'})`});
    await new Promise(r=>setTimeout(r,400));
  }
  const shot = await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
  fs.writeFileSync(out, Buffer.from(shot.result.data,'base64'));
  console.log('saved', out);
  ws.close(); chrome.kill();
  process.exit(0);
})().catch(e=>{console.error(e);chrome.kill();process.exit(1)});
