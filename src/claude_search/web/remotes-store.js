/* Persistent store of paired remote servers.

   Lives in localStorage as JSON. One record per remote:
     { id, label, url, token, addedAt, enabled }

   The id is generated client-side. The token is the long-lived bearer
   returned by the remote's /v1/auth/redeem.

   IMPORTANT: localStorage is plaintext storage from a security
   perspective. For a packaged desktop client we'd swap this for an OS
   keychain. In the browser-prototype context, localStorage is the
   pragmatic choice.
*/
(() => {
  const KEY = 'claude-search.remotes.v1';

  function read() {
    try {
      const raw = window.localStorage.getItem(KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
      return [];
    }
  }

  function write(list) {
    window.localStorage.setItem(KEY, JSON.stringify(list));
    // Notify other components in the same tab. (storage events only
    // fire across tabs.)
    window.dispatchEvent(new CustomEvent('cs-remotes-changed'));
  }

  function newId() {
    return 'r_' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
  }

  function findByHostPort(host, port) {
    const url = `https://${host}:${port}`;
    const httpUrl = `http://${host}:${port}`;
    return list().find((r) => r.url === url || r.url === httpUrl);
  }

  function list() {
    return read();
  }

  function add({ label, url, token }) {
    const list = read();
    const dup = list.find((r) => r.url === url);
    if (dup) {
      throw Object.assign(new Error('A server with that URL is already configured.'), {
        code: 'duplicate',
        existing: dup,
      });
    }
    const record = {
      id: newId(),
      label: label || 'remote',
      url,
      token,
      addedAt: new Date().toISOString(),
      enabled: true,
    };
    list.push(record);
    write(list);
    return record;
  }

  function replaceToken(id, { label, token }) {
    const list = read();
    const idx = list.findIndex((r) => r.id === id);
    if (idx < 0) return null;
    list[idx] = { ...list[idx], token, label: label || list[idx].label, updatedAt: new Date().toISOString() };
    write(list);
    return list[idx];
  }

  function update(id, patch) {
    const list = read();
    const idx = list.findIndex((r) => r.id === id);
    if (idx < 0) return null;
    list[idx] = { ...list[idx], ...patch };
    write(list);
    return list[idx];
  }

  function remove(id) {
    const list = read().filter((r) => r.id !== id);
    write(list);
  }

  function clear() {
    write([]);
  }

  window.csRemotes = { list, add, replaceToken, update, remove, findByHostPort, clear };
})();
