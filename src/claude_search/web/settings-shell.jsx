/* Settings — shared atoms for both Remote Servers and Service panels.
   Reuses tokens from styles.css; adds settings-specific layout. */

const { useState: useS, useEffect: useE, useMemo: useM, useRef: useR } = React;
const Ic3 = window.Ic;

/* ── Status dot ─────────────────────────── */
const StatusDot = ({ kind = 'green', pulse = false, size = 8 }) => (
  <span className={`sd sd-${kind} ${pulse ? 'sd-pulse' : ''}`}
    style={{width: size, height: size}} />
);

/* ── Generic settings shell with sidebar ── */
const SettingsShell = ({ active, onNav, children }) => {
  const items = [
    { id:'index',     label:'Index',          icon: Ic3.Refresh },
    { id:'remote',    label:'Remote servers', icon: Ic3.External },
    { id:'service',   label:'Service',        icon: Ic3.Term },
    { id:'appear',    label:'Appearance',     icon: Ic3.Settings },
    { id:'keys',      label:'Keybindings',    icon: Ic3.Help },
  ];
  return (
    <div className="settings-shell">
      <aside className="settings-side">
        <div className="settings-side-h">
          <a className="appmark" href="index.html" title="Back to search" style={{textDecoration:'none'}}>cs</a>
          <div className="settings-side-title">
            Settings
            <span className="settings-side-sub">claude-search · v0.3.1</span>
          </div>
        </div>
        <nav className="settings-nav">
          {items.map(it => (
            <button key={it.id}
              className={`settings-nav-item ${active === it.id ? 'active' : ''}`}
              onClick={() => onNav && onNav(it.id)}>
              <it.icon size={13} />
              <span>{it.label}</span>
            </button>
          ))}
        </nav>
        <div className="settings-side-f">
          <span className="settings-side-version">~/.claude-search/</span>
        </div>
      </aside>
      <main className="settings-main">{children}</main>
    </div>
  );
};

/* ── Panel header ───────────────────────── */
const PanelHeader = ({ title, desc, right }) => (
  <header className="panel-h">
    <div>
      <h1 className="panel-title">{title}</h1>
      {desc && <p className="panel-desc">{desc}</p>}
    </div>
    {right && <div className="panel-h-right">{right}</div>}
  </header>
);

/* ── Section header ─────────────────────── */
const Section = ({ label, hint, right, children, collapsed, onToggle, advanced }) => (
  <section className={`panel-section ${advanced ? 'is-adv' : ''}`}>
    <div className="panel-section-h" onClick={onToggle ? () => onToggle() : undefined}
      style={{cursor: onToggle ? 'pointer' : 'default'}}>
      {onToggle && <Ic3.Chev size={11} className="chev"
        style={{transform: collapsed ? 'rotate(0deg)' : 'rotate(90deg)', transition:'transform 0.12s', color:'var(--fg-3)'}} />}
      <span className="panel-section-label">{label}</span>
      {hint && <span className="panel-section-hint">{hint}</span>}
      {right && <span className="panel-section-right">{right}</span>}
    </div>
    {!collapsed && <div className="panel-section-body">{children}</div>}
  </section>
);

/* ── Inline command pill (copyable) ─────── */
const CmdPill = ({ children, onCopy }) => {
  const [c, setC] = useS(false);
  return (
    <button className="cmd-pill" onClick={() => { setC(true); setTimeout(()=>setC(false),1200); onCopy && onCopy(); }}>
      <Ic3.Term size={10} />
      <code>{children}</code>
      {c ? <Ic3.Check size={10} /> : <Ic3.Copy size={10} />}
    </button>
  );
};

/* ── Info banner ───────────────────────── */
const InfoBanner = ({ children, onDismiss }) => (
  <div className="banner">
    <span className="banner-i"><Ic3.Help size={12} /></span>
    <span className="banner-body">{children}</span>
    {onDismiss && <button className="icon-btn" onClick={onDismiss}><Ic3.X size={12} /></button>}
  </div>
);

/* ── Field & toggle ────────────────────── */
const Field = ({ label, hint, children, error, mono }) => (
  <div className="field">
    {label && <label className="field-label">{label}</label>}
    {children}
    {error && <span className="field-err">{error}</span>}
    {hint && !error && <span className={`field-hint ${mono ? 'mono' : ''}`}>{hint}</span>}
  </div>
);

const Toggle = ({ on, onChange, label, dim }) => (
  <button className={`toggle ${on ? 'on' : ''} ${dim ? 'dim' : ''}`}
    onClick={() => onChange && onChange(!on)} role="switch" aria-checked={on}>
    <span className="toggle-knob" />
    {label && <span className="toggle-label">{label}</span>}
  </button>
);

const Radio = ({ value, current, onChange, children, hint }) => (
  <button className={`radio2 ${current === value ? 'active' : ''}`}
    onClick={() => onChange(value)}>
    <span className="radio2-d" />
    <span className="radio2-l">{children}</span>
    {hint && <span className="radio2-h">{hint}</span>}
  </button>
);

window.Settings = { SettingsShell, PanelHeader, Section, StatusDot, CmdPill, InfoBanner, Field, Toggle, Radio };
