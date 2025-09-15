// app.js — Monaco + TextMate + Lean LSP "InfoView++"
(function(){
  const state = { idx: 0, data: [], serverMode: false, saving: false, dirty: false, autoCompile: false, compiling: false, pollingTemp: false };
  let currentUri = null;
  let legend = null; // semantic tokens legend (if server provides)

  // -------------------- Data load --------------------
  async function loadDefault() {
    const tryPaths = async (paths) => {
      for (const url of paths) {
        try {
          const res = await fetch(url);
          if (!res.ok) continue;
          const data = await res.json();
          if (!Array.isArray(data)) continue;
          state.data = data;
          state.idx = 0;
          render();
          return true;
        } catch (_) { /* try next */ }
      }
      return false;
    };
    if (await tryPaths(['http://127.0.0.1:8000/data', '/data'])) {
      state.serverMode = true; return;
    }
    const ok = await tryPaths(['../sfs4_reshape_with_main.json', './sfs4_reshape_with_main.json', '/sfs4_reshape_with_main.json']);
    if (!ok) throw new Error('Failed to fetch JSON');
  }

  // -------------------- Utilities --------------------
  function getField(obj, keys) {
    for (const k of keys) {
      if (typeof obj[k] === 'string' && obj[k].trim()) return obj[k];
    }
    return '';
  }
  function $(id){ return document.getElementById(id); }

  // -------------------- Diagnostics -> Monaco markers + Messages list --------------------
  function diagnosticsToMarkers(diags) {
    const mk = [];
    for (const d of (diags||[])) {
      const s = d.severity;
      mk.push({
        severity: s === 2 ? monaco.MarkerSeverity.Warning : s === 3 ? monaco.MarkerSeverity.Info : s === 4 ? monaco.MarkerSeverity.Hint : monaco.MarkerSeverity.Error,
        message: d.message || '',
        startLineNumber: (d.range?.start?.line ?? 0) + 1,
        startColumn:     (d.range?.start?.character ?? 0) + 1,
        endLineNumber:   (d.range?.end?.line ?? 0) + 1,
        endColumn:       (d.range?.end?.character ?? 0) + 1
      });
    }
    return mk;
  }
  function applyDiagnosticsFromLean(stderrText) {
    const editor = window.__monacoEditor; const mon = window.monaco; if (!editor || !mon) return;
    const model = editor.getModel(); if (!model) return;
    const markers = [];
    const lines = (stderrText || '').split(/\r?\n/);
    const re = /^(?:[^:]+):(\d+):(\d+):\s*(error|warning|info)\s*:\s*(.*)$/i;
    for (const ln of lines) {
      const m = re.exec(ln); if (!m) continue;
      const line = Math.max(1, parseInt(m[1],10)||1);
      const col  = Math.max(1, parseInt(m[2],10)||1);
      const sevS = (m[3]||'error').toLowerCase();
      const msg  = (m[4]||'').trim();
      markers.push({ severity: sevS==='warning'? mon.MarkerSeverity.Warning : sevS==='info'? mon.MarkerSeverity.Info : mon.MarkerSeverity.Error, message: msg || sevS, startLineNumber: line, startColumn: col, endLineNumber: line, endColumn: col+1 });
    }
    mon.editor.setModelMarkers(model, 'lean', markers);
    renderMessages(markers.map(m => ({severity: m.severity, message: m.message, startLineNumber: m.startLineNumber, startColumn: m.startColumn})));
  }
  function renderMessages(items) {
    const ul = $('messagesList'); if (!ul) return;
    ul.innerHTML = '';
    for (const it of (items||[])) {
      const li = document.createElement('li'); li.className = 'msg ' + (it.severity===1?'err':it.severity===2?'warn':'info');
      const sev = document.createElement('div'); sev.className = 'sev ' + (it.severity===1?'err':it.severity===2?'warn':'info'); sev.textContent = it.severity===1?'●':it.severity===2?'▲':'ℹ';
      const loc = document.createElement('div'); loc.className = 'loc'; loc.textContent = (it.startLineNumber?`${it.startLineNumber}:${it.startColumn||1}`:''); 
      const msg = document.createElement('div'); msg.className = 'msgtext'; msg.textContent = it.message || '';
      li.appendChild(sev); li.appendChild(loc); li.appendChild(msg);
      ul.appendChild(li);
    }
  }

  // Register hover to display marker message
  function ensureMarkerHoverRegistered() {
    if (!window.monaco) return;
    if (window.__leanMarkerHover) return;
    window.__leanMarkerHover = monaco.languages.registerHoverProvider('lean', {
      provideHover(model, position) {
        const ms = monaco.editor.getModelMarkers({ owner:'lean', resource: model.uri })
          .filter(m => position.lineNumber === m.startLineNumber && position.column >= m.startColumn && position.column <= m.endColumn);
        if (!ms.length) return null;
        const text = ms.map(m => m.message).join('\n\n');
        return { range: ms[0], contents: [{ value: text }] };
      }
    });
  }

  // -------------------- Lean transport (SSE + POST) --------------------
  const LeanClient = (() => {
    let nextId = 1; const inflight = new Map();
    let reconnectTimer = null; let backoff = 500;
    function connect() {
      try { if (window.__leanES) { window.__leanES.close(); window.__leanES = null; } } catch(_) {}
      try {
        const es = new EventSource('/lean_events');
        window.__leanES = es;
        es.onmessage = (ev) => {
          try {
            const msg = JSON.parse(ev.data);
            if (msg && msg.id && inflight.has(msg.id)) {
              inflight.get(msg.id).resolve(msg); inflight.delete(msg.id);
            } else {
              handleLeanNotification(msg);
            }
          } catch(_) {}
        };
        es.onopen = () => { updateLspStatus(true); backoff = 500; };
        es.onerror = () => {
          updateLspStatus(false);
          try { es.close(); } catch(_) {}
          window.__leanES = null;
          if (reconnectTimer) clearTimeout(reconnectTimer);
          reconnectTimer = setTimeout(connect, Math.min(8000, backoff));
          backoff = Math.min(8000, backoff * 2);
        };
      } catch(_) { /* ignore */ }
    }
    async function send(method, params) {
      const id = nextId++; const payload = { jsonrpc:'2.0', id, method, params };
      return new Promise((resolve, reject) => {
        inflight.set(id, { resolve, reject });
        fetch('/lean_rpc', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) })
          .catch(err => { inflight.delete(id); reject(err); });
      });
    }
    return { connect, send };
  })();
  function updateLspStatus(ok){ const el = $('lspStatus'); if (!el) return; el.textContent = ok ? 'LSP: connected' : 'LSP: offline'; el.className = 'badge ' + (ok?'ok':'err'); }

  // -------------------- Current doc <-> LSP --------------------
  async function openAndSyncCurrent() {
    if (!state.serverMode) return;
    const i = state.idx;
    const code = getCode();
    try {
      const res = await fetch('/sync_lean', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ index: i + 1, code }) });
      const out = await res.json();
      if (out && out.ok && out.uri) currentUri = out.uri;
    } catch(_) {}
  }

  // -------------------- Debounced "InfoView++" --------------------
  const reqInfoView = (() => {
    let timer = null;
    return function schedule() {
      if (!window.__monacoEditor || !currentUri) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(async () => {
        const pos = window.__monacoEditor.getPosition(); if (!pos) return;
        const p = { line: pos.lineNumber - 1, character: pos.column - 1 };
        // Expected type: use hover text (best-effort)
        try {
          const r = await LeanClient.send('textDocument/hover', { textDocument: { uri: currentUri }, position: p });
          const toStr = (c) => typeof c === 'string' ? c : (c?.value || c?.contents || '');
          const txt = r && r.result ? (Array.isArray(r.result.contents) ? r.result.contents.map(toStr).join('\n') : toStr(r.result.contents)) : '';
          $('expectedType').textContent = txt || '';
        } catch(_) { $('expectedType').textContent = ''; }
        // Goals
        try {
          const g = await LeanClient.send('$/lean/plainGoal', { textDocument: { uri: currentUri }, position: p, includeExtra: true });
          const txt = g && g.result && (g.result.rendered || g.result.goals || g.result.text || g.result)? g.result.rendered || g.result.goals || g.result.text || '' : '';
          $('goalView').textContent = typeof txt === 'string' ? txt : JSON.stringify(txt);
        } catch(_) { /* ignore */ }
        // Term goal
        try {
          const t = await LeanClient.send('$/lean/plainTermGoal', { textDocument: { uri: currentUri }, position: p });
          const txt = t && t.result && (t.result.rendered || t.result.text || t.result)? t.result.rendered || t.result.text || '' : '';
          $('termGoalView').textContent = typeof txt === 'string' ? txt : JSON.stringify(txt);
        } catch(_) { $('termGoalView').textContent = ''; }
      }, 120);
    };
  })();

  // -------------------- Render + bindings --------------------
  function render() {
    const n = state.data.length; if (n === 0) return;
    const i = Math.max(0, Math.min(state.idx, n - 1)); state.idx = i;
    const item = state.data[i] || {};
    const id = item.id ?? (i + 1);
    const title = item.title || item.problem || item.name || '';
    const formal = getField(item, ['formalProof','lean','leanCode','lean_code','proof','code']);
    const mainStmt = item['main theorem statement'] || '';

    $('itemId').textContent = id; $('itemTitle').textContent = title || '—';
    if (window.__formalEditor) { try { window.__formalEditor.setValue(formal); } catch(_){} }
    const edHost = $('mainEditor');
    if (edHost) {
      if (window.__monacoEditor) { try { window.__monacoEditor.setValue(mainStmt); } catch(_){} }
    }
    const statusEl = $('compileStatus'); if (statusEl) statusEl.textContent = state.serverMode ? 'Server mode' : 'Read-only (no server)';
    $('compileStdout').textContent = ''; $('compileStderr').textContent = '';
    const pos = `${i+1} / ${n}`; $('pos').textContent = pos; $('posBottom').textContent = pos;
    const j1 = $('jumpInput'); const j2 = $('jumpInputBottom'); if (j1) j1.value = String(i+1); if (j2) j2.value = String(i+1);
    if (state.serverMode && state.autoCompile && typeof state.triggerCompile === 'function') { state.triggerCompile(); }
    openAndSyncCurrent();
  }

  function stopPollingTemp(){ state.pollingTemp = false; if (window.__pollTimer) { clearTimeout(window.__pollTimer); window.__pollTimer = null; } const btn=$('editInVSCode'); if (btn) btn.textContent='Modify in VSCode'; }
  function next(){ if (state.idx < state.data.length - 1) { state.idx++; render(); } }
  function prev(){ if (state.idx > 0) { state.idx--; render(); } }

  function bind() {
    const nextBtn = $('nextBtn'); const prevBtn = $('prevBtn');
    const nextBtnB = $('nextBtnBottom'); const prevBtnB = $('prevBtnBottom');
    const loadBtn = $('loadBtn'); const fileInput = $('fileInput');
    const jumpInput = $('jumpInput'); const jumpBtn = $('jumpBtn');
    const jumpInputB = $('jumpInputBottom'); const jumpBtnB = $('jumpBtnBottom');
    const editorHost = $('mainEditor'); const formalHost = $('formalViewer');
    const compileBtn = $('compileBtn'); const autoChk = $('autoCompile');
    const toggleInfo = $('toggleInfoview'); const editVSBtn = $('editInVSCode');
    const statusEl = $('compileStatus'); const out1 = $('compileStdout'); const out2 = $('compileStderr'); const infoView = $('infoview');

    nextBtn.addEventListener('click', next); nextBtnB.addEventListener('click', next);
    prevBtn.addEventListener('click', prev); prevBtnB.addEventListener('click', prev);

    function jumpTo(val) {
      const n = state.data.length; if (!n) return;
      let idx = parseInt(val, 10); if (!Number.isFinite(idx)) return;
      idx = Math.max(1, Math.min(n, idx)); state.idx = idx - 1; render();
    }
    const bindJump = (inputEl, btnEl) => {
      if (!inputEl || !btnEl) return;
      btnEl.addEventListener('click', () => jumpTo(inputEl.value));
      inputEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') jumpTo(inputEl.value); });
    };
    bindJump(jumpInput, jumpBtn); bindJump(jumpInputB, jumpBtnB);

    loadBtn.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', async (e) => {
      const f = e.target.files && e.target.files[0]; if (!f) return;
      try { const text = await f.text(); const data = JSON.parse(text); if (!Array.isArray(data)) throw new Error('JSON must be an array'); state.data = data; state.idx = 0; state.serverMode = false; render(); }
      catch (err) { alert('Failed to load selected file: ' + err.message); }
    });

    // Debounced save to server
    let saveTimer = null;
    const updateStatus = (msg) => { if (statusEl) statusEl.textContent = msg; };
    const saveNow = async () => {
      if (!state.serverMode) return;
      const i = state.idx; const code = getCode();
      state.saving = true; state.dirty = false; updateStatus('Saving…');
      try {
        const res = await fetch('/update', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ index: i + 1, code }) });
        const out = await res.json().catch(() => ({}));
        if (!res.ok || out.ok === false) throw new Error(out.error || 'HTTP error');
        state.data[i]['main theorem statement'] = code; updateStatus('Saved');
      } catch (e) { console.error(e); updateStatus('Save failed'); }
      finally { state.saving = false; }
    };
    const debounceSave = () => { state.dirty = true; if (saveTimer) clearTimeout(saveTimer); saveTimer = setTimeout(saveNow, 600); };

    // Debounced Lean sync
    let syncTimer = null;
    const debounceSync = () => {
      if (!state.serverMode) return;
      if (syncTimer) clearTimeout(syncTimer);
      syncTimer = setTimeout(async () => {
        try {
          const i = state.idx; const code = getCode();
          await fetch('/sync_lean', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ index: i + 1, code }) });
        } catch(_) {}
      }, 250);
    };

    // Code change wiring
    onCodeChange(() => { if (!state.data.length) return; state.data[state.idx]['main theorem statement'] = getCode(); debounceSave(); debounceSync(); if (state.autoCompile) triggerCompile(); reqInfoView(); });

    // Compile
    async function triggerCompile() {
      if (!state.serverMode || state.compiling) return;
      state.compiling = true;
      const i = state.idx; const code = getCode();
      updateStatus('Compiling…'); out1.textContent = ''; out2.textContent = '';
      try {
        const res = await fetch('/compile', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ index: i + 1, code }) });
        const out = await res.json(); const ok = !!out.success;
        out1.textContent = out.stdout || ''; out2.textContent = out.stderr || ''; updateStatus(ok ? 'Compiled ✅' : 'Compile failed ❌');
        try { if (window.monaco && window.__monacoEditor) applyDiagnosticsFromLean(out.stderr || ''); } catch(_) {}
      } catch (e) { out2.textContent = String(e); updateStatus('Compile error'); }
      finally { state.compiling = false; }
    }
    state.triggerCompile = triggerCompile;
    compileBtn.addEventListener('click', () => { state.autoCompile = true; if (autoChk) autoChk.checked = true; triggerCompile(); });
    autoChk?.addEventListener('change', (e) => { state.autoCompile = !!e.target.checked; if (state.autoCompile) triggerCompile(); });
    toggleInfo?.addEventListener('click', () => { if (infoView.hasAttribute('open')) infoView.removeAttribute('open'); else infoView.setAttribute('open',''); });

    // VS Code temp edit sync (SSE + polling fallback)
    async function pollTempLoop() {
      if (!state.pollingTemp) { window.__pollTimer = null; return; }
      if (!state.serverMode) return;
      try {
        const res = await fetch('/temp_read'); if (!res.ok) throw new Error('HTTP error');
        const out = await res.json(); if (out && out.exists && typeof out.code === 'string') {
          const currentIndex = state.idx + 1;
          if (!out.index || out.index === currentIndex) {
            const cur = getCode(); if (cur !== out.code) { setCode(out.code); if (!window.__monacoEditor) $('mainEditor').dispatchEvent(new Event('input')); }
          }
        }
      } catch (_) {}
      finally { if (window.__pollTimer) clearTimeout(window.__pollTimer); window.__pollTimer = setTimeout(pollTempLoop, 50); }
    }
    editVSBtn?.addEventListener('click', async () => {
      if (!state.serverMode) { alert('This feature requires the local server. Run server.py.'); return; }
      if (state.pollingTemp) { state.pollingTemp = false; if (window.__pollTimer) { clearTimeout(window.__pollTimer); window.__pollTimer = null; } editVSBtn.textContent='Modify in VSCode'; return; }
      const i = state.idx; const code = getCode();
      try {
        const res = await fetch('/prepare_temp', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ index: i + 1, code, open_vscode: true }) });
        const out = await res.json(); if (!res.ok || out.ok === false) throw new Error(out.error || 'Cannot prepare temp file');
        state.pollingTemp = true; editVSBtn.textContent = 'Stop VSCode Sync';
        try {
          if (window.__evtSrc) { window.__evtSrc.close(); window.__evtSrc = null; }
          const es = new EventSource('/temp_events'); window.__evtSrc = es;
          es.onmessage = (ev) => { if (!state.pollingTemp) return; try { const out = JSON.parse(ev.data); if (out && out.exists && typeof out.code === 'string') { const currentIndex = state.idx + 1; if (!out.index || out.index === currentIndex) { const cur = getCode(); if (cur !== out.code) { setCode(out.code); if (!window.__monacoEditor) $('mainEditor').dispatchEvent(new Event('input')); }}} } catch(_){} };
          es.onerror = () => { if (window.__evtSrc) { window.__evtSrc.close(); window.__evtSrc = null; } if (window.__pollTimer) clearTimeout(window.__pollTimer); pollTempLoop(); };
        } catch (_) { if (window.__pollTimer) clearTimeout(window.__pollTimer); pollTempLoop(); }
      } catch (e) { alert(String(e)); }
    });

    document.addEventListener('visibilitychange', () => { if (document.hidden) { if (window.__pollTimer) { clearTimeout(window.__pollTimer); window.__pollTimer = null; } } else { if (state.pollingTemp && !window.__pollTimer) pollTempLoop(); } });
    window.addEventListener('beforeunload', () => { if (window.__pollTimer) clearTimeout(window.__pollTimer); if (window.__evtSrc) { window.__evtSrc.close(); window.__evtSrc = null; } });
    window.addEventListener('keydown', (e) => { if (e.key === 'ArrowRight') next(); if (e.key === 'ArrowLeft') prev(); });

    // -------------------- Monaco init (TextMate grammar + VSCode theme) --------------------
    function initMonaco() {
      if (!window.require || !editorHost) return;
      window.require.config({ paths: { 'vs': 'https://cdn.jsdelivr.net/npm/monaco-editor@0.49.0/min/vs' } });
      window.require(['vs/editor/editor.main'], async function() {
        try {
          // 1) Create editor(s)
          monaco.languages.register({ id: 'lean' });
          // temporary Monarch until TM wired (fallback)
          monaco.languages.setMonarchTokensProvider('lean', {
            keywords: ['theorem','lemma','def','defn','axiom','abbrev','structure','class','instance','where','open','scoped','namespace','end','variable','variables','section','by','intro','intros','exact','apply','refine','match','with','fun','if','then','else','do','let','in','macro','macro_rules','deriving','mutual','termination','decreasing_by','have','from','calc','simp','dsimp','rw','rfl','sorry'],
            typeKeywords: ['Prop','Type','Sort','Nat','Int','Rat','Real','Bool','True','False'],
            builtins: ['List','Option','Some','None','And','Or','Not','Eq','HEq','Subtype','Sigma','Sum','Prod','PUnit','PEmpty','Unit','Empty','Decidable','DecidableEq'],
            tokenizer: {
              root: [
                [/--.*/, 'comment'],
                [/\/\-/, 'comment', '@comment'],
                [/\"([^\"\\]|\\.)*\"/, 'string'],
                [/\d+(\.\d+)?/, 'number'],
                [/[A-Za-z_α-ωΑ-Ω][\w_ʼ′’₀-₉]*/, { cases: { '@keywords':'keyword', '@typeKeywords':'type', '@builtins':'type.identifier', '@default':'identifier' } }],
                [/[[\]{}().,:;]/, 'delimiter'],
                [/[:=+\-*/%<>!|&^~?]+/, 'operator']
              ],
              comment: [
                [/-\//, 'comment', '@pop'],
                [/./, 'comment']
              ]
            }
          });

          // Load VSCode Lean 4 TextMate grammar + VS Code "Lean 4" theme colors
          await installTextMateAndTheme([window.__monacoEditor, ...(window.__formalEditor ? [window.__formalEditor] : [])]);

          // Define theme (fallback if converting VS Code theme fails)
          if (!window.__themeInstalled) {
            monaco.editor.defineTheme('sfs4-dark', {
              base: 'vs-dark', inherit: true,
              rules: [
                { token: 'comment', foreground: '9ca3af', fontStyle: 'italic' },
                { token: 'keyword', foreground: '7dd3fc', fontStyle: 'bold' },
                { token: 'string',  foreground: 'a7f3d0' },
                { token: 'number',  foreground: 'fca5a5' },
                { token: 'type',    foreground: 'fdba74' },
                { token: 'type.identifier', foreground: 'fdba74' },
                { token: 'operator',foreground: 'cbd5e1' },
                { token: 'identifier', foreground: 'e2e8f0' }
              ],
              colors: { 'editor.background': '#0f172a' }
            });
          }

          const initial = state.data[state.idx]?.['main theorem statement'] || '';
          window.__monacoEditor = monaco.editor.create(editorHost, {
            value: initial, language: 'lean', theme: window.__themeInstalled || 'vs-dark',
            minimap: { enabled: false }, automaticLayout: true, fontSize: 14,
            wordWrap: 'on', wordWrapColumn: 120, wrappingIndent: 'same'
          });

          // read-only Monaco for left viewer (to benefit from same TM highlighting)
          if (formalHost && !window.__formalEditor) {
            window.__formalEditor = monaco.editor.create(formalHost, {
              value: getField(state.data[state.idx] || {}, ['formalProof','lean','leanCode','lean_code','proof','code']),
              language: 'lean', theme: window.__themeInstalled || 'vs-dark', readOnly: true,
              lineNumbers: 'on', glyphMargin: false, folding: false, minimap: { enabled: false }, renderLineHighlight: 'none',
              wordWrap: 'on', wordWrapColumn: 120, wrappingIndent: 'same',
              scrollbar: { vertical: 'auto', horizontal: 'auto' }
            });
          }

          // Wire after editors exist
          window.__monacoEditor.onDidChangeModelContent(() => { if (!state.data.length) return; state.data[state.idx]['main theorem statement'] = window.__monacoEditor.getValue(); debounceSave(); if (state.autoCompile) state.triggerCompile(); reqInfoView(); });
          window.__monacoEditor.onDidChangeCursorPosition(() => reqInfoView());
          window.__monacoEditor.onDidFocusEditorWidget(() => reqInfoView());
          ensureMarkerHoverRegistered();

          // LSP hover/def providers
          registerLspProviders();

          // Connect Lean transport
          try {
            if (!window.__leanTransportConnected) { LeanClient.connect(); window.__leanTransportConnected = true; const statusEl = $('compileStatus'); if (statusEl) statusEl.textContent = 'Server mode + Lean transport'; }
          } catch(_) {}
        } catch (e) { console.warn('Monaco init failed', e); }
      });
    }

    async function installTextMateAndTheme(editors) {
      try {
        const [{ loadWASM }, monacoTextmate, editorTextmate] = await Promise.all([
          import('https://cdn.jsdelivr.net/npm/onigasm@2.2.5/dist/onigasm.min.js'),
          import('https://cdn.jsdelivr.net/npm/monaco-textmate@8.0.0/dist/index.min.js'),
          import('https://cdn.jsdelivr.net/npm/monaco-editor-textmate@3.0.1/dist/index.min.js')
        ]);
        await loadWASM('https://cdn.jsdelivr.net/npm/onigasm@2.2.5/dist/onigasm.wasm');
        const { Registry } = monacoTextmate;
        const { wireTmGrammars } = editorTextmate;

        // Fetch grammar (local first; then remote)
        const tryFetchText = async (url) => { const r = await fetch(url, { cache:'no-store' }); if (!r.ok) throw new Error('HTTP '+r.status); return await r.text(); };
        const grammarCandidates = [
          '/grammars/lean4.json',
          '/grammars/lean4.tmLanguage.json',
          'https://raw.githubusercontent.com/leanprover/vscode-lean4/master/vscode-lean4/syntaxes/lean4.json',
          'https://raw.githubusercontent.com/leanprover/vscode-lean4/master/syntaxes/lean.tmLanguage.json'
        ];
        let grammarContent = ''; let scopeName = 'source.lean4';
        for (const url of grammarCandidates) { try { grammarContent = await tryFetchText(url); break; } catch(_){} }
        if (grammarContent) { try { const parsed = JSON.parse(grammarContent); if (parsed && typeof parsed.scopeName === 'string') scopeName = parsed.scopeName; } catch(_){} }
        const registry = new Registry({ getGrammarDefinition: async (scope) => (scope === scopeName ? { format: 'json', content: grammarContent } : null) });
        const grammars = new Map(); grammars.set('lean', scopeName);
        await wireTmGrammars(monaco, registry, grammars, editors.filter(Boolean));

        // Try to load the official VS Code theme used by Lean extension and convert to Monaco
        // If this fails, our fallback theme 'sfs4-dark' remains.
        try {
          const themeJson = await tryFetchText('https://raw.githubusercontent.com/leanprover/vscode-lean4/master/vscode-lean4/themes/lean4-color-theme.json');
          const theme = JSON.parse(themeJson);
          const conv = vscodeThemeToMonaco(theme);
          monaco.editor.defineTheme('lean4-vscode', conv);
          monaco.editor.setTheme('lean4-vscode');
          window.__themeInstalled = 'lean4-vscode';
        } catch (e) { /* keep fallback */ }
      } catch (e) {
        console.info('TextMate/theme setup skipped; Monarch fallback in effect:', e?.message || e);
      }
    }

    function vscodeThemeToMonaco(theme) {
      // Convert a VSCode "tokenColors" theme (very simplified) to a Monaco theme
      const rules = [];
      const colors = Object.assign({ 'editor.background':'#0f172a' }, theme.colors || {});
      for (const tc of (theme.tokenColors || [])) {
        const settings = tc.settings || {};
        const foreground = (settings.foreground || '').replace('#','');
        const fontStyle = settings.fontStyle || '';
        const scopes = Array.isArray(tc.scope) ? tc.scope : (tc.scope ? (''+tc.scope).split(',').map(s=>s.trim()) : []);
        for (const sc of scopes) {
          if (!sc) continue;
          const token = sc.replace(/[. ]+/g,'-');
          const r = { token };
          if (foreground) r.foreground = foreground;
          if (fontStyle) r.fontStyle = fontStyle;
          rules.push(r);
        }
      }
      return { base: 'vs-dark', inherit: true, rules, colors };
    }

    function registerLspProviders() {
      try {
        const hoverDisp = monaco.languages.registerHoverProvider('lean', {
          provideHover(model, position) {
            if (!currentUri) return null;
            return LeanClient.send('textDocument/hover', { textDocument: { uri: currentUri }, position: { line: position.lineNumber - 1, character: position.column - 1 } })
              .then(res => {
                const r = res && res.result; if (!r) return null;
                const toText = (c) => typeof c === 'string' ? c : (c?.value || c?.contents || '');
                const text = Array.isArray(r.contents) ? r.contents.map(toText).join('\n') : toText(r.contents);
                return { range: new monaco.Range(position.lineNumber, 1, position.lineNumber, 1), contents: [{ value: text || '' }] };
              }).catch(() => null);
          }
        });
        const defDisp = monaco.languages.registerDefinitionProvider('lean', {
          provideDefinition(model, position) {
            if (!currentUri) return null;
            return LeanClient.send('textDocument/definition', { textDocument: { uri: currentUri }, position: { line: position.lineNumber - 1, character: position.column - 1 } })
              .then(async res => {
                const d = res && res.result; if (!d) return null;
                const defs = Array.isArray(d) ? d : [d];
                const locations = [];
                for (const x of defs) {
                  if (!x) continue;
                  const uri = x.uri || x.targetUri;
                  const rng = x.range || x.targetSelectionRange || x.selectionRange;
                  if (!uri || !rng) continue;
                  if (uri === currentUri) {
                    locations.push({ uri: model.uri, range: new monaco.Range((rng.start?.line ?? 0) + 1, (rng.start?.character ?? 0) + 1, (rng.end?.line ?? 0) + 1, (rng.end?.character ?? 0) + 1) });
                  } else if (uri.startsWith('file://')) {
                    try {
                      const r = await fetch('/read_file', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ uri }) });
                      const out = await r.json();
                      if (out && out.ok && typeof out.code === 'string') {
                        let m = monaco.editor.getModel(monaco.Uri.parse(uri));
                        if (!m) m = monaco.editor.createModel(out.code, 'lean', monaco.Uri.parse(uri));
                        locations.push({ uri: m.uri, range: new monaco.Range((rng.start?.line ?? 0) + 1, (rng.start?.character ?? 0) + 1, (rng.end?.line ?? 0) + 1, (rng.end?.character ?? 0) + 1) });
                      }
                    } catch(_) {}
                  }
                }
                return locations.length ? locations : null;
              }).catch(() => null);
          }
        });
        window.__leanProviders = [hoverDisp, defDisp];
      } catch(_) {}
    }

    // -------------------- Get/Set code in editor --------------------
    function getCode(){ return window.__monacoEditor ? window.__monacoEditor.getValue() : ($('mainEditor').value ?? ''); }
    function setCode(v){ if (window.__monacoEditor) window.__monacoEditor.setValue(v); else if ($('mainEditor').value !== undefined) $('mainEditor').value = v; }
    function onCodeChange(cb){ if (window.__monacoEditor) window.__monacoEditor.onDidChangeModelContent(cb); else $('mainEditor').addEventListener('input', cb); }

    // -------------------- Bootstrap --------------------
    function bindShortcuts(){
      window.addEventListener('keydown', (e) => { if (e.key === 'ArrowRight') next(); if (e.key === 'ArrowLeft') prev(); });
    }

    initMonaco();
    // Split slider logic
    (function(){
      const range = document.getElementById('splitRange');
      if (!range) return;
      const root = document.documentElement;
      const saved = localStorage.getItem('sfs4_split');
      const val = saved ? Math.min(80, Math.max(20, parseInt(saved,10)||50)) : 50;
      range.value = String(val);
      root.style.setProperty('--split', val + '%');
      range.addEventListener('input', () => {
        const v = Math.min(80, Math.max(20, parseInt(range.value,10)||50));
        root.style.setProperty('--split', v + '%');
        localStorage.setItem('sfs4_split', String(v));
        if (window.__monacoEditor) window.__monacoEditor.layout();
        if (window.__formalEditor) window.__formalEditor.layout();
      });
    })();
    // Main width numeric input
    (function(){
      const input = document.getElementById('mainWidthInput');
      if (!input) return;
      const root = document.documentElement;
      const saved = localStorage.getItem('sfs4_main_width');
      const clamp = (n) => Math.min(2400, Math.max(900, Math.round(n)));
      const initial = clamp(saved ? parseInt(saved,10) || 1400 : 1400);
      input.value = String(initial);
      root.style.setProperty('--main-width', initial + 'px');
      const apply = (v) => {
        const n = clamp(parseInt(v,10) || initial);
        root.style.setProperty('--main-width', n + 'px');
        localStorage.setItem('sfs4_main_width', String(n));
        if (window.__monacoEditor) window.__monacoEditor.layout();
        if (window.__formalEditor) window.__formalEditor.layout();
      };
      input.addEventListener('change', () => apply(input.value));
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') apply(input.value); });
    })();
    bindShortcuts();
  }

  bind();
  loadDefault().catch(err => {
    console.error(err);
    alert('Failed to load data: ' + err.message + '\nYou can use "Load JSON…" to pick the file manually.');
  });

  // Keep InfoView fresh as mouse moves / cursor changes
  (function(){
    let t = null;
    window.addEventListener('mousemove', () => { if (t) clearTimeout(t); t = setTimeout(() => { if (typeof reqInfoView === 'function') reqInfoView(); }, 150); });
    document.addEventListener('keydown', () => { if (typeof reqInfoView === 'function') reqInfoView(); });
  })();
})();
