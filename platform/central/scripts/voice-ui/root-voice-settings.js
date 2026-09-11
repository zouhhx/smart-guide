// Editable source injected by patch-root-ui.py into the deployed React bundle.
function uaLocalVoiceSettings({ notify, onSaved }) {
  const h = l.createElement;
  const [saved, setSaved] = l.useState(null);
  const [draft, setDraft] = l.useState({ provider: '', voice: '' });
  const [catalog, setCatalog] = l.useState(null);
  const [text, setText] = l.useState('欢迎来到思德科技展厅，很高兴为您服务。');
  const [busy, setBusy] = l.useState('');
  const [error, setError] = l.useState('');
  const audio = l.useRef(null), pending = l.useRef(null);
  const mounted = l.useRef(true);
  function stop() {
    pending.current?.abort(); pending.current = null;
    if (audio.current) { audio.current.pause(); audio.current.removeAttribute('src'); audio.current.load(); }
    if (mounted.current) setBusy('');
  }
  async function load(initial = false) {
    try {
      const data = await x(); if (!mounted.current) return;
      setCatalog(data); setSaved(data); setError('');
      if (initial) setDraft({ provider: data.provider, voice: data.voice });
    } catch (e) { if (mounted.current) setError(e.message); }
  }
  l.useEffect(() => { mounted.current = true; load(true); return () => { mounted.current = false; stop(); }; }, []);
  async function save() {
    stop(); setBusy('save'); setError('');
    try {
      const data = await ee({ ...draft, avatarEnabled: saved.avatarEnabled });
      if (!data.ok) throw new Error(data.error || '保存失败');
      if (!mounted.current) return;
      setSaved(data); setCatalog(data); onSaved?.(data); notify('已保存，下一次现场播报使用新音色');
    } catch (e) { if (mounted.current) setError(e.message); }
    finally { if (mounted.current) setBusy(''); }
  }
  async function preview() {
    stop(); const request = new AbortController(); pending.current = request;
    setBusy('preview'); setError('');
    try {
      const res = await fetch(`${d()}/api/tts`, { method: 'POST', signal: request.signal,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...draft, text, preview: true }) });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || '试听失败');
      if (request.signal.aborted || !mounted.current) return;
      audio.current.src = new URL(data.audio.url, d() || location.origin).href;
      await audio.current.play();
    } catch (e) { if (!request.signal.aborted && mounted.current) setError(e.message); }
    finally { if (pending.current === request && mounted.current) { setBusy(''); pending.current = null; } }
  }
  const voices = catalog?.allVoices?.[draft.provider] || {};
  const changed = saved && (draft.voice !== saved.voice || draft.provider !== saved.provider);
  const status = catalog?.localStatus;
  return h('section', { className: 'panel local-voice-settings', style: { padding: 24 } },
    h('div', { className: 'digital-human-section-head' },
      h('div', null, h('h2', null, '语音设置'), h('p', null, '先在当前电脑试听，保存后从下一次现场播报生效。')),
      h('button', { className: 'ghost', onClick: () => load(false) }, '刷新音色与状态')),
    h('p', { role: 'status' }, saved ? `当前已保存：${saved.providerName} · ${saved.voiceName || saved.voice}${changed ? '（有未保存修改）' : ''}` : '读取语音设置中…'),
    h('p', null, `本地语音服务：${status?.online ? status.ready ? status.busy ? '忙碌' : '就绪' : '准备中' : '离线'}`),
    h('div', { style: { display: 'flex', gap: 12, margin: '16px 0' } },
      ...Object.entries(catalog?.providers || {}).map(([provider, label]) => h('button', {
        key: provider, className: draft.provider === provider ? 'primary' : 'ghost', disabled: busy === 'save',
        onClick: () => { stop(); setDraft({ provider, voice: provider === saved?.provider ? saved.voice : Object.keys(catalog.allVoices[provider] || {})[0] || '' }); },
      }, label))),
    draft.provider === 'qwen3' && h('p', { style: { background: '#edf5ff', padding: 12, borderRadius: 8, color: '#244b6b' } },
      '本地音色由展示屏播放，数字人继续显示。当前数字人软件尚不支持本地音频口型同步。'),
    h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: 10, margin: '16px 0' } },
      ...Object.entries(voices).map(([voice, label]) => h('button', { key: voice,
        'aria-pressed': draft.voice === voice, disabled: busy === 'save',
        className: draft.voice === voice ? 'primary' : 'ghost', style: { padding: '14px 12px', textAlign: 'left', height: 'auto', minHeight: 76, whiteSpace: 'normal', lineHeight: 1.5 },
        onClick: () => { stop(); setDraft(v => ({ ...v, voice })); },
      }, h('strong', { style: { display: 'block' } }, label), h('small', null, voice)))),
    h('label', null, '本机试听内容', h('textarea', { value: text, maxLength: 300, onChange: e => setText(e.target.value), style: { width: '100%', minHeight: 66, marginTop: 8 } })),
    h('div', { className: 'digital-human-command-row' },
      h('button', { className: 'ghost', disabled: !!busy || !draft.voice || !text.trim(), onClick: preview }, busy === 'preview' ? '正在生成…' : '在本机试听'),
      h('button', { className: 'ghost', onClick: stop }, '停止试听'),
      h('button', { className: 'primary', disabled: !!busy || !draft.voice || !saved, onClick: save }, busy === 'save' ? '保存中…' : '保存语音设置')),
    h('audio', { ref: audio, controls: true, style: { width: '100%', marginTop: 12 } }),
    error && h('p', { role: 'alert', style: { color: '#bf3030' } }, error));
}
