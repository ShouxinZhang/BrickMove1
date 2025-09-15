async function loadConfig(){
  try{
    const r=await fetch('/load-config');
    if(!r.ok) throw new Error('加载失败');
    const cfg=await r.json();
    document.getElementById('model').value=cfg.model||'x-ai/grok-code-fast-1';
    document.getElementById('temperature').value=cfg.temperature??0.0;
    document.getElementById('top_p').value=cfg.top_p??1.0;
    document.getElementById('max_concurrency').value=cfg.max_concurrency??8;
    document.getElementById('max_input_chars').value=cfg.max_input_chars??0;
    document.getElementById('max_tokens').value=cfg.max_tokens??0;
    document.getElementById('system_prompt').value=cfg.system_prompt||'';
    try{
      document.getElementById('history').value=cfg.history?JSON.stringify(cfg.history, null, 2):'';
    }catch(e){document.getElementById('history').value='';}
    setMsg('配置已加载');
  }catch(e){setMsg('未找到配置，将使用默认值');}
}

async function saveConfig(ev){
  ev.preventDefault();
  let historyArr=null;
  const historyRaw=document.getElementById('history').value.trim();
  if(historyRaw){
    try{ historyArr=JSON.parse(historyRaw);}catch(e){ setMsg('history JSON 解析失败'); return; }
  }
  const body={
    model: document.getElementById('model').value || 'x-ai/grok-code-fast-1',
    temperature: parseFloat(document.getElementById('temperature').value||'0'),
    top_p: parseFloat(document.getElementById('top_p').value||'1'),
    max_concurrency: parseInt(document.getElementById('max_concurrency').value||'8'),
    max_input_chars: parseInt(document.getElementById('max_input_chars').value||'0'),
    max_tokens: parseInt(document.getElementById('max_tokens').value||'0'),
    system_prompt: document.getElementById('system_prompt').value || '',
    history: historyArr
  };
  const r=await fetch('/save-config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(r.ok){setMsg('已保存');}else{setMsg('保存失败');}
}

function setMsg(t){document.getElementById('msg').textContent=t}

document.getElementById('btn-load').addEventListener('click',loadConfig);
document.getElementById('config-form').addEventListener('submit',saveConfig);

loadConfig();

// --- Drag & Drop processing ---
const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('file-input');
const pickBtn = document.getElementById('pick-files');
const runMsg = document.getElementById('run-msg');
const runLog = document.getElementById('run-log');

function setRunMsg(t, cls){
  runMsg.textContent = t || '';
  runMsg.className = cls || '';
}
function appendLog(t){
  const time = new Date().toLocaleTimeString();
  runLog.textContent += `[${time}] ${t}\n`;
  runLog.scrollTop = runLog.scrollHeight;
}

function getMode(){
  const el = document.querySelector('input[name="mode"]:checked');
  return el ? el.value : 'local';
}

pickBtn.addEventListener('click', ()=> fileInput.click());
fileInput.addEventListener('change', async (e)=>{
  if(e.target.files && e.target.files.length>0){
    await handleFiles(e.target.files);
    fileInput.value = '';
  }
});

['dragenter','dragover'].forEach(evt => dropzone.addEventListener(evt, (e)=>{
  e.preventDefault(); e.stopPropagation();
  dropzone.classList.add('dragover');
}));
['dragleave','drop'].forEach(evt => dropzone.addEventListener(evt, (e)=>{
  e.preventDefault(); e.stopPropagation();
  dropzone.classList.remove('dragover');
}));

dropzone.addEventListener('drop', async (e)=>{
  const dt = e.dataTransfer;
  if(!dt) return;
  const items = dt.items ? Array.from(dt.items) : [];
  if(items.length){
    const files = await readDataTransferItems(items);
    await handleFiles(files);
  } else {
    const files = Array.from(dt.files || []);
    await handleFiles(files);
  }
});

function onlyLean(files){
  return files.filter(f => f.name.toLowerCase().endsWith('.lean'));
}

async function handleFiles(fileList){
  const mode = getMode();
  const leanFiles = onlyLean(Array.from(fileList));
  if(leanFiles.length===0){ setRunMsg('未检测到 .lean 文件', 'error'); return; }
  setRunMsg(`准备处理 ${leanFiles.length} 个文件（模式：${mode}）...`);
  appendLog(`接收 ${leanFiles.length} 个 .lean 文件`);
  try{
    const form = new FormData();
    for(const f of leanFiles){ form.append('files', f, f.webkitRelativePath || f.name); }
    form.append('mode', mode);
    // Server should create folder newBlocks+日期+时间 and return its name
    const r = await fetch('/process', { method:'POST', body: form });
    let res = null;
    try { res = await r.json(); } catch(_) { /* ignore */ }
    if(!r.ok){
      if(res && res.logs){ res.logs.forEach(l=>appendLog(l)); }
      const errMsg = (res && (res.error || res.message)) ? `${res.error||res.message}` : `服务返回 ${r.status}`;
      throw new Error(errMsg);
    }
    if(res && res.outputDir){
      setRunMsg(`处理完成，输出目录：${res.outputDir}`, 'success');
      appendLog(`输出目录：${res.outputDir}`);
      if(Array.isArray(res.logs)) res.logs.forEach(l=>appendLog(l));
    }else{
      setRunMsg('处理完成，但未返回输出目录', 'error');
    }
  }catch(err){
    console.error(err);
    setRunMsg(`处理失败：${err.message}`, 'error');
    appendLog(`错误：${err.stack||err}`);
  }
}

async function readDataTransferItems(items){
  const filePromises = [];
  for(const item of items){
    const entry = item.webkitGetAsEntry ? item.webkitGetAsEntry() : null;
    if(entry){
      filePromises.push(traverseEntry(entry));
    }else if(item.kind === 'file'){
      const f = item.getAsFile();
      if(f) filePromises.push(Promise.resolve([f]));
    }
  }
  const nested = await Promise.all(filePromises);
  return nested.flat();
}

function traverseEntry(entry){
  return new Promise(resolve => {
    if(entry.isFile){
      entry.file(f => resolve([f]), () => resolve([]));
    }else if(entry.isDirectory){
      const dirReader = entry.createReader();
      const all = [];
      const readEntries = () => {
        dirReader.readEntries(async entries => {
          if(!entries.length){
            resolve(all.flat());
          }else{
            const ps = entries.map(traverseEntry);
            const batch = await Promise.all(ps);
            all.push(...batch);
            readEntries();
          }
        }, () => resolve(all.flat()));
      };
      readEntries();
    }else{
      resolve([]);
    }
  });
}