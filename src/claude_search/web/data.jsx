/* Sample data — fallback shown when the local API isn't reachable.
   Generic synthetic content; no real session data. */

const PROJECTS = ['web-app', 'api-server', 'notes', 'homelab', 'data-pipeline', 'mobile-app'];

const mark = (text, terms) => {
  if (!terms || !terms.length) return [{t: text}];
  const re = new RegExp(`(${terms.map(t => t.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\$&')).join('|')})`, 'gi');
  const parts = text.split(re);
  return parts.map((p, i) => re.test(p) ? {t: p, m: true} : {t: p});
};

const HITS = [
  { id:'h01', ts:'2026-04-29 22:42', proj:'api-server', role:'asst', kind:'assistant_prose',
    snippet:'… The 500 from `/api/secrets/rotate` traces back to a missing transactional swap in `secrets.go:142`. Two issues, in order of severity …',
    sessionId:'sess_01HZQ3KN8R7T9F4VBM2E5WX6P',
    convPath:'~/.claude/projects/api-server/sess_01HZQ3KN8R7T9F4VBM2E5WX6P.jsonl',
    msgCount: 47 },
  { id:'h02', ts:'2026-04-28 00:30', proj:'api-server', role:'asst', kind:'assistant_prose',
    snippet:'Done with the auth refactor — switched the middleware to verify the JWT signature before touching the request body, so malformed tokens fail …' },
  { id:'h03', ts:'2026-04-27 18:34', proj:'api-server', role:'asst', kind:'assistant_prose',
    snippet:'### Issues filed | # | Title | Tier | |---|---|---| | #230 | Postgres backup window collides with peak traffic | P1 | …' },
  { id:'h04', ts:'2026-04-27 02:31', proj:'api-server', role:'asst', kind:'assistant_prose',
    snippet:'All four tasks queued — **Postgres in-cluster vs managed**, **Redis sharding strategy**, **rate-limit budget refresh**, **observability dashboards** …' },
  { id:'h05', ts:'2026-04-26 00:49', proj:'api-server', role:'asst', kind:'assistant_prose',
    snippet:'Merged. PR landed at commit `1ed7cdd` after the CI green light. Cleaning up the held-back worktree.' },
  { id:'h06', ts:'2026-04-22 04:15', proj:'api-server', role:'asst', kind:'assistant_prose',
    snippet:'… The reset-link flow hits `https://example.com/reset?token=<new token>` and lands on the password page after the token verifies …' },
  { id:'h07', ts:'2026-04-15 11:09', proj:'web-app', role:'asst', kind:'assistant_prose',
    snippet:'… BM25 doesn\'t do recency natively. Compose a hybrid: `bm25(content_fts, 1.0, 1.5) - 0.0001 * (now - ts_unix)` …' },
  { id:'h08', ts:'2026-04-15 11:08', proj:'web-app', role:'user', kind:'user_typed',
    snippet:'how do I tune the FTS5 BM25 weights so that recent matches rank higher? old chats keep surfacing first.' },
  { id:'h09', ts:'2026-04-12 17:34', proj:'notes', role:'asst', kind:'assistant_prose',
    snippet:'… RFC 5545 says `BYDAY=MO,TU` interacts with `WKST` in non-obvious ways. The week-numbering anchor shifts, so the rule spans a different 7-day window …' },
  { id:'h10', ts:'2026-04-12 17:33', proj:'notes', role:'user', kind:'user_typed',
    snippet:'RFC 5545 RRULE BYDAY interacts with WKST in weird ways for weekly cadences — what\'s the actual edge case?' },
  { id:'h11', ts:'2026-04-08 09:14', proj:'mobile-app', role:'asst', kind:'assistant_prose',
    snippet:'… WebRTC ICE gathering was timing out behind the symmetric NAT. Added a TURN fallback (`coturn`) at `turn.example.com` and updated the client config …' },
  { id:'h12', ts:'2026-04-04 14:23', proj:'data-pipeline', role:'asst', kind:'assistant_prose',
    snippet:'… Per-tenant token bucket inside a sliding-window per-origin envelope. The bucket protects fairness; the window stops bursts from a single source …' },
  { id:'h13', ts:'2026-04-04 14:22', proj:'data-pipeline', role:'user', kind:'user_typed',
    snippet:'rate limiting strategy for the ingestion API — token bucket per tenant, or sliding window per origin? Both?' },
  { id:'h14', ts:'2026-03-28 22:48', proj:'web-app', role:'asst', kind:'assistant_prose',
    snippet:'… The CDN honors origin Cache-Control but the edge has its own min-TTL of 600s on this route. Override with a `Cache-Control: max-age=60` from the origin …' },
  { id:'h15', ts:'2026-03-15 18:43', proj:'homelab', role:'user', kind:'user_typed',
    snippet:'If I\'m already running an SSH bastion, do I really need a VPN for the homelab services?' },
  { id:'h16', ts:'2026-03-13 03:22', proj:'homelab', role:'asst', kind:'assistant_prose',
    snippet:'Got it — focused on `~/homelab/services/` for the next pass.' },
  { id:'h17', ts:'2026-03-09 16:23', proj:'homelab', role:'user', kind:'user_typed',
    snippet:'Still not seeing the dashboard at https://example.local/dashboards/' },
  { id:'h18', ts:'2026-03-09 04:14', proj:'homelab', role:'user', kind:'user_typed',
    snippet:'https://example.local/grafana/ is not loading' },
  { id:'h19', ts:'2026-03-09 01:30', proj:'homelab', role:'user', kind:'user_typed',
    snippet:'Use WebFetch to check the GitHub API for open pull requests on the repository …' },
  { id:'h20', ts:'2026-03-08 23:39', proj:'api-server', role:'asst', kind:'assistant_prose',
    snippet:'… Here\'s the summary: | PR | Issue | What was done | |----|-------|---------------| | #18 | #1 | Initial scaffolding | | #19 | #2 | Auth middleware …' },
  { id:'h21', ts:'2026-03-08 20:16', proj:'api-server', role:'asst', kind:'assistant_prose',
    snippet:'… `ingress-tls.yaml` — hostname updated from `api.example.com` to `api-ssl.example.com` — `monitoring/loki-values.yaml` …' },
  { id:'h22', ts:'2026-03-08 20:15', proj:'api-server', role:'asst', kind:'assistant_prose',
    snippet:'I need to update the hostname from `api.example.com` to `api-ssl.example.com` so the cert renewal points at the right SAN.' },
];

window.HITS = HITS;
window.PROJECTS = PROJECTS;
window.markText = mark;
