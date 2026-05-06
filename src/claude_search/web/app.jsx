/* The actual claude-search app — composes searchbar, results list, drawer, modals */

const { useState, useMemo, useEffect, useRef } = React;
const { Search: ICSearch, Settings: ICSettings, Help: ICHelp, X: ICX, Chev: ICChev,
        Copy: ICCopy, External: ICExternal, Folder: ICFolder, Refresh: ICRefresh,
        Filter: ICFilter, Clock: ICClock, Alert: ICAlert, Term: ICTerm, Check: ICCheck } = window.Ic;

/* ── Render a snippet with marks ──
   Supports inline markdown found in real CLI snippets:
   `code`, **bold**, [text](url), bare URLs, and `|` table cells. */
const renderInline = (text, terms, keyPrefix = '') => {
  // Tokenize: code, link, bold, url, plain. Keep simple, non-greedy.
  const tokens = [];
  const re = /(`[^`]+`)|(\[[^\]]+\]\([^)]+\))|(\*\*[^*]+\*\*)|(https?:\/\/\S+)/g;
  let last = 0, m, i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) tokens.push({k: 'text', v: text.slice(last, m.index)});
    if (m[1]) tokens.push({k: 'code', v: m[1].slice(1, -1)});
    else if (m[2]) {
      const lm = /\[([^\]]+)\]\(([^)]+)\)/.exec(m[2]);
      tokens.push({k: 'link', label: lm[1], href: lm[2]});
    }
    else if (m[3]) tokens.push({k: 'bold', v: m[3].slice(2, -2)});
    else if (m[4]) tokens.push({k: 'url', v: m[4]});
    last = m.index + m[0].length;
  }
  if (last < text.length) tokens.push({k: 'text', v: text.slice(last)});

  const renderText = (s, kp) => {
    const parts = window.markText(s, terms);
    return parts.map((p, j) => p.m
      ? <mark key={`${kp}m${j}`}>{p.t}</mark>
      : <span key={`${kp}t${j}`}>{p.t}</span>);
  };

  return tokens.map((tok, idx) => {
    const kp = `${keyPrefix}${idx}`;
    if (tok.k === 'text') return <React.Fragment key={kp}>{renderText(tok.v, kp)}</React.Fragment>;
    if (tok.k === 'code') return <code key={kp}>{renderText(tok.v, kp)}</code>;
    if (tok.k === 'link') return <a key={kp} className="snip-link" href={tok.href}>{renderText(tok.label, kp)}</a>;
    if (tok.k === 'bold') return <strong key={kp}>{renderText(tok.v, kp)}</strong>;
    if (tok.k === 'url') return <a key={kp} className="snip-link" href={tok.v}>{renderText(tok.v, kp)}</a>;
    return null;
  });
};

const Snippet = ({ text, terms }) => <>{renderInline(text, terms)}</>;

const RoleBadge = ({ role }) => (
  <span className={`role-badge ${role === 'user' ? 'user' : role === 'asst' ? 'asst' : 'tool'}`}>
    {role === 'user' ? 'USER' : role === 'asst' ? 'ASST' : 'TOOL'}
  </span>
);

const Kbd = ({ children }) => <span className="kbd">{children}</span>;

/* ── Search bar ── */
const SearchBar = ({ query, setQuery, status, filters, setFilters, projects, projectCounts, onSettings, onHelp, error }) => {
  const inputRef = useRef(null);
  const [focused, setFocused] = useState(true);
  const [projOpen, setProjOpen] = useState(false);
  // `projects` is either a list of cwd strings (live) or undefined.
  // Falls back to the mock label list for the design canvas.
  const allProjects = projects || window.PROJECTS;

  const toggleProject = (p) => {
    const has = filters.projects.includes(p);
    setFilters({
      ...filters,
      projects: has ? filters.projects.filter(x => x !== p) : [...filters.projects, p],
    });
  };

  // cwds can be long; the chip + check rows display the trailing path
  // component while keeping the full cwd in the title attribute.
  const projShortLabel = (p) => {
    if (!p) return '?';
    const parts = String(p).split(/[\/\\]/).filter(Boolean);
    return parts.length ? parts[parts.length - 1] : String(p);
  };

  // Everything before the trailing component, with the original
  // separators preserved so '/home/...' stays POSIX and 'C:\Users\...'
  // stays Windows. Empty for top-level cwds.
  const projParent = (p) => {
    if (!p) return '';
    const s = String(p);
    const idx = Math.max(s.lastIndexOf('/'), s.lastIndexOf('\\'));
    return idx > 0 ? s.slice(0, idx) : '';
  };

  const projCountFor = (p) => {
    if (projectCounts && Object.prototype.hasOwnProperty.call(projectCounts, p)) {
      return projectCounts[p];
    }
    // Fall back to mock data when no live count is provided.
    return (window.HITS || []).filter((h) => h.proj === p).length;
  };

  const projLabel = filters.projects.length === 0
    ? 'All projects'
    : filters.projects.length === 1
      ? `[${projShortLabel(filters.projects[0])}]`
      : `${filters.projects.length} projects`;

  return (
    <div className="searchbar">
      <div className="searchbar-row">
        <div className="appmark">cs</div>
        <div className={`search-input-wrap ${focused ? 'focused' : ''}`}>
          <ICSearch size={14} />
          <input
            ref={inputRef}
            className="search-input"
            value={query}
            placeholder="Search your Claude Code history…"
            onChange={(e) => setQuery(e.target.value)}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
          />
          <span className="search-kbd"><span className="key">{window.MOD_KEY}</span><span className="key">K</span></span>
        </div>
        <span className="status-pill">
          <span className="dot" />
          {status}
        </span>
        <button className="icon-btn" title="Help (?)" onClick={onHelp}><ICHelp size={14} /></button>
        <button className="icon-btn" title={`Settings (${window.MOD_KEY},)`} onClick={onSettings}><ICSettings size={14} /></button>
      </div>
      <div className="chip-row">
        <div className="chip-group">
          <button className={`chip ${filters.kind === 'prose' ? 'active' : ''}`}
            onClick={() => setFilters({ ...filters, kind: 'prose' })}>
            Prose only<span className="chip-hot">{window.MOD_KEY}1</span>
          </button>
          <button className={`chip ${filters.kind === 'all' ? 'active' : ''}`}
            onClick={() => setFilters({ ...filters, kind: 'all' })}>
            Include tools<span className="chip-hot">{window.MOD_KEY}2</span>
          </button>
        </div>
        <div className="chip-divider" />
        <div className="chip-group">
          {[['all','All time',`${window.MOD_KEY}3`],['7d','Last 7d',`${window.MOD_KEY}4`],['30d','Last 30d',`${window.MOD_KEY}5`],['90d','Last 90d',null]].map(([v, l, n]) => (
            <button key={v} className={`chip ${filters.time === v ? 'active' : ''}`}
              onClick={() => setFilters({ ...filters, time: v })}>
              {l}{n && <span className="chip-hot">{n}</span>}
            </button>
          ))}
        </div>
        <div className="chip-divider" />
        <div className="proj-wrap">
          <button className={`chip ${filters.projects.length ? 'active' : ''}`}
            onClick={() => setProjOpen(o => !o)}>
            <ICFilter size={11} />
            {projLabel}
            {filters.projects.length > 0 && <span className="chip-hot">{filters.projects.length}</span>}
            <ICChev size={11} style={{transform: projOpen ? 'rotate(-90deg)' : 'rotate(90deg)', opacity: 0.6, transition: 'transform 0.12s'}} />
          </button>
          {projOpen && (
            <div className="proj-pop" onMouseLeave={() => setProjOpen(false)}>
              <div className="proj-pop-h">
                <span>Projects</span>
                <button className="proj-clear"
                  onClick={() => setFilters({ ...filters, projects: [] })}>
                  Clear
                </button>
              </div>
              <div className="proj-pop-list">
                {allProjects.length === 0 && (
                  <div style={{padding:'10px 12px', color:'var(--fg-3)', fontSize:11.5}}>
                    (no indexed projects yet)
                  </div>
                )}
                {allProjects.map(p => {
                  const checked = filters.projects.includes(p);
                  const short = projShortLabel(p);
                  const parent = projParent(p);
                  const count = projCountFor(p);
                  return (
                    <button key={p} className="proj-item" onClick={() => toggleProject(p)} title={p}>
                      <span className={`proj-check ${checked ? 'on' : ''}`}>
                        {checked && <ICCheck size={10} />}
                      </span>
                      <span className="proj-name">[{short}]</span>
                      <span className="proj-parent">{parent}</span>
                      <span className="proj-count">{count.toLocaleString()}</span>
                    </button>
                  );
                })}
              </div>
              <div className="proj-pop-f">
                <span><span className="key">↑↓</span> nav</span>
                <span><span className="key">␣</span> toggle</span>
                <span><span className="key">esc</span> close</span>
              </div>
            </div>
          )}
        </div>
        {error && (
          <span className="chip-error">
            <ICAlert size={12} /> {error}
          </span>
        )}
      </div>
    </div>
  );
};

/* ── A single result row ── */
const ResultRow = ({ hit, terms, selected, onClick }) => (
  <div className={`result-row ${selected ? 'selected' : ''}`} onClick={onClick}>
    <span className="r-time">{hit.ts}</span>
    <span className="r-project">[{hit.proj}]</span>
    <RoleBadge role={hit.role} />
    <span className="r-snippet"><Snippet text={hit.snippet} terms={terms} /></span>
    {selected && <span className="r-side"><Kbd>↵</Kbd></span>}
  </div>
);

/* ── Results list ── */
const ResultsList = ({ hits, terms, selectedId, onSelect, sectionLabel }) => (
  <div className="results-pane">
    {sectionLabel && (
      <div className="results-section-h">
        <ICClock size={11} />{sectionLabel}
        <span className="count">{hits.length} shown</span>
      </div>
    )}
    {hits.map((h) => (
      <ResultRow key={h.id} hit={h} terms={terms}
        selected={h.id === selectedId} onClick={() => onSelect(h.id)} />
    ))}
  </div>
);

/* ── No results ── */
const NoResults = ({ query }) => (
  <div className="results-pane" style={{display:'flex'}}>
    <div className="empty">
      <div className="title">No results for <code style={{fontFamily:'var(--font-mono)', background:'var(--bg-3)', padding:'1px 6px', borderRadius:2, color:'var(--fg-1)'}}>{query}</code></div>
      <div className="hint">
        FTS5 syntax: <code>"exact phrase"</code> · <code>term*</code> · <code>a AND b</code> · <code>NOT c</code>
      </div>
    </div>
  </div>
);

/* ── Footer ── */
const Footer = ({ extra }) => (
  <div className="footer">
    <span className="hint"><span className="key">↑</span><span className="key">↓</span> navigate</span>
    <span className="hint"><span className="key">↵</span> open</span>
    <span className="hint"><span className="key">esc</span> close</span>
    <span className="hint"><span className="key">{window.MOD_KEY}C</span> copy match</span>
    <span className="spacer" />
    {extra}
    <span className="hint"><span className="key">?</span> shortcuts</span>
  </div>
);

window.SearchApp = { SearchBar, ResultsList, NoResults, Footer, RoleBadge, Snippet, Kbd, ResultRow };
