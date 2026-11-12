// 콘텐츠 캘린더 저장 API (Vercel 서버리스 함수)
// GET  /api/calendar  → {items, sha}
// PUT  /api/calendar  {items, sha} → {sha}   (sha가 다르면 409: 다른 사람이 먼저 저장함)
// 인증: 헤더 x-dashboard-password 가 대시보드 비밀번호와 같아야 함
// 저장 위치: GitHub 비공개 저장소의 content/calendar.json (GitHub Contents API)
const crypto = require('crypto');

const REPO = process.env.CALENDAR_REPO || 'chelseadoer-bot/fedit_mkt';
const PATH = 'content/calendar.json';
const GH = `https://api.github.com/repos/${REPO}/contents/${PATH}`;

function same(a, b) {
  const x = Buffer.from(String(a || '')), y = Buffer.from(String(b || ''));
  return x.length === y.length && crypto.timingSafeEqual(x, y);
}

async function gh(method, body) {
  const r = await fetch(GH + (method === 'GET' ? '?ref=main' : ''), {
    method,
    headers: {
      Authorization: `Bearer ${process.env.CALENDAR_GH_TOKEN}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'creative-dashboard-calendar',
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  let json = null;
  try { json = JSON.parse(text); } catch (e) {}
  return { status: r.status, json, text };
}

function clean(items) {
  const CH = ['instagram', 'threads', 'ad', 'other'];
  const ST = ['기획', '제작 중', '예약', '게시 완료', '보류'];
  if (!Array.isArray(items) || items.length > 2000) throw new Error('items 형식 오류');
  return items.map(it => ({
    id: String(it.id || crypto.randomUUID()).slice(0, 64),
    date: /^\d{4}-\d{2}-\d{2}$/.test(it.date) ? it.date : (() => { throw new Error('date 형식 오류'); })(),
    channel: CH.includes(it.channel) ? it.channel : 'other',
    title: String(it.title || '').slice(0, 200),
    status: ST.includes(it.status) ? it.status : '기획',
    link: String(it.link || '').slice(0, 500),
    memo: String(it.memo || '').slice(0, 3000),
    updated_at: new Date().toISOString(),
  }));
}

module.exports = async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  if (!process.env.DASHBOARD_PASSWORD || !process.env.CALENDAR_GH_TOKEN) {
    return res.status(503).json({ error: 'not_configured' });
  }
  if (!same(req.headers['x-dashboard-password'], process.env.DASHBOARD_PASSWORD)) {
    return res.status(401).json({ error: 'unauthorized' });
  }
  try {
    if (req.method === 'GET') {
      const r = await gh('GET');
      if (r.status === 404) return res.status(200).json({ items: [], sha: null });
      if (r.status !== 200) return res.status(502).json({ error: 'github', status: r.status });
      const data = JSON.parse(Buffer.from(r.json.content, 'base64').toString('utf8'));
      return res.status(200).json({ items: data.items || [], sha: r.json.sha });
    }
    if (req.method === 'PUT') {
      const body = typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {});
      const items = clean(body.items);
      const content = Buffer.from(JSON.stringify({ items }, null, 2) + '\n').toString('base64');
      const r = await gh('PUT', {
        message: `콘텐츠 캘린더 수정 (${items.length}개 항목)`,
        content, branch: 'main', ...(body.sha ? { sha: body.sha } : {}),
      });
      if (r.status === 409 || r.status === 422) return res.status(409).json({ error: 'conflict' });
      if (r.status !== 200 && r.status !== 201) return res.status(502).json({ error: 'github', status: r.status });
      return res.status(200).json({ sha: r.json.content.sha, items });
    }
    res.setHeader('Allow', 'GET, PUT');
    return res.status(405).json({ error: 'method' });
  } catch (e) {
    return res.status(400).json({ error: String(e.message || e).slice(0, 200) });
  }
};
