/* No document or credential is sent to GitHub. All API traffic uses loopback. */
(() => {
  'use strict';
  const base = 'http://127.0.0.1:18765';
  const storageKey = 'pdfswitch-connector-session-v1';
  let token = '';
  let connected = false;
  const urls = new Set();
  const el = id => document.getElementById(id);
  const savedCode = new URLSearchParams(location.hash.slice(1)).get('pair');
  if (savedCode) {
    // A pairing code only belongs in the fragment; remove it before any interaction.
    history.replaceState(null, '', location.pathname + location.search);
    if (/^[0-9]{6}$/.test(savedCode)) el('pair-code').value = savedCode;
  }
  try { token = sessionStorage.getItem(storageKey) || ''; } catch {}

  function updateState(value, message) {
    connected = value;
    el('connection-status').textContent = value ? '● 已连接这台电脑' : '○ 尚未连接本机';
    el('connection-status').classList.toggle('connected', value);
    el('connection-card').classList.toggle('connected', value);
    el('connection-title').textContent = value ? '这台电脑，已准备就绪。' : '让网站用上这台电脑的软件。';
    el('connection-message').textContent = message || (value
      ? '文件在当前电脑处理，结果保存在连接器目录。使用完毕后可关闭连接器。'
      : '首次使用请下载并运行连接器，再输入配对码。每台电脑独立处理，无需把文件传到云端。');
    el('connect-button').textContent = value ? '断开连接' : '连接本机 ↗';
    el('submit').disabled = !value;
    el('submit').innerHTML = value ? '开始转换 <span>↗</span>' : '请先连接本机 <span>↗</span>';
  }

  async function request(path, options = {}) {
    if (!/^\/(?:api\/|files\/)/.test(path) || path.includes('..')) throw Error('请求路径无效');
    const headers = new Headers(options.headers);
    if (token) headers.set('Authorization', 'Bearer ' + token);
    const response = await fetch(base + path, {
      ...options, headers, credentials: 'omit', cache: 'no-store',
      redirect: 'error', referrerPolicy: 'no-referrer', targetAddressSpace: 'loopback',
      signal: options.signal || AbortSignal.timeout(120000)
    });
    if (response.status === 401) {
      token = '';
      try { sessionStorage.removeItem(storageKey); } catch {}
      updateState(false, '连接器已重启或配对已失效，请重新连接。');
      window.dispatchEvent(new Event('pdfswitch:disconnected'));
      throw Error('请重新配对本机连接器');
    }
    return response;
  }

  async function check() {
    const response = await request('/api/health', {signal: AbortSignal.timeout(6000)});
    if (!response.ok) throw Error('连接器未响应');
    const result = await response.json();
    if (result.application !== 'PDFSwitch Connector') throw Error('本机端口不是 PDFSwitch 连接器');
    updateState(true);
    window.dispatchEvent(new Event('pdfswitch:connected'));
  }

  function showPairDialog() {
    if (connected) {
      token = '';
      try { sessionStorage.removeItem(storageKey); } catch {}
      updateState(false);
      window.dispatchEvent(new Event('pdfswitch:disconnected'));
      return;
    }
    el('pair-error').textContent = '';
    if (!el('connect-dialog').open) el('connect-dialog').showModal();
    el('pair-code').focus();
  }
  el('connect-button').addEventListener('click', showPairDialog);
  el('connection-status').addEventListener('click', showPairDialog);
  el('close-connect').addEventListener('click', () => el('connect-dialog').close());
  el('pair-form').addEventListener('submit', async event => {
    event.preventDefault();
    const code = el('pair-code').value.trim();
    if (!/^[0-9]{6}$/.test(code)) { el('pair-error').textContent = '请输入六位数字配对码'; return; }
    el('pair-submit').disabled = true;
    el('pair-error').textContent = '正在连接这台电脑…浏览器询问时，请允许本地网络访问。';
    try {
      const response = await request('/api/pair', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code}),
        signal: AbortSignal.timeout(30000)
      });
      const result = await response.json();
      if (!response.ok) throw Error(result.error || '配对失败');
      token = result.token;
      await check();
      try { sessionStorage.setItem(storageKey, token); } catch {}
      el('pair-code').value = '';
      el('connect-dialog').close();
    } catch (error) {
      const network = error instanceof TypeError || error.name === 'TimeoutError';
      el('pair-error').textContent = network
        ? '无法连接。请确认本机连接器正在运行，并允许浏览器访问本机；也可在连接器页面选择“使用本机网页”。'
        : error.message;
    } finally { el('pair-submit').disabled = false; }
  });

  function revokeURLs() { for (const url of urls) URL.revokeObjectURL(url); urls.clear(); }
  async function artifact(path) {
    if (!connected) throw Error('请先连接本机');
    const response = await request(path);
    if (!response.ok) throw Error('无法读取文件，请刷新任务后重试');
    const url = URL.createObjectURL(await response.blob()); urls.add(url); return url;
  }
  async function hydrateImages(root) {
    for (const image of root.querySelectorAll('img[data-artifact]')) {
      try { image.src = await artifact(image.dataset.artifact); }
      catch { image.alt = '预览暂不可用，请重新连接后打开报告'; }
    }
  }
  document.addEventListener('click', async event => {
    const link = event.target.closest('a[data-artifact]');
    if (!link) return;
    event.preventDefault();
    const download = link.getAttribute('download');
    const preview = download === null ? window.open('about:blank', '_blank') : null;
    if (preview) preview.opener = null;
    try {
      const url = await artifact(link.dataset.artifact);
      if (preview) preview.location.replace(url);
      else {
        const anchor = document.createElement('a');
        anchor.href = url; anchor.download = download || link.dataset.artifact.split('/').pop();
        document.body.append(anchor); anchor.click(); anchor.remove();
      }
    } catch (error) { if (preview) preview.close(); el('error').textContent = error.message; }
  });
  window.addEventListener('pdfswitch:disconnected', revokeURLs);
  window.addEventListener('pagehide', revokeURLs);
  window.PDFSwitch = {request, get connected() { return connected; }, hydrateImages, revokeURLs};
  updateState(false);
  // Restoring an existing session is allowed only after the user previously paired.
  // A fresh page never probes localhost or prompts for network permission on its own.
  if (token) check().catch(() => updateState(false, '未连接到本机连接器，请运行连接器后重新连接。'));
  if (savedCode && /^[0-9]{6}$/.test(savedCode)) showPairDialog();
})();
