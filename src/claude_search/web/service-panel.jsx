/* Service panel — server side.

   Live by default: pulls /v1/status and /v1/auth/clients from the local
   service, drives "Invite a client" through /v1/auth/issue, revoke
   through /v1/auth/revoke/{id}.

   Falls back to canvas-style mock when invoked with a specific
   `scenario` (used by the design preview, not by settings.html).
*/

const { useState: useSP, useEffect: useSPE, useRef: useSPR } = React;
const SS = window.Settings;
const II = window.Ic;

/* Tiny QR using a deterministic 25x25 grid (mock — purely decorative) */
const QrMock = ({ size = 144 }) => {
  const N = 25;
  const cells = [];
  let s = 0xC0FFEE;
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
    s = (s * 1103515245 + 12345) & 0x7fffffff;
    const isFinder = (x < 7 && y < 7) || (x > N-8 && y < 7) || (x < 7 && y > N-8);
    const onFinderEdge =
      ((x < 7 && y < 7) && (x===0||x===6||y===0||y===6 || (x>=2&&x<=4&&y>=2&&y<=4))) ||
      ((x > N-8 && y < 7) && (x===N-7||x===N-1||y===0||y===6 || (x>=N-5&&x<=N-3&&y>=2&&y<=4))) ||
      ((x < 7 && y > N-8) && (x===0||x===6||y===N-7||y===N-1 || (x>=2&&x<=4&&y>=N-5&&y<=N-3)));
    const on = isFinder ? onFinderEdge : (s % 100) > 52;
    if (on) cells.push(<rect key={`${x}-${y}`} x={x} y={y} width="1" height="1" />);
  }
  return (
    <div className="qr-wrap">
      <svg width={size} height={size} viewBox={`0 0 ${N} ${N}`} shapeRendering="crispEdges" fill="black">
        {cells}
      </svg>
    </div>
  );
};

function formatCountdown(expiresAt) {
  if (!expiresAt) return '—';
  const t = Date.parse(expiresAt);
  if (isNaN(t)) return '—';
  const ms = t - Date.now();
  if (ms <= 0) return 'expired';
  const total = Math.floor(ms / 1000);
  const m = String(Math.floor(total / 60)).padStart(2, '0');
  const s = String(total % 60).padStart(2, '0');
  return `${m}:${s}`;
}

function formatRelative(iso) {
  if (!iso) return '—';
  const t = Date.parse(iso);
  if (isNaN(t)) return iso;
  const delta = Math.max(0, Date.now() - t);
  const m = Math.floor(delta / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m} minute${m === 1 ? '' : 's'} ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} hour${h === 1 ? '' : 's'} ago`;
  const d = Math.floor(h / 24);
  return `${d} day${d === 1 ? '' : 's'} ago`;
}

/* Status card */
const StatusCard = ({ running, bind, lan, port, fp, fpOpen, setFpOpen, loopback }) => (
  <div className={`status-card ${running ? '' : 'stopped'}`}>
    <div className="status-dot-wrap">
      <SS.StatusDot kind={running ? 'green' : 'red'} size={12} pulse={running} />
    </div>
    <div className="status-card-body">
      <div className="status-card-title">{running ? `Running on ${lan}:${port}` : 'Service stopped'}</div>
      <div className="status-card-meta">
        <span><span className="key">Listening on:</span> {bind}</span>
        {fp && <>
          <span>·</span>
          <span><span className="key">TLS fingerprint:</span> </span>
          <span className="fp-truncate" onClick={() => setFpOpen(!fpOpen)}>
            {fpOpen ? fp : `${fp.slice(0, 18)}…${fp.slice(-4)}`}
            <II.Copy size={10} />
          </span>
        </>}
      </div>
      {loopback && (
        <div style={{marginTop:8, fontSize:11.5, color:'var(--warning)', display:'flex', alignItems:'center', gap:6}}>
          <II.Alert size={11} />
          This machine isn't reachable from other machines. Restart with --host 0.0.0.0 to expose it on the LAN.
        </div>
      )}
    </div>
  </div>
);

/* Connected clients table */
const ClientRow = ({ c, onRevoke }) => (
  <div className={`clients-tr ${c.kind === 'pending' ? 'pending' : ''} ${c.muted ? 'muted' : ''}`}>
    <span>
      {c.kind === 'pending'
        ? <SS.StatusDot kind="amber" size={8} pulse />
        : c.kind === 'inactive'
          ? <SS.StatusDot kind="grey" size={8} />
          : <SS.StatusDot kind="green" size={8} />}
    </span>
    <span className="label">{c.label}</span>
    <span className="ts">{c.created}</span>
    <span className="ts">{c.lastUsed}</span>
    <span className="ip">{c.ip || '—'}</span>
    <span style={{display:'flex', justifyContent:'flex-end'}}>
      {c.kind === 'pending'
        ? <span className="countdown">{c.countdown}</span>
        : onRevoke
          ? <button className="btn tiny danger" onClick={() => onRevoke(c)}>Revoke</button>
          : <button className="btn tiny danger">Revoke</button>}
    </span>
  </div>
);

/* Network section */
const NetworkSection = ({ open, onToggle, dirty }) => {
  const [bind, setBind] = useSP('all');
  const [showRegen, setShowRegen] = useSP(false);
  return (
    <SS.Section advanced label="Network settings" collapsed={!open} onToggle={onToggle}>
      <div style={{display:'flex', flexDirection:'column', gap:18}}>
        <SS.Field label="Bind address" hint="Which network interfaces accept connections.">
          <SS.Radio value="all" current={bind} onChange={setBind} hint="All network interfaces · most permissive within LAN">All network interfaces</SS.Radio>
          <SS.Radio value="lan" current={bind} onChange={setBind} hint="Bind to the detected LAN IP only">LAN only</SS.Radio>
          <SS.Radio value="loopback" current={bind} onChange={setBind} hint="Only this machine can connect · useful with SSH tunnels">Loopback only</SS.Radio>
          <SS.Radio value="custom" current={bind} onChange={setBind} hint="Specific bind address">Custom…</SS.Radio>
        </SS.Field>
        <SS.Field label="Port" hint="Other machines' Remote Servers panel will connect to this port.">
          <input className="input mono" defaultValue="8765" style={{width:120}} />
        </SS.Field>
        <SS.Field label="Hostname or IP in invites"
          hint="What invite URLs will tell clients to connect to. Auto-detected from the bind address if left blank. Override if you're running behind a Tailscale hostname or custom DNS.">
          <input className="input mono" placeholder="auto-detect" />
        </SS.Field>
        <SS.Field label="TLS certificate"
          hint="No TLS yet — bearer tokens go over plain HTTP. Run over Tailscale, VPN, or SSH tunnel for now.">
          <div className="input-with-action">
            <input className="input mono" readOnly value="(plain HTTP)" />
            <button className="btn" disabled>Copy</button>
            <button className="btn danger" disabled onClick={() => setShowRegen(true)}>Regenerate</button>
          </div>
        </SS.Field>
      </div>
      {dirty && (
        <div className="apply-bar">
          <span className="apply-bar-msg">
            <SS.StatusDot kind="amber" size={8} />
            Network settings need a service restart to apply. (Restart claude-search serve from the terminal.)
          </span>
          <div style={{display:'flex', gap:8}}>
            <button className="btn ghost">Discard</button>
            <button className="btn primary" disabled>Apply (restart manually)</button>
          </div>
        </div>
      )}
      {showRegen && (
        <div className="backdrop" onClick={() => setShowRegen(false)}>
          <div className="modal" style={{width:480}} onClick={(e)=>e.stopPropagation()}>
            <div className="modal-header">Regenerate TLS certificate</div>
            <div className="modal-body" style={{fontSize:12.5, color:'var(--fg-2)', lineHeight:1.6}}>
              No TLS yet. Returns when the UI ships as a desktop app and can pin self-signed fingerprints.
            </div>
            <div className="modal-foot">
              <button className="btn ghost" onClick={() => setShowRegen(false)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </SS.Section>
  );
};

/* Invite modal — issues a real invite via the local API and shows the URL */
const InviteModal = ({ onClose, withQr, onIssued }) => {
  const [showQr, setShowQr] = useSP(!!withQr);
  const [label, setLabel] = useSP('client');
  const [issued, setIssued] = useSP(null);
  const [error, setError] = useSP(null);
  const [busy, setBusy] = useSP(false);
  const [, setTick] = useSP(0);
  const [copied, setCopied] = useSP(false);

  const issue = async (lbl) => {
    setBusy(true); setError(null);
    try {
      const result = await window.csAPI.local.issueInvite({ label: lbl || label || 'client' });
      setIssued(result);
      onIssued && onIssued(result);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  // Initial issue on mount.
  useSPE(() => { issue(label); /* eslint-disable-next-line */ }, []);
  // Tick once a second so the countdown updates.
  useSPE(() => {
    const id = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const url = issued ? issued.invite_url : '';
  const expiresIn = issued ? formatCountdown(issued.expires_at) : '—';
  const expired = expiresIn === 'expired';

  const copy = async () => {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (e) {
      // Clipboard API blocked (insecure context, etc). Fall back to selection.
      setError("Couldn't copy automatically. Select the URL manually.");
    }
  };

  return (
    <div className="backdrop" onClick={onClose}>
      <div className="modal" style={{width:560}} onClick={(e)=>e.stopPropagation()}>
        <div className="modal-header">Invite a client to this machine
          <span className="title-mono">{window.MOD_KEY}I</span>
          <button className="close" onClick={onClose}><II.X size={12} /></button>
        </div>
        <div className="modal-body">
          <SS.Field label="Label" hint="What you'll call the connecting machine in the access list.">
            <div className="input-with-action">
              <input className="input"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="laptop" />
              <button className="btn" disabled={busy} onClick={() => issue(label)}>
                {busy ? 'Issuing…' : 'Re-issue'}
              </button>
            </div>
          </SS.Field>
          <div style={{height:14}} />
          {error && (
            <div style={{fontSize:11.5, color:'var(--danger)', display:'flex', alignItems:'center', gap:6, marginBottom:10}}>
              <II.Alert size={11} /> {error}
            </div>
          )}
          {showQr ? (
            <div style={{display:'flex', gap:18, alignItems:'flex-start'}}>
              <QrMock size={160} />
              <div style={{flex:1}}>
                <div className="invite-url">{url || (busy ? 'issuing…' : '—')}</div>
                <div style={{display:'flex', gap:8, alignItems:'center', marginTop:10}}>
                  <span className="invite-countdown">
                    <II.Clock size={11} /> {expired ? 'expired' : `Expires in ${expiresIn}`}
                  </span>
                  <button className="btn tiny" onClick={() => setShowQr(false)}>Hide QR</button>
                </div>
              </div>
            </div>
          ) : (
            <>
              <div className="invite-url">{url || (busy ? 'issuing…' : '—')}</div>
              <div style={{display:'flex', gap:8, alignItems:'center'}}>
                <button className="btn primary" onClick={copy} disabled={!url || expired}>
                  <II.Copy size={11} /> {copied ? 'Copied' : 'Copy URL'}
                </button>
                <button className="btn" onClick={() => setShowQr(true)} disabled={!url}>Show QR code</button>
                <span style={{flex:1}} />
                <span className="invite-countdown">
                  <II.Clock size={11} /> {expired ? 'expired' : `Expires in ${expiresIn}`}
                </span>
              </div>
            </>
          )}
          <p style={{margin:'12px 0 0', fontSize:11.5, color:'var(--fg-3)'}}>
            Single use. After it's redeemed or expires, it stops working — generate another if needed.
          </p>
        </div>
        <div className="modal-foot">
          <button className="btn primary" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  );
};

/* Revoke confirm */
const RevokeConfirm = ({ onClose, client, onConfirm }) => (
  <div className="backdrop" onClick={onClose}>
    <div className="modal" style={{width:440}} onClick={(e)=>e.stopPropagation()}>
      <div className="modal-header">Revoke '{client?.label || client?.id}'?</div>
      <div className="modal-body" style={{fontSize:12.5, color:'var(--fg-2)', lineHeight:1.6}}>
        It will stop being able to search this machine immediately. Issue a new invite if you want to reconnect it.
      </div>
      <div className="modal-foot">
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn danger" onClick={() => onConfirm(client)}>Revoke</button>
      </div>
    </div>
  </div>
);

/* The whole panel */
const ServicePanel = ({ scenario, onNav }) => {
  const live = scenario === undefined || scenario === 'live';
  const stopped = scenario === 'stopped';
  const loopback = scenario === 'loopback';

  const [fpOpen, setFpOpen] = useSP(false);
  const [networkOpen, setNetworkOpen] = useSP(scenario === 'network' || scenario === 'regenerate');
  const networkDirty = scenario === 'network' || scenario === 'regenerate';
  const [inviteOpen, setInviteOpen] = useSP(scenario === 'invite' || scenario === 'invite-qr');
  const [revokeTarget, setRevokeTarget] = useSP(scenario === 'revoke' ? { id:'c1', label:'laptop' } : null);

  // Live state
  const [serverStatus, setServerStatus] = useSP(null);
  const [serverError, setServerError] = useSP(null);
  const [clients, setClients] = useSP([]);

  const refreshStatus = async () => {
    if (!live) return;
    try {
      const s = await window.csAPI.local.getStatus();
      setServerStatus(s);
      setServerError(null);
    } catch (e) {
      setServerError(e.message);
      setServerStatus(null);
    }
  };

  const refreshClients = async () => {
    if (!live) return;
    try {
      const list = await window.csAPI.local.listClients();
      setClients(list);
    } catch (e) {
      // Loopback-only endpoint; failure here is usually because the
      // browser is hitting a remote IP. Surface in server error.
      setServerError(e.message);
    }
  };

  useSPE(() => {
    if (!live) return;
    refreshStatus();
    refreshClients();
    const id = setInterval(() => { refreshStatus(); refreshClients(); }, 5000);
    return () => clearInterval(id);
  }, [live]);

  const onIssued = () => { refreshClients(); };

  const doRevoke = async (c) => {
    try {
      await window.csAPI.local.revoke(c.id);
      setRevokeTarget(null);
      refreshClients();
    } catch (e) {
      // Surface inline (rare path).
      setRevokeTarget(null);
      setServerError(`Revoke failed: ${e.message}`);
    }
  };

  // Build the rendered clients list
  let clientsView;
  if (live) {
    clientsView = clients.map((t) => {
      const kind = t.kind === 'invite'
        ? 'pending'
        : (t.last_used_at && (Date.now() - Date.parse(t.last_used_at)) < 30 * 24 * 60 * 60 * 1000
            ? 'active'
            : 'inactive');
      return {
        id: t.id,
        label: t.label || t.id,
        created: (t.created_at || '').slice(0, 16).replace('T', ' '),
        lastUsed: t.last_used_at ? formatRelative(t.last_used_at) : (t.kind === 'invite' ? '—' : 'Never'),
        ip: t.last_ip || '—',
        kind,
        muted: kind === 'inactive',
        countdown: t.kind === 'invite' ? formatCountdown(t.expires_at) : undefined,
      };
    });
  } else {
    clientsView = [
      { id:'c1', label:'laptop',     created:'2026-04-30 14:22', lastUsed:'5 minutes ago', ip:'10.0.0.7',   kind:'active' },
      { id:'c2', label:'work-laptop', created:'2026-04-29 09:11', lastUsed:'just now',      ip:'100.84.7.12', kind:'active' },
      { id:'c3', label:'studio-mac',  created:'2026-04-30 16:01', lastUsed:'—',             ip:'—',           kind:'pending', countdown:'14:42' },
      { id:'c4', label:'old-air',     created:'2026-01-18 12:00', lastUsed:'34 days ago',   ip:'192.168.1.5', kind:'inactive', muted:true },
    ];
  }

  // Status card props
  const running = live ? !!serverStatus : !stopped;
  const lanIp = serverStatus
    ? (() => {
        try { return new URL(window.location.href).hostname || '127.0.0.1'; }
        catch { return '127.0.0.1'; }
      })()
    : '10.0.0.4';
  const port = (() => {
    try {
      const u = new URL(window.csAPI.LOCAL_BASE);
      return u.port || '8765';
    } catch { return '8765'; }
  })();
  const bindLabel = loopback
    ? '127.0.0.1 (loopback only)'
    : (serverStatus ? '0.0.0.0 (all interfaces — see serve --host)' : '—');
  const fp = live ? null : 'sha256:8e3f4d2a91c7b5e0d6f8a2b14c9e3f7d52a8b4e1c6d9f3a7b0e4c5d8f2a91b3c1';

  return (
    <SS.SettingsShell active="service" onNav={onNav}>
      <SS.PanelHeader
        title="Service"
        desc="Control how this machine exposes its local index to clients running on other machines you own."
      />

      {live && serverError && (
        <SS.InfoBanner>
          <span style={{color:'var(--warning)'}}>{serverError}</span>{' '}
          (the service may not be running, or the browser can't reach it)
        </SS.InfoBanner>
      )}

      <SS.Section label="Status">
        <StatusCard
          running={running}
          lan={lanIp}
          bind={bindLabel}
          port={port}
          fp={fp}
          fpOpen={fpOpen}
          setFpOpen={setFpOpen}
          loopback={loopback}
        />
      </SS.Section>

      <SS.Section label="Invite a client">
        <button className="invite-cta" onClick={() => setInviteOpen(true)}>
          <span className="invite-cta-icon"><II.External size={18} /></span>
          <div className="invite-cta-body">
            <div className="invite-cta-title">Invite a client</div>
            <div className="invite-cta-sub">Generates a one-line URL you paste into the other machine's Remote Servers panel.</div>
          </div>
          <span className="invite-cta-kbd"><span className="key">{window.MOD_KEY}</span><span className="key">I</span></span>
        </button>
      </SS.Section>

      <SS.Section label={`Connected clients · ${clientsView.filter(c=>c.kind!=='pending').length} active`}>
        <div className="clients-table">
          <div className="clients-th">
            <span>Status</span>
            <span>Label</span>
            <span>Created</span>
            <span>Last used</span>
            <span>Source IP</span>
            <span style={{textAlign:'right'}}>Actions</span>
          </div>
          {clientsView.length === 0 ? (
            <div style={{padding:'18px 14px', color:'var(--fg-3)', fontSize:12.5}}>
              No clients yet. Click "Invite a client" to get started.
            </div>
          ) : (
            clientsView.map(c => (
              <ClientRow key={c.id} c={c}
                onRevoke={live ? (cl) => setRevokeTarget(cl) : undefined} />
            ))
          )}
        </div>
      </SS.Section>

      <NetworkSection open={networkOpen} onToggle={() => setNetworkOpen(!networkOpen)} dirty={networkDirty} />

      {inviteOpen && <InviteModal
        onClose={() => setInviteOpen(false)}
        withQr={scenario === 'invite-qr'}
        onIssued={onIssued} />}
      {revokeTarget && <RevokeConfirm
        client={revokeTarget}
        onClose={() => setRevokeTarget(null)}
        onConfirm={live ? doRevoke : (() => setRevokeTarget(null))} />}
    </SS.SettingsShell>
  );
};

window.ServicePanel = ServicePanel;
