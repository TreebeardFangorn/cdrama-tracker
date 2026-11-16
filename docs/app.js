/* Drama Tracker - static front end. Two fully separate sections (Chinese / Korean), each with its own
   data file (data/cn.json, data/kr.json), its own saved filters and its own personal list. */
(() => {
  'use strict';
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const safeUrl = (u) => (typeof u === 'string' && /^https:\/\//.test(u) ? u : '');
  const fmtN = (n) => (n == null ? '' : n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e4 ? Math.round(n / 1e3) + 'k' : n >= 1e3 ? (n / 1e3).toFixed(1) + 'k' : String(n));
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const todayIso = new Date(today.getTime() - today.getTimezoneOffset() * 60000).toISOString().slice(0, 10);

  let DATA = { dramas: [], people: {} };
  const byId = new Map();
  const store = {
    get(k, d) { try { return JSON.parse(localStorage.getItem('cdt.' + k)) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem('cdt.' + k, JSON.stringify(v)); } catch { /* private mode */ } },
  };
  const REGIONS = {
    cn: { name: 'Cdramas', netLabel: 'Chinese platform', search: 'Search Chinese dramas: title, actor, director, tag…' },
    kr: { name: 'Kdramas', netLabel: 'Korean network', search: 'Search Korean dramas: title, actor, director, tag…' },
  };
  let REGION = 'cn';
  const rk = (k) => `${k}.${REGION}`; // every saved setting is kept per section
  if (store.get('mine', null) && !store.get('mine.cn', null)) store.set('mine.cn', store.get('mine', {})); // carry over the pre-Kdrama list
  if (store.get('ui', null) && !store.get('ui.cn', null)) store.set('ui.cn', store.get('ui', {}));
  let mine = {}; // id -> want | watching | watched | skip
  const DEFAULT_SORT = { upcoming: 'watchers', airing: 'hot', hot: 'hot', released: 'mdl', mine: 'added', all: 'watchers' };
  let state = {};
  function loadState() {
    state = Object.assign({ tab: 'upcoming', sort: {}, year: '', net: '', us: '', few: false, genres: [], q: '', mineS: '' }, store.get(rk('ui'), {}));
    state.q = '';
    if (!document.querySelector(`#tabs [data-tab="${state.tab}"]`)) state.tab = 'all';
    mine = store.get(rk('mine'), {});
  }
  let list = [], shown = 0;
  const PAGE = 60;

  // ---------- formatting
  function dateLabel(d) {
    if (!d.start) return 'Date TBA';
    const [y, m, day] = d.start.split('-').map(Number);
    if (d.sp === 'y') return String(y);
    if (d.sp === 'm') return `${MONTHS[m - 1]} ${y}`;
    return `${MONTHS[m - 1]} ${day}, ${y}`;
  }
  function cardDate(d) {
    if (d.st === 'upcoming') {
      let s = dateLabel(d);
      if (d.start && d.sp === 'd') {
        const days = Math.round((new Date(d.start + 'T00:00') - today) / 864e5);
        if (days >= 0 && days <= 60) s += days === 0 ? ' · today' : days === 1 ? ' · tomorrow' : ` · in ${days} days`;
      }
      return s;
    }
    if (d.st === 'airing') return `Airing since ${dateLabel(d)}`;
    return d.y ? String(d.y) : '';
  }
  const isNew = (d) => d.add > DATA.baseline && (Date.parse(todayIso) - Date.parse(d.add)) / 864e5 <= 14;
  const bigImg = (u) => safeUrl(u).replace(/[cst]\.jpg/, 'f.jpg');

  // ---------- filtering + sorting
  const val = {
    hot: (d) => d.hot,
    watchers: (d) => d.mdl.w ?? (d.mdl.p ? 1 / d.mdl.p : null),
    vikif: (d) => d.viki?.f, imdbn: (d) => d.imdb?.n,
    avg: (d) => d.avg, mdl: (d) => d.mdl.s, viki: (d) => d.viki?.s, imdb: (d) => d.imdb?.s,
    added: (d) => Date.parse(d.add) + (d.mdl.w || 0) / 1e7,
  };
  const enoughVotes = {
    mdl: (d) => (d.mdl.n ?? (d.mdl.r ? 300 : 0)) >= 300,
    viki: (d) => (d.viki?.n || 0) >= 100,
    imdb: (d) => (d.imdb?.n || 0) >= 100,
    avg: (d) => (d.mdl.n ?? (d.mdl.r ? 300 : 0)) >= 300 || (d.imdb?.n || 0) >= 300,
  };
  function hay(d) {
    if (!d._h) {
      const names = [...(d.cast || []), ...(d.dir || []), ...(d.wri || [])].map((k) => DATA.people[k]?.n || '');
      d._h = [d.t, d.nt, ...(d.g || []), ...(d.tags || []), ...(d.net || []), ...names, d.y,
        ...names.map((n) => n.replace(/\s+/g, '')), d.t.replace(/\s+/g, '')].join(' ').toLowerCase(); // also match names typed without spaces ("Zhao Lusi")
    }
    return d._h;
  }
  function inTab(d, tab) {
    const s = mine[d.id];
    if (tab === 'mine') return s && (state.mineS ? s === state.mineS : s !== 'skip');
    if (s === 'skip') return false;
    if (tab === 'hot') return d.hot != null;
    return tab === 'all' || d.st === tab;
  }
  function passes(d, tab) {
    if (state.q) { // a search looks through the whole catalog: no tab, filters or hidden titles
      for (const w of state.q.toLowerCase().split(/\s+/)) if (w && !hay(d).includes(w)) return false;
      return true;
    }
    if (!inTab(d, tab)) return false;
    if (state.year && String(d.y || '') !== state.year) return false;
    if (state.net && !(d.net || []).includes(state.net)) return false;
    if (state.us) {
      const o = d.us || [];
      if (state.us === '*' ? !o.some((x) => x[1] !== 'buy' && x[1] !== 'rent') : state.us === 'free' ? !o.some((x) => x[1] === 'free') : !o.some((x) => x[0] === state.us)) return false;
    }
    for (const g of state.genres) if (!(d.g || []).includes(g)) return false;
    return true;
  }
  function relevance(d, q) { // title matches first, then cast/tag matches; ties go to the bigger audience
    const t = d.t.toLowerCase();
    return t === q ? 0 : t.startsWith(q) ? 1 : t.includes(q) || (d.nt || '').includes(q) ? 2 : 3;
  }
  function compute() {
    const sort = state.sort[state.tab] || DEFAULT_SORT[state.tab];
    let out = DATA.dramas.filter((d) => passes(d, state.tab));
    if (enoughVotes[sort] && !state.few && state.tab !== 'mine' && !state.q) out = out.filter(enoughVotes[sort]);
    if (state.q) {
      const q = state.q.toLowerCase();
      out.sort((a, b) => relevance(a, q) - relevance(b, q) || (b.mdl.w || 0) - (a.mdl.w || 0));
    } else if (sort === 'soon' || sort === 'new') {
      const dir = sort === 'soon' ? 1 : -1;
      out.sort((a, b) => {
        if (!a.start || !b.start) return (a.start ? 0 : 1) - (b.start ? 0 : 1) || (b.mdl.w || 0) - (a.mdl.w || 0);
        const pa = a.sp === 'd' ? 0 : 1, pb = b.sp === 'd' ? 0 : 1; // vague dates (month/year only) after exact ones
        if (sort === 'soon' && pa !== pb) return pa - pb;
        return a.start < b.start ? -dir : a.start > b.start ? dir : (b.mdl.w || 0) - (a.mdl.w || 0);
      });
    } else {
      const f = val[sort] || val.watchers;
      out.sort((a, b) => {
        const x = f(a), y = f(b);
        if (x == null || y == null) return (x == null) - (y == null) || (b.mdl.w || 0) - (a.mdl.w || 0);
        return y - x || (b.mdl.w || 0) - (a.mdl.w || 0);
      });
    }
    return out;
  }

  // ---------- rendering
  function cardHtml(d) {
    const sort = state.sort[state.tab] || DEFAULT_SORT[state.tab];
    const sc = [];
    if (d.mdl.s) sc.push(`<span class="sc mdl" title="MyDramaList score">MDL ${d.mdl.s.toFixed(1)}</span>`);
    if (d.viki?.s) sc.push(`<span class="sc viki" title="Viki score">Viki ${d.viki.s.toFixed(1)}</span>`);
    if (d.imdb?.s) sc.push(`<span class="sc imdb" title="IMDb score">IMDb ${d.imdb.s.toFixed(1)}</span>`);
    if ((!sc.length || sort === 'watchers') && d.mdl.w) sc.unshift(`<span class="sc buzz" title="People tracking this on MyDramaList">👥 ${fmtN(d.mdl.w)}</span>`);
    if (sort === 'hot' && d.hot) sc.unshift(`<span class="sc buzz" title="New people tracking this on MyDramaList per day${d.hs === 'e' ? ' (estimate until a few days of history build up)' : ', measured over the past week'}">🔥 ${d.hs === 'e' ? '~' : '+'}${fmtN(Math.round(d.hot))}/day</span>`);
    if (sort === 'vikif' && d.viki?.f) sc.unshift(`<span class="sc buzz" title="Viki followers">👥 ${fmtN(d.viki.f)}</span>`);
    if (sort === 'imdbn' && d.imdb?.n) sc.unshift(`<span class="sc buzz" title="IMDb votes">🗳 ${fmtN(d.imdb.n)}</span>`);
    const img = safeUrl(d.img);
    const want = mine[d.id];
    const pill = d.st === 'upcoming' ? '<span class="pill upcoming">Upcoming</span>' : d.st === 'airing' ? '<span class="pill airing">Airing</span>' : '';
    return `<article class="card" tabindex="0" data-id="${esc(d.id)}">
      <div class="poster">
        <div class="ph">${esc(d.t)}</div>
        ${img ? `<img loading="lazy" referrerpolicy="no-referrer" alt="" src="${esc(img)}" onerror="this.remove()">` : ''}
        ${pill}${isNew(d) ? '<span class="pill new">New</span>' : ''}
        <button class="heart ${want ? 'on' : ''}" title="${want ? 'On your list' : 'Add to Want to watch'}" aria-label="Toggle want to watch">${want === 'watched' ? '✓' : want === 'watching' ? '▶' : '♥'}</button>
        <div class="scores">${sc.join('')}</div>
      </div>
      <div class="meta">
        <div class="t">${esc(d.t)}</div>
        ${d.nt ? `<div class="nt">${esc(d.nt)}</div>` : ''}
        <div class="d">${esc(cardDate(d))}${d.ep ? ` · ${d.ep} eps` : ''}</div>
        <div class="g">${esc((d.g || []).slice(0, 3).join(' · '))}</div>
        ${d.us ? `<div class="us" title="Where to watch in the US">🇺🇸 ${esc(d.us.filter((o) => o[1] === 'free' || o[1] === 'sub').sort((x, y) => usRank(x[0]) - usRank(y[0])).slice(0, 3).map((o) => o[0].replace(/ \(.*/, '')).join(' · ') || 'Buy / rent')}</div>` : ''}
      </div></article>`;
  }
  function more() {
    if (shown >= list.length) return;
    const chunk = list.slice(shown, shown + PAGE);
    $('#grid').insertAdjacentHTML('beforeend', chunk.map(cardHtml).join(''));
    shown += chunk.length;
  }
  function render() {
    store.set(rk('ui'), state);
    list = compute(); shown = 0;
    $('#grid').innerHTML = '';
    more();
    $('#empty').hidden = list.length > 0;
    $('#empty').textContent = state.tab === 'mine' && !Object.keys(mine).length
      ? 'Your list is empty. Tap the ♥ on any drama to save it here.' : 'Nothing matches these filters.';
    $('#count').textContent = list.length ? `${list.length.toLocaleString()} drama${list.length === 1 ? '' : 's'}${state.q ? ' matching your search, from the whole catalog (tabs, filters and sorting are ignored while searching)' : ''}` : '';
    $('#reset').hidden = !(state.year || state.net || state.us || state.genres.length) || !!state.q;
    $$('.controls, .genres').forEach((el) => { el.style.opacity = state.q ? 0.35 : ''; el.style.pointerEvents = state.q ? 'none' : ''; });
    $$('#tabs button').forEach((b) => {
      b.classList.toggle('on', b.dataset.tab === state.tab);
      $('b', b).textContent = DATA.dramas.filter((d) => inTab(d, b.dataset.tab)).length.toLocaleString();
    });
    $('#sort').value = state.sort[state.tab] || DEFAULT_SORT[state.tab];
    $('#year').value = state.year; $('#net').value = state.net; $('#us').value = state.us || ''; $('#few').checked = state.few;
    $('#mineFilter').hidden = state.tab !== 'mine';
    $$('#mineFilter button').forEach((b) => b.classList.toggle('on', b.dataset.s === state.mineS));
    $$('#genres .chip').forEach((c) => c.classList.toggle('on', state.genres.includes(c.dataset.g)));
  }

  function personHtml(key, cur) {
    const p = DATA.people[key];
    if (!p) return '';
    const works = (p.w || []).filter((id) => id !== cur.id && byId.has(id)).slice(0, 3).map((id) => byId.get(id));
    const link = safeUrl(p.l) || `https://mydramalist.com/search?q=${encodeURIComponent(p.n)}`;
    const img = safeUrl(p.i);
    const initials = p.n.split(/\s+/).map((w) => w[0]).slice(0, 2).join('');
    return `<div class="person">
      ${img ? `<img loading="lazy" referrerpolicy="no-referrer" alt="" src="${esc(img)}" onerror="this.outerHTML='<div class=av>${esc(initials)}</div>'">` : `<div class="av">${esc(initials)}</div>`}
      <div><a class="pn" href="${esc(link)}" target="_blank" rel="noopener noreferrer">${esc(p.n)}</a>
        <div class="kf">${works.length ? 'Also known for (since 2020)' : 'No other tracked dramas since 2020'}</div>
        <ul>${works.map((w) => `<li><button data-open="${esc(w.id)}">${esc(w.t)}</button> <span>${w.st === 'upcoming' ? 'upcoming' : w.y || ''}${w.mdl.s ? ` · ★ ${w.mdl.s.toFixed(1)}` : ''}</span></li>`).join('')}</ul>
      </div></div>`;
  }
  const US_MAIN = ['Netflix', 'Viki', 'WeTV', 'iQIYI', 'Amazon Prime Video', 'Tubi'];
  const usRank = (n) => { const i = US_MAIN.findIndex((m) => n.startsWith(m)); return i < 0 ? 99 : i; };
  function tryLinks(d) { // international apps of the original Chinese platform; availability not verified
    if (REGION !== 'cn') return '';
    const q = encodeURIComponent(d.t), has = (n) => (d.us || []).some((o) => o[0].startsWith(n)), out = [];
    if ((d.net || []).includes('iQiyi') && !has('iQIYI')) out.push(['iQIYI (iq.com)', `https://www.iq.com/search?query=${q}`]);
    if ((d.net || []).includes('Tencent Video') && !has('WeTV')) out.push(['WeTV', `https://wetv.vip/en/search/${q}`]);
    out.push(['YouTube (official channels)', `https://www.youtube.com/results?search_query=${encodeURIComponent(`${d.t} ${d.nt || ''} eng sub ep 1`)}`]);
    return `<p class="muted small try">Not confirmed, but worth a search: ${out.map(([n, u]) => `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(n)}</a>`).join(' · ')}</p>`;
  }
  function scoreBox(label, cls, s, n, nLabel, url) {
    const inner = `<div class="lbl">${label}</div><div class="val" style="color:var(--${cls})">${s ? s.toFixed(1) : '–'}</div><div class="n">${n ? `${n.toLocaleString()} ${nLabel}` : s ? '' : 'no score yet'}</div>`;
    return url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${inner}</a>` : `<div>${inner}</div>`;
  }
  function openModal(id, push = true) {
    const d = byId.get(id);
    if (!d) return;
    const q = encodeURIComponent;
    const yt = `https://www.youtube.com/results?search_query=${q(`${d.t} ${d.nt || ''} ${d.y || ''} trailer`.replace(/\s+/g, ' '))}`;
    const imdbUrl = d.imdb ? `https://www.imdb.com/title/${q(d.imdb.id)}/` : `https://www.imdb.com/find/?s=tt&q=${q(d.t)}`;
    const s = mine[d.id];
    const stLabel = { upcoming: 'Upcoming', airing: 'Airing now', released: 'Released' }[d.st];
    const when = d.start ? (d.end && d.end !== d.start ? `${dateLabel(d)} – ${dateLabel({ start: d.end, sp: 'd' })}` : dateLabel(d)) : 'Release date TBA';
    const sect = (title, keys) => (keys && keys.length ? `<h3>${title}</h3><div class="people">${keys.map((k) => personHtml(k, d)).join('')}</div>` : '');
    const trackBtn = (v, label) => `<button class="btn ${s === v ? 'on' : ''}" data-track="${v}">${label}</button>`;
    $('#modalBody').innerHTML = `<button class="close" aria-label="Close">✕</button>
    <div class="m">
      <div class="left">
        <img referrerpolicy="no-referrer" alt="Poster for ${esc(d.t)}" src="${esc(bigImg(d.img))}" data-fallback="${esc(safeUrl(d.img))}">
        <div class="track">${trackBtn('want', '♥ Want to watch')}${trackBtn('watching', '▶ Watching')}${trackBtn('watched', '✓ Watched')}${trackBtn('skip', '✕ Not interested')}</div>
      </div>
      <div>
        <h2>${esc(d.t)}</h2>
        <div class="sub">${esc(d.nt || '')}</div>
        <div class="facts">
          <span class="st-${d.st}">${stLabel}</span><span>${esc(when)}</span>
          ${d.ep ? `<span>${d.ep} episodes</span>` : ''}${d.dur ? `<span>${esc(d.dur)}</span>` : ''}
          ${(d.net || []).map((n) => `<span>📺 ${esc(n)}</span>`).join('')}
          ${d.cr ? `<span>${esc(d.cr.split(' - ')[0])}</span>` : ''}
          ${(d.g || []).map((g) => `<span>${esc(g)}</span>`).join('')}
        </div>
        <div class="scorebox">
          ${scoreBox('MyDramaList', 'mdl', d.mdl.s, d.mdl.n, 'ratings', d.url)}
          ${scoreBox('Viki', 'viki', d.viki?.s, d.viki?.n, 'ratings', d.viki?.u)}
          ${scoreBox('IMDb', 'imdb', d.imdb?.s, d.imdb?.n, 'votes', d.imdb ? imdbUrl : '')}
          ${scoreBox('Average', 'text', d.avg, null, '', '')}
          <div><div class="lbl">${d.st === 'upcoming' ? 'Anticipation' : 'Audience'}</div><div class="val">${d.mdl.w ? fmtN(d.mdl.w) : '–'}</div>
            <div class="n">${d.mdl.w ? 'tracking on MDL' : ''}${d.mdl.p ? ` · #${d.mdl.p.toLocaleString()} popular` : ''}${d.viki?.f ? `<br>${fmtN(d.viki.f)} Viki followers` : ''}${d.hot ? `<br>🔥 ${d.hs === 'e' ? 'about ' : '+'}${fmtN(Math.round(d.hot))} new per day` : ''}</div></div>
        </div>
        <div class="syn">${esc(d.syn || 'No synopsis yet.')}</div>
        ${d.st === 'upcoming' ? '' : `<h3>Watch in the US</h3>${d.us ? `<div class="links us-links">${d.us.map((o) => `<a class="btn" href="${esc(safeUrl(o[2]))}" target="_blank" rel="noopener noreferrer">${esc(o[0])} <small>${{ free: 'free', sub: 'subscription', rent: 'rent', buy: 'buy' }[o[1]] || ''}</small></a>`).join('')}</div>` : '<p class="muted small">Not found on US streaming services (per JustWatch). It may still be on YouTube or an app JustWatch does not track.</p>'}${tryLinks(d)}<h3>More</h3>`}
        <div class="links">
          <a class="btn yt" href="${esc(yt)}" target="_blank" rel="noopener noreferrer">▶ Trailer on YouTube</a>
          <a class="btn" href="${esc(safeUrl(d.url))}" target="_blank" rel="noopener noreferrer">MyDramaList page</a>
          ${d.viki?.u ? `<a class="btn" href="${esc(safeUrl(d.viki.u))}" target="_blank" rel="noopener noreferrer">${d.viki.tr ? 'Watch / trailers on Viki' : 'Viki page'}</a>` : ''}
          <a class="btn" href="${esc(imdbUrl)}" target="_blank" rel="noopener noreferrer">${d.imdb ? 'IMDb page' : 'Search IMDb'}</a>
          ${d.nt && REGION === 'cn' ? `<a class="btn" href="https://search.douban.com/movie/subject_search?search_text=${q(d.nt)}" target="_blank" rel="noopener noreferrer">Douban 豆瓣</a>` : ''}
        </div>
        ${sect('Main stars', d.cast)}${sect(d.dir && d.dir.length > 1 ? 'Directors' : 'Director', d.dir)}${sect(d.wri && d.wri.length > 1 ? 'Screenwriters' : 'Screenwriter', d.wri)}
        ${!d.cast && !d.dir ? '<p class="muted">Cast and crew have not been announced or loaded yet.</p>' : ''}
        ${d.tags ? `<h3>Tags</h3><div class="tags">${esc(d.tags.join(' · '))}</div>` : ''}
      </div>
    </div>`;
    const big = $('#modalBody .left img');
    big.onerror = () => { big.onerror = null; if (big.dataset.fallback && big.src !== big.dataset.fallback) big.src = big.dataset.fallback; };
    const m = $('#modal');
    m.dataset.id = id;
    if (!m.open) m.showModal();
    m.scrollTop = 0;
    if (push) history.replaceState(null, '', `#${REGION}/d=${id}`);
  }
  function closeModal() {
    const m = $('#modal');
    if (m.open) m.close();
  }
  function setMine(id, v) {
    if (mine[id] === v || !v) delete mine[id]; else mine[id] = v;
    store.set(rk('mine'), mine);
  }

  // ---------- events
  function wire() {
    $('#regions').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b && b.dataset.region !== REGION) loadRegion(b.dataset.region); });
    $('#tabs').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) { state.tab = b.dataset.tab; render(); window.scrollTo({ top: 0 }); } });
    $('#sort').addEventListener('change', (e) => { state.sort[state.tab] = e.target.value; render(); });
    $('#year').addEventListener('change', (e) => { state.year = e.target.value; render(); });
    $('#net').addEventListener('change', (e) => { state.net = e.target.value; render(); });
    $('#us').addEventListener('change', (e) => { state.us = e.target.value; render(); });
    $('#reset').addEventListener('click', () => { state.year = ''; state.net = ''; state.us = ''; state.genres = []; render(); });
    $('#few').addEventListener('change', (e) => { state.few = e.target.checked; render(); });
    let t; $('#q').addEventListener('input', (e) => { clearTimeout(t); t = setTimeout(() => { state.q = e.target.value.trim(); render(); }, 150); });
    $('#genres').addEventListener('click', (e) => {
      const c = e.target.closest('.chip'); if (!c) return;
      const g = c.dataset.g; state.genres = state.genres.includes(g) ? state.genres.filter((x) => x !== g) : [...state.genres, g]; render();
    });
    $('#mineFilter').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) { state.mineS = b.dataset.s; render(); } });
    $('#grid').addEventListener('click', (e) => {
      const card = e.target.closest('.card'); if (!card) return;
      if (e.target.closest('.heart')) {
        const id = card.dataset.id; setMine(id, mine[id] ? null : 'want');
        if (state.tab === 'mine') render(); else { card.outerHTML = cardHtml(byId.get(id)); updateCounts(); }
        return;
      }
      openModal(card.dataset.id);
    });
    $('#grid').addEventListener('keydown', (e) => { if (e.key === 'Enter') { const c = e.target.closest('.card'); if (c) openModal(c.dataset.id); } });
    const m = $('#modal');
    m.addEventListener('click', (e) => {
      if (e.target === m || e.target.closest('.close')) return closeModal();
      const o = e.target.closest('[data-open]'); if (o) return openModal(o.dataset.open);
      const tr = e.target.closest('[data-track]');
      if (tr) { setMine(m.dataset.id, tr.dataset.track); openModal(m.dataset.id); m.dataset.dirty = '1'; }
    });
    m.addEventListener('close', () => {
      history.replaceState(null, '', '#' + REGION);
      if (m.dataset.dirty) { delete m.dataset.dirty; const y = window.scrollY, n = shown; render(); while (shown < n) more(); window.scrollTo({ top: y }); }
    });
    new IntersectionObserver((en) => { if (en[0].isIntersecting) more(); }, { rootMargin: '900px' }).observe($('#sentinel'));
  }
  function updateCounts() {
    $$('#tabs button').forEach((b) => { $('b', b).textContent = DATA.dramas.filter((d) => inTab(d, b.dataset.tab)).length.toLocaleString(); });
  }

  function setupFilters() {
    const count = (arr) => arr.reduce((m, x) => m.set(x, (m.get(x) || 0) + 1), new Map());
    const g = [...count(DATA.dramas.flatMap((d) => d.g || []))].sort((a, b) => b[1] - a[1]).slice(0, 26);
    $('#genres').innerHTML = g.map(([name]) => `<button class="chip" data-g="${esc(name)}">${esc(name)}</button>`).join('');
    state.genres = state.genres.filter((x) => g.some(([n]) => n === x));
    const years = [...new Set(DATA.dramas.map((d) => d.y).filter(Boolean))].sort((a, b) => b - a);
    $('#year').innerHTML = '<option value="">Any</option>';
    $('#year').insertAdjacentHTML('beforeend', years.map((y) => `<option>${y}</option>`).join(''));
    const nets = [...count(DATA.dramas.flatMap((d) => d.net || []))].filter(([, n]) => n >= 8).sort((a, b) => b[1] - a[1]).slice(0, 25);
    $('#net').innerHTML = '<option value="">Any</option>';
    $('#net').insertAdjacentHTML('beforeend', nets.map(([n, c]) => `<option value="${esc(n)}">${esc(n)} (${c})</option>`).join(''));
    if (state.net && !nets.some(([n]) => n === state.net)) state.net = '';
    const us = [...count(DATA.dramas.flatMap((d) => (d.us || []).map((o) => o[0])))].filter(([, n]) => n >= 5).sort((a, b) => b[1] - a[1]).slice(0, 20);
    $$('#us option').slice(3).forEach((o) => o.remove());
    $('#us').insertAdjacentHTML('beforeend', us.map(([n, c]) => `<option value="${esc(n)}">${esc(n)} (${c})</option>`).join(''));
    if (state.us && !['*', 'free'].includes(state.us) && !us.some(([n]) => n === state.us)) state.us = '';
    if (state.year && !years.includes(+state.year)) state.year = '';
  }

  const loaded = {}; // region -> data, so switching back and forth does not re-download
  async function loadRegion(region, openId) {
    REGION = REGIONS[region] ? region : 'cn';
    store.set('region', REGION);
    closeModal();
    history.replaceState(null, '', '#' + REGION);
    loadState();
    $('#q').value = '';
    $('#q').placeholder = REGIONS[REGION].search;
    $('#netLabel').textContent = REGIONS[REGION].netLabel;
    $$('#regions button').forEach((b) => b.classList.toggle('on', b.dataset.region === REGION));
    document.title = `${REGIONS[REGION].name} · Drama Tracker`;
    byId.clear(); DATA = { dramas: [], people: {} };
    $('#grid').innerHTML = ''; $('#count').textContent = ''; $('#updated').textContent = 'Loading…'; $('#empty').hidden = true;
    const want = REGION;
    try {
      if (!loaded[want]) {
        const r = await fetch(`data/${want}.json`, { cache: 'no-cache' });
        if (!r.ok) throw new Error(r.status);
        const j = await r.json();
        if (j.region !== want) throw new Error('wrong section in data file'); // never show one section's data in the other
        loaded[want] = j;
      }
    } catch (e) {
      if (REGION !== want) return;
      $('#updated').textContent = '';
      $('#empty').hidden = false; $('#empty').textContent = `Could not load ${REGIONS[want].name} data. Try refreshing in a minute.`;
      return;
    }
    if (REGION !== want) return; // the other section was chosen while this one was loading
    DATA = loaded[want];
    DATA.dramas.forEach((d) => byId.set(d.id, d));
    const gen = new Date(DATA.generated);
    $('#updated').textContent = `Updated ${gen.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}, ${gen.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })} · ${DATA.dramas.length.toLocaleString()} dramas`;
    setupFilters();
    render();
    if (openId) openModal(openId);
  }
  function init() {
    wire();
    const m = location.hash.match(/^#(cn|kr)?\/?(?:d=(\d+))?/) || [];
    loadRegion(m[1] || store.get('region', 'cn'), m[2]);
  }
  init();
})();
