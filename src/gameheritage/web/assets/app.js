/* Digital Heritage in Video Games: a static explorer of real places and their
   versions in video games. Data: data/index.json and data/places/<id>.json. */
"use strict";

const PACKAGE_URL = "https://github.com/cantayus/gameheritage";
const S = { index: null, places: {}, games: {}, bySite: {}, byGame: {}, cache: {} };
const main = document.getElementById("main");
const FEATURED = [
  "notre-dame-de-paris", "pantheon", "colosseum", "hagia-sophia", "taj-mahal", "tower-bridge",
  "st-pauls-cathedral", "eiffel-tower", "the-great-sphinx", "great-pyramid-of-giza",
  "machu-picchu", "chichen-itza", "himeji-castle", "forbidden-city", "stonehenge", "alhambra",
  "angkor-wat", "parthenon", "westminster-abbey", "sainte-chapelle", "palace-of-westminster",
  "big-ben", "brandenburg-gate", "independence-hall", "kremlin", "sistine-chapel",
  "great-wall-of-china", "louvre-museum", "arc-de-triomphe", "golden-gate-bridge",
  "statue-of-liberty", "empire-state-building", "washington-monument", "florence",
]
const PAGE = 48;

/* ---------- helpers ---------------------------------------------------- */
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => Number(n).toLocaleString("en-US");
const thumb = (id) => `img/thumb/${encodeURIComponent(id)}.webp`;
const full = (id) => `img/full/${encodeURIComponent(id)}.webp`;
/* The viewer shows full-resolution originals when site.json names a server for them
   (the project's NAS); if that server cannot be reached it falls back to the web copy. */
function big(im, useOriginal = true) {
  const web = full(im.id);
  if (!useOriginal || !S.originals || !im.o) return `src="${web}"`;
  return `src="${esc(S.originals + encodeURIComponent(im.o))}" onerror="this.onerror=null;this.src='${web}'"`;
}
function originalLink(im) {
  return S.originals && im.o ? ` ${link(S.originals + encodeURIComponent(im.o), "Full-resolution file")}.` : "";
}
const norm = (s) => String(s || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
const plural = (n, one, many) => `${fmt(n)} ${n === 1 ? one : many}`;
function link(url, text) {
  return url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(text)}</a>` : esc(text);
}
function gameLine(g) {
  if (!g) return "";
  const who = [g.developer, g.publisher].filter(Boolean).filter((v, i, a) => a.indexOf(v) === i).join(" and ");
  return [g.year, who].filter(Boolean).join(", ");
}
function setTitle(t) { document.title = t ? `${t} | Digital Heritage in Video Games` : "Digital Heritage in Video Games"; }
function navCurrent(name) {
  document.querySelectorAll(".topnav a").forEach((a) => a.toggleAttribute("aria-current", a.dataset.nav === name));
  document.querySelectorAll(".topnav a[aria-current]").forEach((a) => a.setAttribute("aria-current", "page"));
}
async function getJSON(url) {
  if (S.cache[url]) return S.cache[url];
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return (S.cache[url] = await r.json());
}
function params() {
  const q = location.hash.split("?")[1] || "";
  return Object.fromEntries(new URLSearchParams(q));
}

/* ---------- data ------------------------------------------------------- */
async function load() {
  try {
    const conf = await getJSON("site.json");
    const base = (conf.originals_base || "").trim();
    S.originals = base ? (base.endsWith("/") ? base : base + "/") : "";
  } catch { S.originals = ""; }
  const idx = await getJSON("data/index.json");
  S.index = idx;
  idx.places.forEach((p) => { S.places[p.id] = p; S.bySite[p.id] = []; });
  idx.games.forEach((g) => { S.games[g.id] = g; S.byGame[g.id] = []; });
  idx.depictions.forEach(([site, game, name, img, ok]) => {
    const d = { site, game, name, img, ok: !!ok };
    S.bySite[site]?.push(d);
    S.byGame[game]?.push(d);
  });
  S.search = [
    ...idx.places.map((p) => ({ kind: "place", id: p.id, key: norm(`${p.name} ${p.locality} ${p.country}`), p })),
    ...idx.games.map((g) => ({ kind: "game", id: g.id, key: norm(`${g.title} ${g.series}`), g })),
  ];
  // In-game names that differ from the real name also find the place.
  idx.depictions.forEach(([site, , name]) => S.search.push({ kind: "alias", id: site, key: norm(name) }));
}

function find(q, limit = 8) {
  const terms = norm(q).split(/\s+/).filter(Boolean);
  if (!terms.length) return { places: [], games: [] };
  const score = (key) => {
    let s = 0;
    for (const t of terms) {
      const i = key.indexOf(t);
      if (i < 0) return 0;
      s += i === 0 ? 3 : key[i - 1] === " " ? 2 : 1;
    }
    return s;
  };
  const places = new Map(), games = [];
  for (const e of S.search) {
    const s = score(e.key);
    if (!s) continue;
    if (e.kind === "game") games.push([s, e.g]);
    else {
      const p = S.places[e.id];
      const prev = places.get(e.id) || 0;
      places.set(e.id, Math.max(prev, s + (e.kind === "place" ? 0.5 : 0) + Math.min(S.bySite[e.id].length, 5) * 0.1));
      if (!p) places.delete(e.id);
    }
  }
  return {
    places: [...places.entries()].sort((a, b) => b[1] - a[1]).slice(0, limit).map(([id]) => S.places[id]),
    games: games.sort((a, b) => b[0] - a[0]).slice(0, limit).map(([, g]) => g),
  };
}

/* ---------- shared pieces --------------------------------------------- */
function diptych(realId, gameId, alt) {
  const r = realId ? `<img src="${thumb(realId)}" alt="Photograph of ${esc(alt)}" loading="lazy" width="240" height="180">` : `<span class="blank">no photograph yet</span>`;
  const g = gameId ? `<img src="${thumb(gameId)}" alt="${esc(alt)} in a video game" loading="lazy" width="240" height="180">` : `<span class="blank"></span>`;
  return `<span class="diptych">${r}<span class="sep" aria-hidden="true"></span>${g}</span>`;
}
function placeTile(p, gameImg) {
  const deps = S.bySite[p.id] || [];
  const img = gameImg || deps[0]?.img;
  const nGames = new Set(deps.map((d) => d.game)).size;
  const when = p.century || (p.era === "Natural formations" ? "Natural formation" : "");
  const where = [p.locality, p.country].filter(Boolean).filter((v, i, a) => a.indexOf(v) === i).join(", ");
  return `<a class="tile" href="#/place/${encodeURIComponent(p.id)}">
    ${diptych(p.real, img, p.name)}
    <h3>${esc(p.name)}</h3>
    <p>${esc(where)}${when ? `<br><span class="when">${esc(when)}</span>` : ""}</p>
    <p class="gcount">${plural(nGames, "game", "games")}</p>
  </a>`;
}
function searchBox(id, placeholder) {
  return `<div class="search" role="search">
    <label for="${id}">Search a place, a city or a game</label>
    <input id="${id}" type="search" autocomplete="off" spellcheck="false" placeholder="${esc(placeholder)}">
    <div class="results" id="${id}-results" aria-live="polite"></div>
  </div>`;
}
function wireSearch(id) {
  const input = document.getElementById(id), out = document.getElementById(`${id}-results`);
  const render = () => {
    const q = input.value.trim();
    if (q.length < 2) { out.innerHTML = ""; return; }
    const { places, games } = find(q);
    if (!places.length && !games.length) {
      out.innerHTML = `<p class="none">Nothing matches “${esc(q)}”. Try a shorter name, a city or a game series.</p>`;
      return;
    }
    out.innerHTML = `
      <div><h2>Places</h2><ul>${places.map((p) => `<li><a href="#/place/${encodeURIComponent(p.id)}">
        ${p.real ? `<img src="${thumb(p.real)}" alt="" loading="lazy">` : `<span class="noimg"></span>`}
        <span><span class="rn">${esc(p.name)}</span><br><span class="rs">${esc([p.country, p.century].filter(Boolean).join(", "))}</span></span></a></li>`).join("") || `<li class="none">No places</li>`}</ul></div>
      <div class="game"><h2>Games</h2><ul>${games.map((g) => `<li><a href="#/game/${encodeURIComponent(g.id)}">
        <span><span class="rn">${esc(g.title)}</span><br><span class="rs">${esc(gameLine(g))}, ${plural(new Set(S.byGame[g.id].map((d) => d.site)).size, "place", "places")}</span></span></a></li>`).join("") || `<li class="none">No games</li>`}</ul></div>`;
  };
  input.addEventListener("input", render);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { const a = out.querySelector("a"); if (a) location.hash = a.getAttribute("href"); }
  });
}
function initCompare(root) {
  const range = root.querySelector('input[type="range"]');
  if (!range) return;
  const set = (v) => root.style.setProperty("--cut", `${v}%`);
  range.addEventListener("input", () => set(range.value));
  set(range.value);
}
function compareMarkup(realImg, gameImg, realLabel, gameLabel, extraClass = "", useOriginal = true) {
  return `<div class="compare ${extraClass}" style="--cut:50%">
    <img ${big(realImg, useOriginal)} alt="Photograph: ${esc(realLabel)}" width="${realImg.w}" height="${realImg.h}">
    <img class="over" ${big(gameImg, useOriginal)} alt="In the game: ${esc(gameLabel)}" width="${gameImg.w}" height="${gameImg.h}">
    <input type="range" min="0" max="100" value="50" aria-label="Move the divide between the photograph and the game image">
    <span class="seam" aria-hidden="true"></span>
    <span class="corner left tag-real">Real world</span>
    <span class="corner right tag-game">${esc(gameLabel)}</span>
  </div>`;
}

/* ---------- home ------------------------------------------------------- */
async function home() {
  setTitle(""); navCurrent("");
  const idx = S.index;
  const nGameImgs = idx.depictions.length;
  const candidates = FEATURED.filter((id) => S.places[id]?.real && S.bySite[id]?.length);
  const pool = candidates.length ? candidates : idx.places.filter((p) => p.real).sort((a, b) => S.bySite[b.id].length - S.bySite[a.id].length).slice(0, 20).map((p) => p.id);
  const pick = pool[Math.floor(Math.random() * pool.length)];

  const eraCounts = Object.fromEntries(idx.eras.map((e) => [e, 0]));
  idx.places.forEach((p) => { eraCounts[p.era] = (eraCounts[p.era] || 0) + 1; });
  const maxEra = Math.max(...Object.values(eraCounts));
  const notable = pool.slice(0, 12).map((id) => S.places[id]);

  main.innerHTML = `
    <section class="hero stage" aria-label="Featured comparison"><div id="hero-stage" class="stage-inner"><div class="compare"></div></div></section>
    <section class="intro">
      <p class="lede">Games rebuild the world’s heritage. This collection sets each rebuilt place beside the real one, so you can see what was kept, what was changed and what was imagined.</p>
      <div>
        <p class="why">Millions of people first meet a cathedral, a temple or a battlefield inside a game. These virtual copies have become part of how humanity remembers its shared past, and they deserve the same care as any other record of it. Every image and text here is cited to its source.</p>
        <div class="counts">
          <div class="r"><b>${fmt(idx.places.length)}</b><span>real places</span></div>
          <div class="g"><b>${fmt(idx.games.length)}</b><span>games</span></div>
          <div><b>${fmt(nGameImgs)}</b><span>in-game versions</span></div>
        </div>
      </div>
    </section>
    ${searchBox("q", `Try “Notre-Dame”, “Kyoto” or “Assassin’s Creed Unity”`)}
    <section class="timeline wrap" aria-labelledby="tl-h">
      <h2 id="tl-h">Through the ages</h2>
      <p class="tl-sub">Places by the period they were built in. Choose a period to browse it.</p>
      <ol class="eras">
        ${idx.eras.map((e) => `<li><a href="#/places?era=${encodeURIComponent(e)}" class="era ${e === "Natural formations" ? "nat" : ""} ${e === "Date not recorded" ? "und" : ""}">
          <span class="bar"><span style="height:${Math.max(4, Math.round((100 * eraCounts[e]) / maxEra))}%"></span></span>
          <span class="en">${esc(e)}</span><span class="ec">${plural(eraCounts[e], "place", "places")}</span></a></li>`).join("")}
      </ol>
    </section>
    <section class="browse" aria-labelledby="nb-h">
      <div class="browse-head"><h2 id="nb-h">Landmarks to start with</h2><a href="#/places">All ${fmt(idx.places.length)} places</a></div>
      <div class="grid">${notable.map((p) => placeTile(p)).join("")}</div>
    </section>`;
  wireSearch("q");
  renderHero(pick, pool);
}

async function renderHero(id, pool) {
  const box = document.getElementById("hero-stage");
  if (!box) return;
  let place;
  try { place = await getJSON(`data/places/${encodeURIComponent(id)}.json`); } catch { box.innerHTML = ""; return; }
  const dep = place.depictions[Math.floor(Math.random() * place.depictions.length)];
  const g = S.games[dep.game];
  const real = place.real[0], gimg = dep.images[0];
  if (!real || !gimg) { box.innerHTML = ""; return; }
  box.innerHTML = `${compareMarkup(real, gimg, place.name, g?.title || "", "intro", false)}
    <div class="hero-caption">
      <div>
        <h1>${esc(place.name)}</h1>
        <p class="where">${esc([place.locality, place.country].filter(Boolean).join(", "))}${place.century ? `. Built in the ${esc(place.century.replace(/ century/, " century"))}` : ""}${place.year_text ? ` (${esc(place.year_text)})` : ""}</p>
        <p class="ingame">In ${esc(g?.title || "")}${g?.year ? `, ${esc(g.year)}` : ""}</p>
      </div>
      <div class="hero-actions">
        <a class="btn" href="#/place/${encodeURIComponent(place.id)}">Open this place</a>
        <button class="btn quiet" type="button" id="another">Show another</button>
      </div>
    </div>`;
  const cmp = box.querySelector(".compare");
  initCompare(cmp);
  if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
    cmp.style.setProperty("--cut", "100%");
    requestAnimationFrame(() => requestAnimationFrame(() => cmp.style.setProperty("--cut", "50%")));
  }
  document.getElementById("another").addEventListener("click", () => {
    const others = pool.filter((p) => p !== id);
    renderHero(others[Math.floor(Math.random() * others.length)] || id, pool);
  });
}

/* ---------- places ----------------------------------------------------- */
function placesPage() {
  setTitle("Places"); navCurrent("places");
  const p = params();
  const idx = S.index;
  const countries = [...new Set(idx.places.map((x) => x.country.split(";")[0].trim()).filter(Boolean))].sort();
  main.innerHTML = `
    <section class="browse">
      <div class="browse-head">
        <h1 class="pagetitle">Places</h1>
        <form class="filters" id="filters">
          <label>Period <select name="era"><option value="">All periods</option>${idx.eras.map((e) => `<option ${p.era === e ? "selected" : ""}>${esc(e)}</option>`).join("")}</select></label>
          <label>Country <select name="country"><option value="">All countries</option>${countries.map((c) => `<option ${p.country === c ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></label>
          <label>Order <select name="sort">
            <option value="name" ${p.sort !== "old" && p.sort !== "games" ? "selected" : ""}>A to Z</option>
            <option value="old" ${p.sort === "old" ? "selected" : ""}>Oldest first</option>
            <option value="games" ${p.sort === "games" ? "selected" : ""}>Most games</option></select></label>
        </form>
      </div>
      <p class="resultcount" id="count"></p>
      <div class="grid" id="grid"></div>
      <div class="more" id="more"></div>
    </section>`;
  const form = document.getElementById("filters");
  form.addEventListener("change", () => {
    const fd = new FormData(form);
    const q = new URLSearchParams([...fd.entries()].filter(([, v]) => v)).toString();
    history.replaceState(null, "", `#/places${q ? "?" + q : ""}`);
    draw();
  });
  let shown = PAGE;
  function draw() {
    const fd = Object.fromEntries(new FormData(form));
    let list = idx.places.filter((x) => (!fd.era || x.era === fd.era) && (!fd.country || x.country.split(";")[0].trim() === fd.country));
    if (fd.sort === "old") list = [...list].sort((a, b) => (a.year ?? 1e9) - (b.year ?? 1e9));
    if (fd.sort === "games") list = [...list].sort((a, b) => new Set(S.bySite[b.id].map((d) => d.game)).size - new Set(S.bySite[a.id].map((d) => d.game)).size);
    document.getElementById("count").textContent = `${plural(list.length, "place", "places")}${fd.era ? `, ${fd.era.toLowerCase()}` : ""}${fd.country ? `, ${fd.country}` : ""}`;
    document.getElementById("grid").innerHTML = list.slice(0, shown).map((x) => placeTile(x)).join("") || `<p class="empty">No places match these filters. Choose “All periods” or “All countries” to widen the search.</p>`;
    document.getElementById("more").innerHTML = list.length > shown ? `<button class="btn quiet" type="button" id="morebtn">Show ${Math.min(PAGE, list.length - shown)} more</button>` : "";
    document.getElementById("morebtn")?.addEventListener("click", () => { shown += PAGE; draw(); });
  }
  draw();
}

/* ---------- games ------------------------------------------------------ */
function gamesPage() {
  setTitle("Games"); navCurrent("games");
  const bySeries = {};
  S.index.games.forEach((g) => { (bySeries[g.series || g.title] ||= []).push(g); });
  const series = Object.keys(bySeries).sort((a, b) => a.localeCompare(b));
  main.innerHTML = `
    <section class="browse">
      <div class="browse-head"><h1 class="pagetitle">Games</h1><p class="resultcount">${plural(S.index.games.length, "game", "games")} in ${plural(series.length, "series", "series")}</p></div>
      ${searchBox("gq", "Search a game or a series")}
      <ul class="gamelist">
        ${series.map((s) => `<li class="serieshead">${esc(s)}</li>${bySeries[s].sort((a, b) => (a.year || "").localeCompare(b.year || "")).map((g) => `
          <li><a href="#/game/${encodeURIComponent(g.id)}">${esc(g.title)}</a><span>${esc(g.year || "")}, ${plural(new Set(S.byGame[g.id].map((d) => d.site)).size, "place", "places")}</span></li>`).join("")}`).join("")}
      </ul>
    </section>`;
  wireSearch("gq");
}

async function gamePage(id) {
  const g = S.games[id];
  if (!g) return notFound();
  setTitle(g.title); navCurrent("games");
  const deps = S.byGame[id];
  const sites = [...new Map(deps.map((d) => [d.site, d])).values()].map((d) => ({ p: S.places[d.site], d })).filter((x) => x.p);
  sites.sort((a, b) => (a.p.year ?? 1e9) - (b.p.year ?? 1e9));
  const eras = S.index.eras.filter((e) => sites.some((x) => x.p.era === e));
  const dated = sites.filter((x) => x.p.year != null);
  const span = dated.length > 1 ? `Its real places span from the ${dated[0].p.century} to the ${dated[dated.length - 1].p.century}.` : "";
  main.innerHTML = `
    <nav class="crumbs" aria-label="Breadcrumb"><a href="#/games">Games</a></nav>
    <header class="game-head">
      <h1>${esc(g.title)}</h1>
      <p>${esc(gameLine(g))}${g.series && g.series !== g.title ? `. Part of the ${esc(g.series)} series` : ""}. ${plural(sites.length, "real place", "real places")} found in the game. ${esc(span)}</p>
      <div class="chips erachips">${eras.map((e) => `<a href="#era-${encodeURIComponent(e)}">${esc(e)}</a>`).join("")}</div>
      ${g.wikipedia ? `<p class="links">${link(g.wikipedia, "About the game on Wikipedia")}</p>` : ""}
    </header>
    <section class="browse">
      ${eras.map((e) => `<h2 class="erahead" id="era-${esc(e)}">${esc(e)}</h2>
        <div class="grid">${sites.filter((x) => x.p.era === e).map((x) => placeTile(x.p, x.d.img)).join("")}</div>`).join("")}
    </section>`;
}

/* ---------- a place ---------------------------------------------------- */
async function placePage(id) {
  if (!S.places[id]) return notFound();
  main.innerHTML = `<p class="loading">Loading…</p>`;
  let p;
  try { p = await getJSON(`data/places/${encodeURIComponent(id)}.json`); } catch { return notFound(); }
  setTitle(p.name); navCurrent("places");
  const st = { real: 0, dep: 0, img: 0, mode: matchMedia("(max-width: 760px)").matches ? "side" : "side" };
  const where = [p.locality, p.country].filter(Boolean).filter((v, i, a) => a.indexOf(v) === i).join(", ");
  const facts = [
    ["Period", p.era],
    ["Built", p.century ? `${p.century}${p.year_text ? ` (${p.year_text}${/approximate/.test(p.date_basis) ? ", approximate" : ""})` : ""}` : ""],
    ["Historical period", p.period],
    ["Type", p.type.split(";").slice(0, 3).join(", ")],
    ["Style", p.style],
    ["Heritage status", p.heritage],
    ["Wikidata", p.qid ? `<a href="https://www.wikidata.org/wiki/${esc(p.qid)}" target="_blank" rel="noopener">${esc(p.qid)}</a>` : ""],
  ].filter(([, v]) => v);
  const nGames = new Set(p.depictions.map((d) => d.game)).size;

  main.innerHTML = `
    <nav class="crumbs" aria-label="Breadcrumb"><a href="#/places">Places</a>${p.era ? ` / <a href="#/places?era=${encodeURIComponent(p.era)}">${esc(p.era)}</a>` : ""}</nav>
    <header class="place-head">
      <div>
        <h1>${esc(p.name)}</h1>
        <p class="where">${esc(where)}. Appears in ${plural(nGames, "game", "games")}.</p>
      </div>
      <dl class="facts">${facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${k === "Wikidata" ? v : esc(v)}</dd>`).join("")}</dl>
    </header>
    <section class="viewer stage" aria-label="Comparison">
      <div class="viewer-bar">
        <span class="pairname" id="pairname"></span>
        <span class="modes" role="group" aria-label="Comparison view">
          <button type="button" data-mode="side" aria-pressed="true">Side by side</button>
          <button type="button" data-mode="overlay" aria-pressed="false">Overlay</button>
        </span>
      </div>
      <div class="compare-wrap" id="cmp"></div>
      <div class="picker">
        ${p.real.length > 1 ? `<h2>Photographs</h2><div class="strip real" id="realstrip">${p.real.map((r, i) => `<button type="button" data-i="${i}" aria-pressed="${i === 0}" aria-label="Photograph ${i + 1}"><img src="${thumb(r.id)}" alt="" loading="lazy"></button>`).join("")}</div>` : ""}
        <h2>In the games</h2>
        <div id="gamestrips">${p.depictions.map((d, di) => `<div class="gamegroup"><span class="gname px">${esc(S.games[d.game]?.title || d.game)}</span>
          <div class="strip game">${d.images.map((im, ii) => `<button type="button" data-d="${di}" data-i="${ii}" aria-pressed="${di === 0 && ii === 0}" aria-label="${esc(S.games[d.game]?.title || "")}, image ${ii + 1}"><img src="${thumb(im.id)}" alt="" loading="lazy"></button>`).join("")}</div></div>`).join("")}</div>
      </div>
    </section>
    <section class="texts">
      <section class="history" aria-labelledby="h-hist">
        <h2 id="h-hist">In history</h2>
        ${p.text ? `<p class="body">${esc(p.text)}</p><p class="src">Text: ${link(p.text_source, p.text_license && p.text_license.startsWith("CC0") ? "Wikidata" : "Wikipedia")}, ${esc(p.text_license || "")}.</p>` : `<p class="body muted">No description recorded yet.</p>`}
        <p class="links">${p.wikipedia ? link(p.wikipedia, "Wikipedia article") : ""}${p.qid ? link(`https://www.wikidata.org/wiki/${p.qid}`, "Wikidata record") : ""}${p.lat && p.lon ? link(`https://www.openstreetmap.org/?mlat=${p.lat}&mlon=${p.lon}#map=15/${p.lat}/${p.lon}`, "Map") : ""}</p>
        ${p.date_basis ? `<p class="src">Date source: ${esc(p.date_basis)}.</p>` : ""}
      </section>
      <section class="ingame" aria-labelledby="h-game" id="gametext"></section>
    </section>
    <section class="alsoin">
      <h2>Appears in</h2>
      <div class="chips">${[...new Set(p.depictions.map((d) => d.game))].map((gid) => `<a href="#/game/${encodeURIComponent(gid)}">${esc(S.games[gid]?.title || gid)}</a>`).join("")}</div>
    </section>`;

  const cmp = document.getElementById("cmp");
  function draw() {
    const real = p.real[st.real];
    const dep = p.depictions[st.dep];
    const gimg = dep.images[st.img];
    const g = S.games[dep.game] || {};
    document.getElementById("pairname").textContent = `${p.name} and ${dep.name} in ${g.title || ""}`;
    const rc = real ? creditReal(real) : "";
    const gc = creditGame(gimg, g);
    if (!real) {
      cmp.innerHTML = `<div class="side"><figure><div class="frame"><span class="muted">No photograph of this place yet.</span></div></figure>
        <figure><div class="frame"><img ${big(gimg)} alt="${esc(dep.name)} in ${esc(g.title || "")}"></div><span class="corner right tag-game">${esc(g.title || "")}</span><figcaption>${gc}</figcaption></figure></div>`;
    } else if (st.mode === "overlay") {
      cmp.innerHTML = `${compareMarkup(real, gimg, p.name, g.title || "")}<div class="credits"><p class="credit">Left: ${rc}</p><p class="credit">Right: ${gc}</p></div>`;
      initCompare(cmp.querySelector(".compare"));
    } else {
      cmp.innerHTML = `<div class="side">
        <figure><div class="frame"><img ${big(real)} alt="Photograph of ${esc(p.name)}" width="${real.w}" height="${real.h}"></div><span class="corner left tag-real">Real world</span><figcaption>${rc}</figcaption></figure>
        <figure><div class="frame"><img ${big(gimg)} alt="${esc(dep.name)} in ${esc(g.title || "")}" width="${gimg.w}" height="${gimg.h}"></div><span class="corner right tag-game">${esc(g.title || "")}</span><figcaption>${gc}</figcaption></figure>
      </div>`;
    }
    const gt = document.getElementById("gametext");
    gt.innerHTML = `
      <h2 id="h-game">In ${esc(g.title || "the game")}</h2>
      <p class="status ${dep.verified ? "ok" : ""}">${dep.verified ? "Match verified by the project team" : "Matched automatically from the place's name and location; not yet reviewed by the project team"}</p>
      <p class="gamefacts">${esc(gameLine(g))}${dep.name !== p.name ? `. Called “${esc(dep.name)}” in the game` : ""}.</p>
      ${dep.text ? `<p class="body">${esc(dep.text)}</p><p class="src">Text: ${link(dep.text_source, "the game's fan wiki on Fandom")}, ${esc(dep.text_license)}.</p>` : `<p class="body muted">The game's wiki has no description of this place yet.</p>`}
      <p class="links"><a href="#/game/${encodeURIComponent(dep.game)}">All places in this game</a>${g.wikipedia ? link(g.wikipedia, "The game on Wikipedia") : ""}</p>`;
  }
  draw();
  main.querySelectorAll(".modes button").forEach((b) => b.addEventListener("click", () => {
    st.mode = b.dataset.mode;
    main.querySelectorAll(".modes button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    draw();
  }));
  document.getElementById("realstrip")?.addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    st.real = +b.dataset.i;
    document.querySelectorAll("#realstrip button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    draw();
  });
  document.getElementById("gamestrips").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    st.dep = +b.dataset.d; st.img = +b.dataset.i;
    document.querySelectorAll("#gamestrips button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    draw();
    document.getElementById("cmp").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "nearest" });
  });
}
function creditReal(r) {
  const c = r.credit;
  const lic = c.license_url ? link(c.license_url, c.license) : esc(c.license);
  return `Photograph: ${esc(c.text)}, ${lic}${c.source ? `, ${link(c.source, c.via)}` : `, ${esc(c.via)}`}.${originalLink(r)}`;
}
function creditGame(im, g) {
  const c = im.credit;
  return `${esc(c.text)} ${c.source ? `Source: ${link(c.source, c.via)}.` : ""}${originalLink(im)}`;
}

/* ---------- about and methods ------------------------------------------ */
function aboutPage() {
  setTitle("About"); navCurrent("about");
  const idx = S.index;
  const url = location.href.split("#")[0];
  main.innerHTML = `
    <article class="about">
      <h1>Why compare games with the places they rebuild</h1>
      <div class="col">
        <p>Video games have become one of the main ways people encounter historical architecture. A player may walk through a reconstructed Notre-Dame, climb a pyramid or cross a battlefield long before seeing the real one, and sometimes instead of it. These reconstructions are interpretations: they select, simplify, exaggerate and invent. Comparing them with the real places shows what game makers chose to preserve and what they changed, and it treats games as part of the record of our shared cultural heritage.</p>
        <p>This site pairs ${fmt(idx.places.length)} real places with ${fmt(idx.depictions.length)} in-game versions from ${fmt(idx.games.length)} games. For each place you can compare a photograph with the game’s view side by side, read about its history and about how the game presents it, and follow every image and text back to its source.</p>
        <h2 id="methods">How the collection was made</h2>
        <ol>
          <li><b>Game images.</b> Mija Aleksandraviciute collected the first set of screenshots from the games’ fan wikis on Fandom in June to August 2026. The collection was then extended with images from the same wikis, keeping only images of places that exist in the real world, and removing character portraits, maps, menus and video stills.</li>
          <li><b>Matching.</b> Each in-game place was matched to an item on Wikidata, the open knowledge base, using its name, the kind of place, the game’s other locations, and Wikidata’s own links between places and games. Matches are applied automatically only at high confidence; games set in invented worlds are never matched by name alone.</li>
          <li><b>Review.</b> Automatic matches are labelled “not yet reviewed”. The project team reviews them, and verified matches are labelled as such.</li>
          <li><b>Photographs and facts.</b> Photographs of the real places come from Wikimedia Commons, through each place’s Wikidata record, with their authors and licences. Dates, locations, styles and heritage status come from Wikidata; the short histories come from Wikipedia; descriptions of the places in the games come from the games’ wikis.</li>
          <li><b>Periods.</b> Each place is assigned to a period from its earliest recorded date (construction, founding or opening). Dates read from text are marked as approximate.</li>
        </ol>
        <h2>Images and rights</h2>
        <p>Photographs keep the licences their authors chose, shown under each image. Game images are copyrighted by the games’ publishers; they are reproduced at reduced size for research, teaching and commentary, credited to the game and linked to the page they come from. Rights holders who would like an image removed can contact the authors.</p>
        <h2>Limitations</h2>
        <p>Automatic matching makes mistakes, especially for places that share a name. Some places have no photograph or no description yet, and dates on Wikidata vary in precision. The collection is a work in progress and is updated as the review continues.</p>
        <h2 id="data">Data for researchers</h2>
        <p>The whole catalogue is available as the Python package ${link(PACKAGE_URL, "gameheritage")}: every place, game, in-game version and image, with dates and periods, sources, licences and credit lines. It can search and filter the catalogue, show a game image beside the real photograph, and download images with an attribution file. Install it with <code>pip install "git+${PACKAGE_URL}"</code>. The tables are plain CSV files that any statistics software can read, and each release has a fixed version number for citation.</p>
        <h2>How to cite</h2>
        <p class="cite">Caliskan, C., &amp; Aleksandraviciute, M. (2026). <i>Digital Heritage in Video Games</i> (Version ${esc(idx.version)}) [Data set and website]. University of Rochester. ${esc(url)}</p>
        <p>Data current as of ${esc(idx.built)}. The data and the software that builds this site are open source: ${link(PACKAGE_URL, "github.com/cantayus/gameheritage")}.</p>
      </div>
      <aside class="bio">
        <h2>The authors</h2>
        <h3>Cantay Caliskan, PhD</h3>
        <p>Associate Professor of Instruction at the University of Rochester’s Goergen Institute for Data Science and Artificial Intelligence. His research combines computer vision, generative AI and network analysis to study political polarization, political communication and bias in artificial intelligence. He holds a PhD in Political Science from Boston University and an MBA from Rochester’s Simon Business School, and previously held faculty appointments at Denison University and Ohio Wesleyan University.</p>
        <h3>Mija Aleksandraviciute</h3>
        <p>An undergraduate at the University of Rochester pursuing degrees in Data Science and Economics, graduating in 2027. Her research combines artificial intelligence, econometrics and computational social science to examine historical worldviews, college student outcomes and entrepreneurship. She has held research positions at Rochester and NYU’s Stern School of Business, and her honors include an InnovateCS Grant and a first-place Best Insight Award at ASA DataFest 2026. She collected the original game images for this project.</p>
        <h2>Sources</h2>
        <ul class="sources">
          <li>${link("https://commons.wikimedia.org/", "Wikimedia Commons")}: photographs</li>
          <li>${link("https://www.wikidata.org/", "Wikidata")}: places, dates and facts (CC0)</li>
          <li>${link("https://www.wikipedia.org/", "Wikipedia")}: histories (CC BY-SA 4.0)</li>
          <li>${link("https://www.fandom.com/", "Fandom")} game wikis: game images and in-game descriptions (CC BY-SA 3.0 for text)</li>
        </ul>
      </aside>
    </article>`;
}

function notFound() {
  setTitle("Not found");
  main.innerHTML = `<section class="browse"><h1 class="pagetitle">This page doesn’t exist</h1><p>The link may be out of date. <a href="#/places">Browse all places</a> or <a href="#/">go to the start</a>.</p></section>`;
}

/* ---------- router ----------------------------------------------------- */
async function route() {
  const path = (location.hash.replace(/^#/, "").split("?")[0] || "/").replace(/\/+$/, "") || "/";
  const [, kind, id] = path.split("/");
  try {
    if (path === "/") await home();
    else if (kind === "places") placesPage();
    else if (kind === "games") gamesPage();
    else if (kind === "place") await placePage(decodeURIComponent(id || ""));
    else if (kind === "game") await gamePage(decodeURIComponent(id || ""));
    else if (kind === "about") aboutPage();
    else if (kind.startsWith("era-")) return; // in-page anchors on game pages
    else notFound();
  } catch (err) {
    console.error(err);
    main.innerHTML = `<section class="browse"><h1 class="pagetitle">The collection could not be loaded</h1><p>Reload the page. If this keeps happening, the data files may still be uploading.</p></section>`;
  }
  if (!path.startsWith("/era-")) { window.scrollTo(0, 0); main.focus({ preventScroll: true }); }
}

window.addEventListener("hashchange", route);
load().then(route).catch((err) => {
  console.error(err);
  main.innerHTML = `<p class="loading">The collection could not be loaded. Reload the page to try again.</p>`;
});
