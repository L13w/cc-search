/* Remote Servers panel — client side.

   Live by default: reads paired remotes from window.csRemotes (localStorage),
   polls each one's /v1/status, and routes paste-invite through the remote's
   /v1/auth/redeem before persisting the bearer.

   The pre-baked scenarios from the design canvas are still here so the
   bundle can render its 14-state preview, but settings.html invokes this
   with no scenario and gets the live experience.
*/

const { useState: useRS, useEffect: useRSE, useRef: useRSR } = React;
const S = window.Settings;
const I = window.Ic;

const POLL_INTERVAL_MS = 30000;

function formatRelative(iso) {
  if (!iso) return '—';
  const t = Date.parse(iso);
  if (isNaN(t)) return iso;
  const delta = Math.max(0, Date.now() - t);
  const m = Math.floor(delta / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/* Server card */
const ServerCard = ({ srv, onToggle, onTest, onRemove, onRePair }) => {
  const statusKind = srv.state === 'connecting' ? 'spinner'
    : srv.state === 'reachable' ? 'green'
    : srv.state === 'slow' ? 'amber'
    : srv.state === 'unreachable' ? 'red'
    : srv.state === 'revoked' ? 'red'
    : srv.state === 'disabled' ? 'grey'
    : srv.state === 'error' ? 'red'
    : 'grey';
  const cardCls = `srv-card ${srv.state === 'connecting' ? 'connecting' : ''} ${['unreachable','error','revoked'].includes(srv.state) ? 'error' : ''} ${srv.state === 'disabled' ? 'disabled' : ''}`;

  return (
    <div className={cardCls}>
      <span className="srv-grip" title="Drag to reorder">
        <svg width="10" height="14" viewBox="0 0 10 14" fill="currentColor"><circle cx="2" cy="2" r="1"/><circle cx="2" cy="7" r="1"/><circle cx="2" cy="12" r="1"/><circle cx="8" cy="2" r="1"/><circle cx="8" cy="7" r="1"/><circle cx="8" cy="12" r="1"/></svg>
      </span>
      <span className="srv-status">
        {statusKind === 'spinner' ? <span className="srv-spinner" /> : <S.StatusDot kind={statusKind} size={9} />}
      </span>
      <div className="srv-body">
        <div className="srv-row1">
          <span className="srv-label">{srv.label}</span>
          <span className="srv-url">{srv.url}</span>
        </div>
        <div className="srv-meta">
          {srv.state === 'connecting' && <span>Connecting…</span>}
          {srv.state === 'reachable' && <>
            <span>{(srv.messages || 0).toLocaleString()} messages</span><span className="sep">·</span>
            <span>last sync {srv.lastSync || '—'}</span><span className="sep">·</span>
            <span>v{srv.version || '?'}</span><span className="sep">·</span>
            <span>{srv.latency || 0}ms</span>
          </>}
          {srv.state === 'slow' && <>
            <span>{(srv.messages || 0).toLocaleString()} messages</span><span className="sep">·</span>
            <span style={{color:'var(--warning)'}}>last query {srv.latency}ms · slow</span>
          </>}
          {srv.state === 'unreachable' && <>
            <span className="err-msg">Couldn't reach {srv.url.replace(/^https?:\/\//,'')}. Is the server running and on the same network?</span>
          </>}
          {srv.state === 'revoked' && <>
            <span className="err-msg">{srv.label} rejected the credentials. The token may have been revoked.</span>
          </>}
          {srv.state === 'error' && <span className="err-msg">{srv.error}</span>}
          {srv.state === 'disabled' && <span>Disabled · click toggle to enable</span>}
        </div>
      </div>
      <div className="srv-actions">
        {srv.state === 'unreachable' && <button className="btn tiny" onClick={onTest}>Try again</button>}
        {srv.state === 'revoked' && <>
          <button className="btn tiny" onClick={onRePair}>Re-pair</button>
          <button className="btn tiny danger" onClick={onRemove}>Remove</button>
        </>}
        {(srv.state === 'reachable' || srv.state === 'slow' || srv.state === 'disabled') && <>
          <button className="btn tiny" onClick={onTest}>Test</button>
          {onToggle && <S.Toggle on={srv.state !== 'disabled'} onChange={onToggle} />}
          <button className="icon-btn danger" title="Remove" onClick={onRemove}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-2 14a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>
          </button>
        </>}
      </div>
    </div>
  );
};

/* Manual setup form (still scaffolding — wire when needed) */
const ManualSetup = ({ onCancel }) => (
  <div className="manual-form">
    <div className="manual-form-h">
      <I.Settings size={11} /> Manual setup
    </div>
    <S.Field label="Label" hint="Required, must be unique among configured servers.">
      <input className="input" placeholder="dev-vm" />
    </S.Field>
    <S.Field label="URL" hint="https://hostname:port — validates on blur.">
      <input className="input mono" placeholder="https://10.0.0.4:8765" />
    </S.Field>
    <S.Field label="Bearer token" hint="Stored in your browser's localStorage. Coming from a desktop client, it'd live in the OS keychain.">
      <div className="input-with-action">
        <input className="input mono" type="password" placeholder="••••••••••••" />
        <button className="btn">Paste token</button>
      </div>
    </S.Field>
    <div style={{display:'flex', justifyContent:'flex-end', gap:8, marginTop:4}}>
      <button className="btn ghost" onClick={onCancel}>Cancel</button>
      <button className="btn primary" disabled>Add server</button>
    </div>
  </div>
);

/* Search behavior section */
const SearchBehavior = ({ timeout, setTimeout: setTO, onFailure, setOnFailure, showLabels, setShowLabels }) => (
  <S.Section label="Search behavior">
    <div style={{display:'flex', flexDirection:'column', gap:18}}>
      <S.Field label={`Per-server query timeout · ${timeout}ms`}
        hint="Servers slower than this drop out of the result set for that query.">
        <div style={{display:'flex', alignItems:'center', gap:12}}>
          <input type="range" min="200" max="5000" step="100" value={timeout}
            onChange={(e) => setTO(Number(e.target.value))}
            style={{flex:1, accentColor:'var(--accent)'}} />
          <span style={{fontFamily:'var(--font-mono)', fontSize:11, color:'var(--fg-3)', width:60, textAlign:'right'}}>{timeout}ms</span>
        </div>
      </S.Field>
      <S.Field label="On failure">
        <S.Radio value="partial" current={onFailure} onChange={setOnFailure}
          hint="Surface what came back; banner above results lists who didn't respond.">
          Show partial results
        </S.Radio>
        <S.Radio value="hide" current={onFailure} onChange={setOnFailure}
          hint="Wait or fail — never show incomplete data.">
          Hide results until all servers respond
        </S.Radio>
      </S.Field>
      <div style={{display:'flex', alignItems:'center', justifyContent:'space-between'}}>
        <div>
          <div className="field-label">Show server label on each result</div>
          <div className="field-hint">When off, results are blended without attribution.</div>
        </div>
        <S.Toggle on={showLabels} onChange={setShowLabels} />
      </div>
    </div>
  </S.Section>
);

/* The whole panel.
   When called with no scenario, runs in live mode against the real
   csRemotes store. When called with a specific scenario, falls back to
   the canvas-style mock (used by the design preview, not by settings.html).
*/
const RemoteServersPanel = ({ scenario, onNav }) => {
  const live = scenario === undefined || scenario === 'live';

  const [showBanner, setShowBanner] = useRS(true);
  const [manualOpen, setManualOpen] = useRS(scenario === 'manual');
  const [pasteVal, setPasteVal] = useRS('');
  const [pasteErr, setPasteErr] = useRS(scenario === 'paste-invalid' ? "That doesn't look like an invite. Run claude-search auth invite --copy on the other machine and paste the result." : null);
  const [pasteBusy, setPasteBusy] = useRS(false);
  const [timeout_, setTOut] = useRS(1500);
  const [onFailure, setOnFailure] = useRS('partial');
  const [showLabels, setShowLabels] = useRS(true);

  // Live state
  const [remotes, setRemotes] = useRS(live ? (window.csRemotes ? window.csRemotes.list() : []) : []);
  const [statuses, setStatuses] = useRS({});
  const pollRef = useRSR(null);

  // Subscribe to store changes (paste flow → store update → re-render)
  useRSE(() => {
    if (!live) return;
    const refresh = () => setRemotes(window.csRemotes.list());
    refresh();
    window.addEventListener('cs-remotes-changed', refresh);
    return () => window.removeEventListener('cs-remotes-changed', refresh);
  }, [live]);

  // Poll each remote's /v1/status
  useRSE(() => {
    if (!live || remotes.length === 0) return;
    let cancelled = false;
    const probeOne = async (r) => {
      if (!r.enabled) {
        setStatuses((s) => ({ ...s, [r.id]: { state: 'disabled' } }));
        return;
      }
      const api = window.csAPI.makeAPI({ baseUrl: r.url, token: r.token, source: r.label });
      const t0 = performance.now();
      try {
        const status = await api.getStatus();
        const latency = Math.round(performance.now() - t0);
        if (cancelled) return;
        setStatuses((s) => ({
          ...s,
          [r.id]: {
            state: latency > 2000 ? 'slow' : 'reachable',
            messages: status.messages,
            lastSync: formatRelative(status.last_indexed_at),
            version: status.version,
            latency,
          },
        }));
      } catch (e) {
        if (cancelled) return;
        const state = e.status === 401 ? 'revoked' : 'unreachable';
        setStatuses((s) => ({ ...s, [r.id]: { state, error: e.body || e.message } }));
      }
    };
    const probeAll = () => { for (const r of remotes) probeOne(r); };
    probeAll();
    pollRef.current = setInterval(probeAll, POLL_INTERVAL_MS);
    return () => { cancelled = true; if (pollRef.current) clearInterval(pollRef.current); };
  }, [live, remotes]);

  // Paste-invite handler
  const handlePaste = async (text) => {
    setPasteErr(null);
    let parsed;
    try {
      parsed = window.csAPI.parseInviteURL(text);
    } catch (e) {
      setPasteErr(e.message);
      return;
    }
    setPasteBusy(true);
    const baseUrl = `http://${parsed.host}:${parsed.port}`;
    try {
      const remoteApi = window.csAPI.makeAPI({ baseUrl, token: null });
      const myLabel = window.location.hostname || 'browser';
      const result = await remoteApi.redeem({ invite: parsed.invite, clientLabel: myLabel });
      // Persist
      try {
        window.csRemotes.add({
          label: result.server_label || 'remote',
          url: baseUrl,
          token: result.token,
        });
      } catch (e) {
        if (e.code === 'duplicate') {
          window.csRemotes.replaceToken(e.existing.id, { label: result.server_label, token: result.token });
        } else throw e;
      }
      setPasteVal('');
    } catch (e) {
      if (e.status === 404) setPasteErr("This invite was already used or isn't recognised. Generate a new one.");
      else if (e.status === 410) setPasteErr("This invite expired. Run claude-search auth invite --copy on the other machine to get a new one.");
      else setPasteErr(`Couldn't reach the server. ${e.message}`);
    } finally {
      setPasteBusy(false);
    }
  };

  const onPasteChange = (e) => {
    const v = e.target.value;
    setPasteVal(v);
    setPasteErr(null);
    if (v.includes('claude-search://join?')) {
      // Strip whitespace and paste-junk; auto-trigger.
      const trimmed = v.trim().replace(/^["']|["']$/g, '');
      handlePaste(trimmed);
    }
  };

  const testRemote = async (id) => {
    const r = (window.csRemotes.list() || []).find((x) => x.id === id);
    if (!r) return;
    setStatuses((s) => ({ ...s, [id]: { state: 'connecting' } }));
    const api = window.csAPI.makeAPI({ baseUrl: r.url, token: r.token, source: r.label });
    const t0 = performance.now();
    try {
      const status = await api.getStatus();
      const latency = Math.round(performance.now() - t0);
      setStatuses((s) => ({
        ...s,
        [id]: {
          state: latency > 2000 ? 'slow' : 'reachable',
          messages: status.messages,
          lastSync: formatRelative(status.last_indexed_at),
          version: status.version,
          latency,
        },
      }));
    } catch (e) {
      const state = e.status === 401 ? 'revoked' : 'unreachable';
      setStatuses((s) => ({ ...s, [id]: { state, error: e.body || e.message } }));
    }
  };

  const removeRemote = (id) => {
    if (!confirm('Remove this server from the list?')) return;
    window.csRemotes.remove(id);
  };

  const toggleEnabled = (id, on) => {
    window.csRemotes.update(id, { enabled: on });
    if (!on) setStatuses((s) => ({ ...s, [id]: { state: 'disabled' } }));
  };

  // Build the rendered server list
  let servers;
  if (live) {
    servers = remotes.map((r) => ({
      id: r.id,
      label: r.label,
      url: r.url,
      ...(statuses[r.id] || { state: 'connecting' }),
    }));
  } else {
    // Mock scenarios for the design canvas
    servers = scenario === 'empty' ? []
      : scenario === 'connecting' ? [
          { id:'s1', label:'dev-vm', url:'https://10.0.0.4:8765', state:'reachable',
            messages:48903, lastSync:'2 min ago', version:'0.3.1', latency:42 },
          { id:'sc', label:'work-laptop', url:'https://10.0.0.7:8765', state:'connecting' },
        ]
      : scenario === 'revoked' ? [
          { id:'s1', label:'dev-vm', url:'https://10.0.0.4:8765', state:'reachable',
            messages:48903, lastSync:'2 min ago', version:'0.3.1', latency:42 },
          { id:'s2', label:'laptop', url:'https://100.84.7.12:8765', state:'revoked' },
        ]
      : [
          { id:'s1', label:'dev-vm', url:'https://10.0.0.4:8765', state:'reachable',
            messages:48903, lastSync:'2 min ago', version:'0.3.1', latency:42 },
          { id:'s2', label:'laptop', url:'https://100.84.7.12:8765', state:'slow',
            messages:21044, lastSync:'just now', version:'0.3.1', latency:2340 },
          { id:'s3', label:'home-nas', url:'https://192.168.1.50:8765', state:'unreachable' },
        ];
  }

  return (
    <S.SettingsShell active="remote" onNav={onNav}>
      <S.PanelHeader
        title="Remote servers"
        desc="Search across claude-search instances running on your other machines."
        right={<button className="btn tiny ghost" onClick={() => {
          for (const r of remotes) testRemote(r.id);
        }}><I.Refresh size={11} /> Refresh all</button>}
      />

      {showBanner && (
        <S.InfoBanner onDismiss={() => setShowBanner(false)}>
          This machine's local index is always searched. Add a server here to also search your other machines.{' '}
          <a href="#">How to start the service on another machine →</a>
        </S.InfoBanner>
      )}

      {/* Hero paste field */}
      <div className="paste-hero">
        <div className="paste-hero-h">
          <span className="paste-hero-label">Paste invite</span>
          <span className="invite-cta-kbd"><span className="key">{window.MOD_KEY}</span><span className="key">V</span></span>
        </div>
        <p className="paste-hero-sub">
          Run <S.CmdPill>claude-search auth invite --copy</S.CmdPill> on the other machine, then paste the result here.
        </p>
        <textarea
          className={`paste-hero-input ${pasteErr ? 'error' : ''}`}
          rows={1}
          placeholder="claude-search://join?host=…&port=…&invite=…"
          value={pasteVal}
          onChange={onPasteChange}
          disabled={pasteBusy}
        />
        {pasteErr && (
          <div style={{marginTop:8, fontSize:11.5, color:'var(--danger)', display:'flex', alignItems:'center', gap:6, flexWrap:'wrap'}}>
            <I.Alert size={11} />
            {pasteErr}
          </div>
        )}
        {pasteBusy && (
          <div style={{marginTop:8, fontSize:11.5, color:'var(--fg-3)', display:'flex', alignItems:'center', gap:8}}>
            <span className="srv-spinner" /> Connecting and redeeming invite…
          </div>
        )}
        <div className="paste-hero-foot">
          <button className="paste-hero-disclosure" onClick={() => setManualOpen((o) => !o)}>
            <I.Chev size={11} style={{transform: manualOpen ? 'rotate(90deg)' : 'rotate(0deg)', transition:'transform 0.12s'}} />
            Set up without an invite
          </button>
        </div>
        {manualOpen && <ManualSetup onCancel={() => setManualOpen(false)} />}
      </div>

      <S.Section label={`Servers · ${servers.length}`}
        hint={servers.length ? 'Drag to reorder · order is a tiebreaker only' : null}>
        {servers.length === 0 ? (
          <div className="empty-state">
            <h3>No remote servers yet</h3>
            <p>You can still search this machine. Paste an invite above to fan out to your other machines.</p>
          </div>
        ) : (
          servers.map((srv) => (
            <ServerCard
              key={srv.id}
              srv={srv}
              onTest={() => live && testRemote(srv.id)}
              onRemove={() => live && removeRemote(srv.id)}
              onRePair={() => live && removeRemote(srv.id)}
              onToggle={live ? (on) => toggleEnabled(srv.id, on) : undefined}
            />
          ))
        )}
      </S.Section>

      <SearchBehavior
        timeout={timeout_} setTimeout={setTOut}
        onFailure={onFailure} setOnFailure={setOnFailure}
        showLabels={showLabels} setShowLabels={setShowLabels}
      />
    </S.SettingsShell>
  );
};

window.RemoteServersPanel = RemoteServersPanel;
