import fs from 'node:fs'; import path from 'node:path'
const TOKEN = process.env.GH_TOKEN, REPO = process.env.REPO || 'agent-ops', API = 'https://api.github.com'
const H = { Authorization: 'Bearer ' + TOKEN, Accept: 'application/vnd.github+json', 'User-Agent': 'pub', 'Content-Type': 'application/json' }
const api = async (m, u, b) => { const r = await fetch(API + u, { method: m, headers: H, body: b ? JSON.stringify(b) : undefined }); const t = await r.text(); let j = null; try { j = t ? JSON.parse(t) : null } catch {} return { status: r.status, json: j } }
const login = (await api('GET', '/user')).json.login
let r = await api('GET', `/repos/${login}/${REPO}`)
if (r.status === 404) { r = await api('POST', '/user/repos', { name: REPO, private: false, auto_init: true, description: 'agent-ops：给多个 AI 搭班子用的工程纪律层（事件流 / 桥 / 交接 / 判据）' }); console.log('建仓', r.status) }
const files = []; const walk = (d) => { for (const e of fs.readdirSync(d, { withFileTypes: true })) { if (['.git', 'node_modules', '__pycache__', '.ops', '.ops-demo'].includes(e.name) || e.name.startsWith('.')) continue; const p = path.join(d, e.name); e.isDirectory() ? walk(p) : files.push(p) } }
walk('.')
for (const f of files) { const rel = path.relative('.', f).replace(/\\/g, '/'); const content = fs.readFileSync(f).toString('base64'); const cur = await api('GET', `/repos/${login}/${REPO}/contents/${encodeURIComponent(rel)}`); const sha = cur.json?.sha; const put = await api('PUT', `/repos/${login}/${REPO}/contents/${encodeURIComponent(rel)}`, { message: (sha ? 'update ' : 'add ') + rel, content, branch: 'main', ...(sha ? { sha } : {}) }); console.log(put.status < 400 ? '✓ ' + rel : '✗ ' + rel + ' ' + put.status) }
console.log('https://github.com/' + login + '/' + REPO)
