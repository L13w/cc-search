/* claude-search HTTP API client.

   Talks to a claude-search service (local or remote). The factory
   `makeAPI({ baseUrl, token })` returns a per-server API surface so the
   same code paths work for local + every paired remote.

   The local API (no token) is exposed as `window.csAPI.local` for
   convenience.
*/
(() => {
  const url = new URL(window.location.href);
  const LOCAL_BASE =
    url.searchParams.get('api') ||
    `http://${url.hostname || '127.0.0.1'}:8765`;

  // Platform-aware modifier key, picked once per page load. The
  // keyboard handler treats Cmd and Ctrl as equivalent
  // (metaKey || ctrlKey); this is purely a display choice.
  //   Mac:     ⌘  (Command)
  //   Windows: ⊞  (Windows logo, conventionally rendered as squared plus)
  //   Linux:   ⌃  (Control caret — universal modifier symbol)
  const _platform =
    (navigator.userAgentData && navigator.userAgentData.platform) ||
    navigator.platform || '';
  const MOD_KEY = (() => {
    if (/mac/i.test(_platform)) return '⌘';
    if (/win/i.test(_platform)) return '⊞';
    return '⌃';
  })();

  const trimSlash = (s) => (s.endsWith('/') ? s.slice(0, -1) : s);

  // Pull the trailing path component from a path that might use either
  // POSIX `/` or Windows `\` separators. Falls back to '?' if unclear.
  function lastPathComponent(p) {
    if (!p) return '?';
    const parts = String(p).split(/[\/\\]/).filter(Boolean);
    return parts.length ? parts[parts.length - 1] : String(p);
  }

  // Adapter: API hit → component-shaped hit. The panel components were
  // written against the mock-data shape (id, ts, proj, role∈user/asst/tool).
  function adapt(h, source) {
    const cwd = h.project_path || '';
    // Tool kinds belong to the user/assistant role at the API level,
    // but UX-wise they're a third bucket — the badge color and styling
    // distinguish them from prose.
    const isToolKind = h.kind === 'tool_use' || h.kind === 'tool_result' || h.kind === 'image';
    return {
      id: h.message_id,
      sessionId: h.session_id,
      cwd,
      // convPath retained for back-compat; cwd is what the drawer surfaces.
      convPath: cwd
        ? `${cwd} · ${h.session_id}.jsonl`
        : `${h.session_id}.jsonl`,
      ts: (h.timestamp || '').slice(0, 16).replace('T', ' '),
      proj: lastPathComponent(cwd),
      role: isToolKind
        ? 'tool'
        : (h.role === 'assistant' ? 'asst' : h.role === 'user' ? 'user' : 'tool'),
      kind: h.kind,
      snippet: h.snippet,
      score: h.score,
      // Where this hit came from. 'local' or a remote's label.
      source: source || 'local',
    };
  }

  function makeAPI({ baseUrl, token, source, signalFactory }) {
    const base = trimSlash(baseUrl);
    const src = source || 'local';

    async function fetchJSON(path, init = {}) {
      const headers = { ...(init.headers || {}) };
      if (token) headers['authorization'] = `Bearer ${token}`;
      // Per-call signal lets callers cancel in flight (useful for fan-out timeouts).
      const signal = (init.signal !== undefined)
        ? init.signal
        : (signalFactory ? signalFactory() : undefined);
      const res = await fetch(base + path, { ...init, headers, signal });
      if (!res.ok) {
        const text = await res.text().catch(() => '');
        const err = new Error(`HTTP ${res.status} ${res.statusText}: ${text}`);
        err.status = res.status;
        err.body = text;
        throw err;
      }
      // 204 etc.
      const ct = res.headers.get('content-type') || '';
      return ct.includes('application/json') ? res.json() : null;
    }

    return {
      base,
      source: src,

      async getStatus() {
        return fetchJSON('/v1/status');
      },

      async getRecent(limit = 50) {
        const data = await fetchJSON(`/v1/recent?limit=${limit}`);
        return data.map((h) => adapt(h, src));
      },

      async search(q, { limit = 30, kinds = null, projectPaths = null, signal } = {}) {
        const body = { q, limit };
        if (kinds !== null) body.kinds = kinds;
        if (projectPaths !== null && projectPaths !== undefined) body.project_paths = projectPaths;
        const data = await fetchJSON('/v1/search', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(body),
          signal,
        });
        return data.map((h) => adapt(h, src));
      },

      async getProjects() {
        // Returns [{project_path, messages}, ...] sorted by message count desc.
        return fetchJSON('/v1/projects');
      },

      async whoami() {
        return fetchJSON('/v1/auth/whoami');
      },

      async getMessage(messageId) {
        const data = await fetchJSON(`/v1/message/${encodeURIComponent(messageId)}`);
        return adapt(data, src);
      },

      async getSession(sessionId, { limit = 5000 } = {}) {
        const data = await fetchJSON(`/v1/session/${encodeURIComponent(sessionId)}?limit=${limit}`);
        return data.map((h) => adapt(h, src));
      },

      // Operator endpoints (loopback-only on the server side).
      async issueInvite({ label, ttlSeconds }) {
        return fetchJSON('/v1/auth/issue', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ label, ttl_seconds: ttlSeconds }),
        });
      },

      async listClients() {
        return fetchJSON('/v1/auth/clients');
      },

      async revoke(tokenId) {
        return fetchJSON(`/v1/auth/revoke/${encodeURIComponent(tokenId)}`, {
          method: 'POST',
        });
      },

      // Used by paste-invite. Hits the *remote* server's redeem endpoint.
      async redeem({ invite, clientLabel }) {
        return fetchJSON('/v1/auth/redeem', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ invite, client_label: clientLabel || 'client' }),
        });
      },

      async probe() {
        try {
          const s = await this.getStatus();
          return { live: true, status: s };
        } catch (e) {
          return { live: false, error: e.message, status: e.status };
        }
      },
    };
  }

  // Parse a `claude-search://join?…` URL into its parts.
  function parseInviteURL(s) {
    const trimmed = (s || '').trim();
    const prefix = 'claude-search://join?';
    if (!trimmed.startsWith(prefix)) {
      throw new Error("That doesn't look like an invite. It should start with claude-search://join?…");
    }
    const out = {};
    for (const pair of trimmed.slice(prefix.length).split('&')) {
      if (!pair.includes('=')) continue;
      const [k, v] = pair.split('=', 2);
      out[decodeURIComponent(k)] = decodeURIComponent(v);
    }
    for (const required of ['host', 'port', 'invite']) {
      if (!out[required]) {
        throw new Error(`Invite is missing '${required}'.`);
      }
    }
    return out;
  }

  // The local (loopback) server has no token. Callers can also derive
  // remote APIs via makeAPI({...}) directly.
  const local = makeAPI({ baseUrl: LOCAL_BASE, token: null, source: 'local' });

  window.csAPI = {
    LOCAL_BASE,
    makeAPI,
    parseInviteURL,
    local,
    // Back-compat with previous build of the file.
    base: local.base,
    getStatus: local.getStatus.bind(local),
    getRecent: local.getRecent.bind(local),
    search: local.search.bind(local),
    probe: local.probe.bind(local),
    adapt,
  };
  // Top-level shortcuts globals so JSX components can read them
  // without threading a prop down. Picked once per page load.
  window.MOD_KEY = MOD_KEY;
})();
