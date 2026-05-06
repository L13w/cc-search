/* Drawer + modals (help, settings) */

const { useState: useStateD, useEffect: useEffectD, useRef: useRefD } = React;
const Ic2 = window.Ic;

/* ── Conversation drawer ──
   Loads the full session via /v1/session/{id} and renders every
   message in order. The hit's message gets the .matched style and
   is scrolled to the top third of the drawer body so the surrounding
   conversation is visible above and below.
*/
const Drawer = ({ hit, terms, onClose }) => {
  const [copied, setCopied] = useStateD(false);
  const [pathCopied, setPathCopied] = useStateD(false);
  const [messages, setMessages] = useStateD([]);
  const [loading, setLoading] = useStateD(false);
  const [error, setError] = useStateD(null);
  const bodyRef = useRefD(null);
  const matchedRef = useRefD(null);

  const sessionId = hit.sessionId || '(no session id)';
  const cwd = hit.cwd || hit.proj || '(unknown)';
  const sourceLabel = hit.source && hit.source !== 'local' ? hit.source : null;
  const role = hit.role || 'user';

  // Fetch the full session for this hit. Routes to local or the right
  // paired remote based on hit.source.
  useEffectD(() => {
    if (!hit || !hit.sessionId || !window.csAPI || !window.csAPI.local) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setMessages([]);
    let api = window.csAPI.local;
    if (hit.source && hit.source !== 'local' && window.csRemotes) {
      const r = (window.csRemotes.list() || []).find((x) => x.label === hit.source);
      if (r) api = window.csAPI.makeAPI({ baseUrl: r.url, token: r.token, source: r.label });
    }
    api.getSession(hit.sessionId)
      .then((msgs) => { if (!cancelled) setMessages(msgs); })
      .catch((e) => { if (!cancelled) setError(e.message || String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [hit && hit.sessionId, hit && hit.source]);

  // Once the messages render, scroll the matched one into the top
  // third of the drawer body so the user sees what came before plus
  // a chunk of what came after. requestAnimationFrame defers the
  // scroll until layout has settled.
  useEffectD(() => {
    if (!bodyRef.current || !matchedRef.current || messages.length === 0) return;
    const id = requestAnimationFrame(() => {
      if (!bodyRef.current || !matchedRef.current) return;
      const target = matchedRef.current.offsetTop - bodyRef.current.clientHeight / 3;
      bodyRef.current.scrollTop = Math.max(0, target);
    });
    return () => cancelAnimationFrame(id);
  }, [messages, hit && hit.id]);

  const copyText = (text, setter) => {
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).catch(() => {});
    }
    setter(true);
    setTimeout(() => setter(false), 1500);
  };

  return (
    <div className="drawer">
      <div className="drawer-resize" />
      <div className="drawer-header">
        <div className="drawer-title-row">
          <button className="drawer-session" onClick={() => copyText(sessionId, setCopied)} title="Copy session ID">
            <Ic2.Copy size={11} />
            {sessionId}
            {copied && <span className="copied">Copied</span>}
          </button>
          <div className="drawer-actions">
            {sourceLabel && <span className="source-tag" style={{marginRight:8}}>{sourceLabel}</span>}
            <button className="icon-btn" onClick={onClose} title="Close (Esc)">
              <Ic2.X size={14} />
            </button>
          </div>
        </div>
        <div className="drawer-meta">
          <button className="drawer-session"
            style={{padding:0, margin:0, fontSize:11}}
            onClick={() => copyText(cwd, setPathCopied)}
            title="Copy working directory">
            <Ic2.Folder size={11} /> {cwd}
            {pathCopied && <span className="copied">Copied</span>}
          </button>
          {hit.ts && <>
            <span className="sep">·</span>
            <span>{hit.ts}</span>
          </>}
          {messages.length > 0 && <>
            <span className="sep">·</span>
            <span>{messages.length.toLocaleString()} messages</span>
          </>}
        </div>
      </div>

      <div className="drawer-body" ref={bodyRef}>
        {loading && (
          <div style={{color:'var(--fg-3)', fontSize:11.5, padding:'12px 0'}}>
            loading session…
          </div>
        )}
        {error && (
          <div style={{color:'var(--danger)', fontSize:11.5, padding:'12px 0'}}>
            {error}
          </div>
        )}
        {!loading && !error && messages.length === 0 && (
          // Mock-data path or session not found — fall back to just the
          // hit's snippet so the drawer shows *something* useful.
          <div className="msg matched">
            <div className="msg-head">
              <window.SearchApp.RoleBadge role={role} />
              <span className="ts">{hit.ts || ''}</span>
            </div>
            <div className="msg-body">
              <window.SearchApp.Snippet text={hit.snippet || ''} terms={terms || []} />
            </div>
          </div>
        )}
        {messages.map((m) => {
          const isMatched = m.id === hit.id;
          const isAuxKind = m.kind && m.kind !== 'user_typed' && m.kind !== 'assistant_prose';
          return (
            <div key={m.id}
              ref={isMatched ? matchedRef : null}
              className={`msg ${isMatched ? 'matched' : ''}`}>
              <div className="msg-head">
                <window.SearchApp.RoleBadge role={m.role} />
                <span className="ts">{m.ts}</span>
                {isAuxKind && (
                  <span style={{color:'var(--fg-4)', fontSize:10.5}}>{m.kind}</span>
                )}
                {isMatched && (
                  <span style={{color:'var(--accent-fg)', fontWeight:500, marginLeft:'auto'}}>match</span>
                )}
              </div>
              <div className="msg-body">
                <window.SearchApp.Snippet text={m.snippet} terms={terms || []} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

const Snippet2 = ({ text, terms }) => {
  const parts = window.markText(text, terms);
  return <>{parts.map((p, i) => p.m ? <mark key={i}>{p.t}</mark> : <span key={i}>{p.t}</span>)}</>;
};

/* ── Help overlay ── */
const HelpOverlay = ({ onClose }) => (
  <div className="backdrop" onClick={onClose}>
    <div className="modal help-modal" onClick={(e) => e.stopPropagation()}>
      <div className="modal-header">
        Keyboard shortcuts
        <span className="title-mono">claude-search · v0.3.1</span>
        <button className="close" onClick={onClose}><Ic2.X size={12} /></button>
      </div>
      <div className="help-grid">
        <div className="help-section-h">Navigation</div>
        {[
          [[window.MOD_KEY,'K'], 'Focus search input from anywhere'],
          [['↑'], 'Move selection up'],
          [['↓'], 'Move selection down'],
          [['↵'], 'Open conversation drawer'],
          [['Esc'], 'Close drawer · clear query'],
        ].map(([keys, desc], i) => (
          <div className="help-row" key={i}>
            <span className="help-keys">{keys.map((k, j) => <Kbd2 key={j}>{k}</Kbd2>)}</span>
            <span className="help-desc">{desc}</span>
          </div>
        ))}
        <div className="help-section-h">Actions</div>
        {[
          [[window.MOD_KEY,'C'], 'Copy matched message text'],
          [[window.MOD_KEY,','], 'Open settings'],
          [[window.MOD_KEY,'R'], 'Trigger reindex'],
          [['?'], 'Show this overlay'],
        ].map(([keys, desc], i) => (
          <div className="help-row" key={i}>
            <span className="help-keys">{keys.map((k, j) => <Kbd2 key={j}>{k}</Kbd2>)}</span>
            <span className="help-desc">{desc}</span>
          </div>
        ))}
        <div className="help-section-h">Filters</div>
        {[
          [[window.MOD_KEY,'1'], 'Toggle "Prose only"'],
          [[window.MOD_KEY,'2'], 'Toggle "Include tools"'],
          [[window.MOD_KEY,'3'], 'Set time filter to "All time"'],
          [[window.MOD_KEY,'4'], 'Set time filter to "Last 7d"'],
          [[window.MOD_KEY,'5'], 'Set time filter to "Last 30d"'],
        ].map(([keys, desc], i) => (
          <div className="help-row" key={i}>
            <span className="help-keys">{keys.map((k, j) => <Kbd2 key={j}>{k}</Kbd2>)}</span>
            <span className="help-desc">{desc}</span>
          </div>
        ))}
      </div>
    </div>
  </div>
);
const Kbd2 = ({ children }) => <span className="kbd">{children}</span>;

/* ── Settings modal ── */
const SettingsModal = ({ onClose, reindexing }) => (
  <div className="backdrop" onClick={onClose}>
    <div className="modal settings-modal" onClick={(e) => e.stopPropagation()}>
      <div className="modal-header">
        Settings
        <span className="title-mono">{window.MOD_KEY},</span>
        <button className="close" onClick={onClose}><Ic2.X size={12} /></button>
      </div>
      <div className="settings-body">
        <div className="setting-row">
          <span className="setting-label">Index database</span>
          <div className="setting-control">
            <span className="path-display">~/.claude-search/index.sqlite</span>
            <button className="btn"><Ic2.External size={12} /> Reveal</button>
          </div>
          <span className="setting-hint">SQLite FTS5 index · 84.2 MB · 12,841 messages</span>
        </div>

        <div className="setting-row">
          <span className="setting-label">Projects directory</span>
          <div className="setting-control">
            <span className="path-display">~/.claude/projects/</span>
            <button className="btn"><Ic2.Folder size={12} /> Choose…</button>
          </div>
          <span className="setting-hint">Source of JSONL session files. Watched for changes.</span>
        </div>

        <div className="setting-row">
          <span className="setting-label">Theme</span>
          <div className="setting-control">
            <div className="radio-row">
              <span className="radio active"><span className="dot" /> Dark</span>
              <span className="radio disabled"><span className="dot" /> Light · coming soon</span>
            </div>
          </div>
        </div>

        <div className="setting-row">
          <span className="setting-label">Index</span>
          <div className="setting-control">
            {reindexing ? (
              <>
                <div className="progress" style={{flex:1}}><span /></div>
                <span className="setting-hint" style={{whiteSpace:'nowrap'}}>8,212 / 12,841 · 1.4s</span>
                <button className="btn">Cancel reindex</button>
              </>
            ) : (
              <>
                <span className="setting-hint" style={{flex:1}}>Last reindex: 4 hours ago · auto-watch on</span>
                <button className="btn primary"><Ic2.Refresh size={12} /> Reindex now</button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  </div>
);

window.AppParts = { Drawer, HelpOverlay, SettingsModal };
