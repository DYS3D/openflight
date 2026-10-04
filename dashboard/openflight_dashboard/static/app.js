'use strict';

const SVG_NS = 'http://www.w3.org/2000/svg';
const PROFILE_KEY = 'openflight-dashboard.profile';

const METRICS = {
  carry: { label: 'Carry', unit: 'yd', digits: 0 },
  ball_speed: { label: 'Ball speed', unit: 'mph', digits: 1 },
  club_speed: { label: 'Club speed', unit: 'mph', digits: 1 },
  smash: { label: 'Smash', unit: '', digits: 2 },
  launch_v: { label: 'Launch', unit: '°', digits: 1 },
  spin: { label: 'Spin', unit: 'rpm', digits: 0 },
};

const GAPPING_RANGES = [
  { id: '', label: 'All time' },
  { id: '90', label: 'Last 90 days' },
  { id: '30', label: 'Last 30 days' },
];

const pageEl = document.getElementById('page');
const tooltipEl = document.getElementById('tooltip');
const profileSelect = document.getElementById('profile');
const syncStatus = document.getElementById('sync-status');
const syncButton = document.getElementById('sync-now');

const viewState = { trendClub: null, trendMetric: 'carry', gappingRange: '', importStatus: '' };

/* ----------------------------------------------------------------- helpers */

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null) continue;
    node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function fmt(value, digits = 0) {
  if (value === null || value === undefined) return '—';
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtMetric(value, metric) {
  const spec = METRICS[metric];
  const number = fmt(value, spec.digits);
  if (value === null || value === undefined || !spec.unit) return number;
  return spec.unit === '°' ? `${number}°` : `${number} ${spec.unit}`;
}

function fmtDate(iso, withTime = false) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const options = { year: 'numeric', month: 'short', day: 'numeric' };
  if (withTime) Object.assign(options, { hour: 'numeric', minute: '2-digit' });
  return date.toLocaleString(undefined, options);
}

function fmtShortDate(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? ''
    : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function readStored(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Storage blocked: the choice still applies for this visit.
  }
}

async function api(path, params = {}) {
  const url = new URL(path, window.location.origin);
  if (profileSelect.value) url.searchParams.set('profile', profileSelect.value);
  for (const [key, value] of Object.entries(params)) {
    if (value) url.searchParams.set(key, value);
  }
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function niceTicks(min, max, count = 5) {
  if (min === max) {
    const pad = Math.abs(min) * 0.1 || 1;
    min -= pad;
    max += pad;
  }
  const rawStep = (max - min) / count;
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= rawStep);
  const start = Math.floor(min / step) * step;
  const end = Math.ceil(max / step) * step;
  const ticks = [];
  for (let value = start; value <= end + step / 2; value += step) ticks.push(Number(value.toFixed(6)));
  return ticks;
}

/* ----------------------------------------------------------------- tooltip */

function showTooltip(event, value, rows) {
  tooltipEl.replaceChildren(
    el('div', { class: 'tooltip__value' }, value),
    ...rows.map(([label, text]) => el('div', { class: 'tooltip__row' }, el('span', {}, label), el('strong', {}, text))),
  );
  tooltipEl.hidden = false;
  const box = tooltipEl.getBoundingClientRect();
  let x;
  let y;
  if (event && 'clientX' in event && event.clientX) {
    x = event.clientX + 14;
    y = event.clientY + 14;
  } else {
    const target = event.target.getBoundingClientRect();
    x = target.left + target.width / 2;
    y = target.top - box.height - 8;
  }
  x = Math.min(x, window.innerWidth - box.width - 8);
  if (y + box.height > window.innerHeight - 8) y = y - box.height - 28;
  tooltipEl.style.left = `${Math.max(8, x)}px`;
  tooltipEl.style.top = `${Math.max(8, y)}px`;
}

function hideTooltip() {
  tooltipEl.hidden = true;
}

/* ------------------------------------------------------------------ charts */

/** One series, sessions in date order, with a crosshair that snaps to a session. */
function chartWidth() {
  // Draw at the card's real width so chart text stays readable on a phone.
  const style = getComputedStyle(pageEl);
  const inner = pageEl.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  const cardInset = window.matchMedia('(max-width: 720px)').matches ? 26 : 42;
  return Math.max(280, Math.floor(inner - cardInset));
}

function lineChart(points, metric) {
  const width = chartWidth();
  const narrow = width < 560;
  const height = narrow ? 240 : 320;
  const margin = { top: 20, right: narrow ? 70 : 90, bottom: 40, left: narrow ? 48 : 64 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;
  const values = points.map((point) => point[metric]).filter((value) => value !== null);
  const ticks = niceTicks(Math.min(...values), Math.max(...values));
  const yMin = ticks[0];
  const yMax = ticks[ticks.length - 1];
  const x = (index) => margin.left + (points.length === 1 ? plotW / 2 : (index / (points.length - 1)) * plotW);
  const y = (value) => margin.top + plotH - ((value - yMin) / (yMax - yMin)) * plotH;
  const spec = METRICS[metric];

  const root = svg('svg', {
    class: 'chart',
    viewBox: `0 0 ${width} ${height}`,
    role: 'img',
    tabindex: '0',
    'aria-label': `${spec.label} by session. Use the arrow keys to read each session.`,
  });

  for (const tick of ticks) {
    root.append(
      svg('line', { class: 'grid', x1: margin.left, x2: margin.left + plotW, y1: y(tick), y2: y(tick) }),
      svg('text', { x: margin.left - 10, y: y(tick) + 4, 'text-anchor': 'end' }, fmt(tick, spec.digits === 2 ? 2 : 0)),
    );
  }

  const labelEvery = Math.max(1, Math.ceil(points.length / (narrow ? 4 : 8)));
  points.forEach((point, index) => {
    if (index % labelEvery === 0 || index === points.length - 1) {
      root.append(svg('text', { x: x(index), y: height - 12, 'text-anchor': 'middle' }, fmtShortDate(point.started_at)));
    }
  });

  const segments = [];
  let current = [];
  points.forEach((point, index) => {
    if (point[metric] === null) {
      if (current.length) segments.push(current);
      current = [];
    } else {
      current.push([x(index), y(point[metric])]);
    }
  });
  if (current.length) segments.push(current);
  for (const segment of segments) {
    if (segment.length > 1) {
      const path = segment.map(([px, py], i) => `${i ? 'L' : 'M'}${px},${py}`).join(' ');
      root.append(svg('path', { class: 'line', d: path }));
    }
  }

  const dots = points.map((point, index) =>
    point[metric] === null ? null : svg('circle', { class: 'dot', cx: x(index), cy: y(point[metric]), r: 5 }),
  );
  root.append(...dots.filter(Boolean));

  const lastIndex = points.map((point) => point[metric]).findLastIndex((value) => value !== null);
  if (lastIndex >= 0) {
    root.append(
      svg(
        'text',
        { class: 'value-label', x: x(lastIndex) + 12, y: y(points[lastIndex][metric]) + 4 },
        fmtMetric(points[lastIndex][metric], metric),
      ),
    );
  }

  const crosshair = svg('line', { class: 'crosshair', y1: margin.top, y2: margin.top + plotH, visibility: 'hidden' });
  const hit = svg('rect', { x: margin.left - 20, y: 0, width: plotW + 40, height, fill: 'transparent' });
  root.append(crosshair, hit);

  let active = -1;
  const select = (index, event) => {
    if (active >= 0 && dots[active]) dots[active].classList.remove('dot--active');
    active = index;
    const point = points[index];
    crosshair.setAttribute('x1', x(index));
    crosshair.setAttribute('x2', x(index));
    crosshair.setAttribute('visibility', 'visible');
    if (dots[index]) dots[index].classList.add('dot--active');
    showTooltip(event, fmtMetric(point[metric], metric), [
      ['Session', fmtDate(point.started_at)],
      ['Shots', String(point.shots)],
    ]);
  };
  const clear = () => {
    if (active >= 0 && dots[active]) dots[active].classList.remove('dot--active');
    active = -1;
    crosshair.setAttribute('visibility', 'hidden');
    hideTooltip();
  };
  hit.addEventListener('pointermove', (event) => {
    const box = root.getBoundingClientRect();
    const svgX = ((event.clientX - box.left) / box.width) * width;
    let nearest = 0;
    points.forEach((_, index) => {
      if (Math.abs(x(index) - svgX) < Math.abs(x(nearest) - svgX)) nearest = index;
    });
    select(nearest, event);
  });
  hit.addEventListener('pointerleave', clear);
  root.addEventListener('blur', clear);
  root.addEventListener('keydown', (event) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const step = event.key === 'ArrowRight' ? 1 : -1;
    const next = active < 0 ? points.length - 1 : Math.min(points.length - 1, Math.max(0, active + step));
    select(next, { target: dots[next] ?? root });
  });
  return root;
}

/** Carry per club: min–max hairline, middle-50% bar, median dot. */
function gappingChart(rows) {
  const width = chartWidth();
  const narrow = width < 560;
  // On a phone the club name sits above its bar so the bars get the full width.
  const rowH = narrow ? 56 : 46;
  const margin = { top: 8, right: narrow ? 64 : 90, bottom: 40, left: narrow ? 8 : 140 };
  const plotW = width - margin.left - margin.right;
  const height = margin.top + rows.length * rowH + margin.bottom;
  const ticks = niceTicks(
    Math.min(...rows.map((row) => row.min)),
    Math.max(...rows.map((row) => row.max)),
    narrow ? 4 : 6,
  );
  const xMin = ticks[0];
  const xMax = ticks[ticks.length - 1];
  const x = (value) => margin.left + ((value - xMin) / (xMax - xMin)) * plotW;
  const plotBottom = margin.top + rows.length * rowH;

  const root = svg('svg', {
    class: 'chart',
    viewBox: `0 0 ${width} ${height}`,
    role: 'img',
    'aria-label': 'Carry distance spread for each club',
  });
  for (const tick of ticks) {
    root.append(
      svg('line', { class: 'grid', x1: x(tick), x2: x(tick), y1: margin.top, y2: plotBottom }),
      svg('text', { x: x(tick), y: plotBottom + 22, 'text-anchor': 'middle' }, narrow ? fmt(tick) : `${fmt(tick)} yd`),
    );
  }

  rows.forEach((row, index) => {
    const rowTop = margin.top + index * rowH;
    const cy = narrow ? rowTop + rowH - 16 : rowTop + rowH / 2;
    const labelY = narrow ? rowTop + 18 : cy + 4;
    const barH = 12;
    const group = svg('g', {});
    const hit = svg('rect', {
      class: 'row-hit',
      x: 0,
      y: rowTop,
      width,
      height: rowH,
      tabindex: '0',
      'aria-label': `${row.club_name}: median ${row.median} yards, middle half ${row.p25} to ${row.p75}`,
    });
    group.append(
      hit,
      svg('text', { class: 'club-label', x: 0, y: labelY }, row.club_name),
      svg('line', { class: 'track', x1: x(row.min), x2: x(row.max), y1: cy, y2: cy }),
      svg('rect', {
        class: 'iqr',
        x: x(row.p25),
        y: cy - barH / 2,
        width: Math.max(4, x(row.p75) - x(row.p25)),
        height: barH,
        rx: 4,
      }),
      svg('circle', { class: 'median', cx: x(row.median), cy, r: 6 }),
      svg('text', { class: 'value-label', x: width - margin.right + 16, y: cy + 4 }, `${fmt(row.median)} yd`),
    );
    for (const child of [...group.children]) child.style.pointerEvents = child === hit ? 'all' : 'none';
    const show = (event) =>
      showTooltip(event, `${fmt(row.median)} yd median`, [
        ['Middle half', `${fmt(row.p25)}–${fmt(row.p75)} yd`],
        ['Shortest–longest', `${fmt(row.min)}–${fmt(row.max)} yd`],
        ['Shots', String(row.shots)],
        ['Ball speed', `${fmt(row.median_ball_speed, 1)} mph`],
      ]);
    hit.addEventListener('pointermove', show);
    hit.addEventListener('focus', show);
    hit.addEventListener('pointerleave', hideTooltip);
    hit.addEventListener('blur', hideTooltip);
    root.append(group);
  });
  return root;
}

/* ------------------------------------------------------------------- pages */

function tile(label, value, unit, note, hero = false) {
  return el(
    'div',
    { class: hero ? 'tile tile--hero' : 'tile' },
    el('div', { class: 'eyebrow' }, label),
    el('div', { class: 'tile__value' }, value, unit ? el('span', { class: 'tile__unit' }, unit) : null),
    note ? el('div', { class: 'tile__note' }, note) : null,
  );
}

function table(headers, rows, onRowClick) {
  return el(
    'div',
    { class: 'table-wrap' },
    el(
      'table',
      {},
      el('thead', {}, el('tr', {}, headers.map((header) => el('th', { class: header.left ? 'left' : null }, header.label)))),
      el(
        'tbody',
        {},
        rows.map((row, index) => {
          const attrs = { class: onRowClick ? 'link' : null };
          if (onRowClick) {
            Object.assign(attrs, {
              tabindex: '0',
              onclick: () => onRowClick(index),
              onkeydown: (event) => {
                if (event.key === 'Enter') onRowClick(index);
              },
            });
          }
          return el('tr', attrs, row.map((cell, column) => el('td', { class: headers[column].left ? 'left' : null }, cell)));
        }),
      ),
    ),
  );
}

function empty(message) {
  return el('div', { class: 'empty' }, message);
}

function noShotsYet() {
  return empty(
    'No shots yet. Start the Pi with --session-log-api and point this dashboard at it with --pi, ' +
      'or load copied logs with "openflight-dashboard import".',
  );
}

/* SkyTrak "Shots History" CSV exports, loaded under a golfer's profile. */
async function skytrakImport() {
  const profiles = await api('/api/profiles');
  const golfer = el('input', {
    class: 'input',
    name: 'golfer',
    list: 'skytrak-golfers',
    placeholder: 'Golfer',
    required: true,
    value: profiles[0]?.name || '',
    'aria-label': 'Golfer',
  });
  const files = el('input', { type: 'file', name: 'files', accept: '.csv', multiple: true, required: true, 'aria-label': 'SkyTrak CSV files' });
  // Survives the re-render that shows the imported sessions.
  const status = el('p', { class: 'import__status', role: 'status' }, viewState.importStatus);
  const submit = el('button', { type: 'submit', class: 'button' }, 'Import');
  const form = el(
    'form',
    {
      class: 'import',
      onsubmit: async (event) => {
        event.preventDefault();
        submit.disabled = true;
        status.textContent = 'Importing…';
        try {
          const response = await fetch('/api/import/skytrak', { method: 'POST', body: new FormData(form) });
          if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
          const { results } = await response.json();
          const loaded = results.filter((result) => !result.error);
          const shots = loaded.reduce((sum, result) => sum + result.shots, 0);
          const failed = results.filter((result) => result.error).map((result) => `${result.file}: ${result.error}`);
          viewState.importStatus =
            `Imported ${loaded.length} session(s), ${shots} shots.` + (failed.length ? ` Skipped ${failed.join('; ')}` : '');
          status.textContent = viewState.importStatus;
          if (loaded.length) {
            await loadProfiles();
            await render();
          }
        } catch (error) {
          status.textContent = `Import failed: ${error.message}`;
        } finally {
          submit.disabled = false;
        }
      },
    },
    el('h2', {}, 'Import SkyTrak'),
    el('p', { class: 'lede' }, 'Add SkyTrak "Shots History" CSV exports to a golfer. Importing the same export again replaces it.'),
    el('datalist', { id: 'skytrak-golfers' }, profiles.map((profile) => el('option', { value: profile.name || profile.id }))),
    el('div', { class: 'import__row' }, golfer, files, submit),
    status,
  );
  return form;
}

async function sessionsPage() {
  const sessions = await api('/api/sessions');
  if (!sessions.length) return [el('h1', {}, 'Sessions'), noShotsYet(), await skytrakImport()];
  const totalShots = sessions.reduce((sum, session) => sum + session.shots, 0);
  const latest = sessions[0];
  return [
    el('h1', {}, 'Sessions'),
    el('p', { class: 'lede' }, 'Every practice session copied from the Pi. Pick one to see each shot.'),
    el(
      'div',
      { class: 'tiles' },
      tile('Sessions', fmt(sessions.length), '', null, true),
      tile('Shots', fmt(totalShots), '', null, true),
      tile('Last session', fmtShortDate(latest.started_at), '', `${latest.shots} shots`),
    ),
    table(
      [
        { label: 'Date', left: true },
        { label: 'Clubs', left: true },
        { label: 'Shots' },
        { label: 'Ball speed (median)' },
        { label: 'Carry (median)' },
      ],
      sessions.map((session) => [
        fmtDate(session.started_at, true),
        session.clubs.join(', '),
        fmt(session.shots),
        fmtMetric(session.median_ball_speed, 'ball_speed'),
        fmtMetric(session.median_carry, 'carry'),
      ]),
      (index) => {
        window.location.hash = `#/sessions/${encodeURIComponent(sessions[index].id)}`;
      },
    ),
    await skytrakImport(),
  ];
}

async function sessionPage(sessionId) {
  const session = await api(`/api/sessions/${encodeURIComponent(sessionId)}`);
  const shots = session.shots;
  const median = (key) => {
    const values = shots.map((shot) => shot[key]).filter((value) => value !== null).sort((a, b) => a - b);
    if (!values.length) return null;
    const mid = Math.floor(values.length / 2);
    return values.length % 2 ? values[mid] : (values[mid - 1] + values[mid]) / 2;
  };
  return [
    el('a', { class: 'back', href: '#/sessions' }, '← All sessions'),
    el('h1', {}, fmtDate(session.started_at, true)),
    el(
      'div',
      { class: 'session-actions' },
      el('p', { class: 'lede' }, `${shots.length} shots`),
      el(
        'button',
        {
          type: 'button',
          class: 'button button--quiet',
          onclick: async () => {
            if (!window.confirm('Remove this session from the dashboard? Its raw files stay archived.')) return;
            const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/hide`, { method: 'POST' });
            if (response.ok) window.location.hash = '#/sessions';
          },
        },
        'Remove from dashboard',
      ),
    ),
    el(
      'div',
      { class: 'tiles' },
      tile('Carry', fmt(median('carry')), 'yd', 'median', true),
      tile('Ball speed', fmt(median('ball_speed'), 1), 'mph', 'median', true),
      tile('Club speed', fmt(median('club_speed'), 1), 'mph', 'median'),
      tile('Launch', fmt(median('launch_v'), 1), '°', 'median'),
      tile('Spin', fmt(median('spin')), 'rpm', 'median'),
    ),
    shots.length
      ? table(
          [
            { label: '#', left: true },
            { label: 'Club', left: true },
            { label: 'Ball' },
            { label: 'Club spd' },
            { label: 'Smash' },
            { label: 'Launch' },
            { label: 'Direction' },
            { label: 'Spin' },
            { label: 'Carry' },
          ],
          shots.map((shot, index) => [
            String(shot.shot_number ?? index + 1),
            shot.club_name,
            fmt(shot.ball_speed, 1),
            fmt(shot.club_speed, 1),
            fmt(shot.smash, 2),
            shot.launch_v === null ? '—' : `${fmt(shot.launch_v, 1)}°`,
            shot.launch_h === null ? '—' : `${fmt(Math.abs(shot.launch_h), 1)}° ${shot.launch_h < 0 ? 'L' : 'R'}`,
            fmt(shot.spin),
            fmt(shot.carry),
          ]),
        )
      : empty('No shots for this golfer in this session.'),
  ];
}

async function trendsPage() {
  const clubs = await api('/api/trends');
  if (!clubs.length) return [el('h1', {}, 'Trends'), noShotsYet()];
  if (!clubs.some((club) => club.club === viewState.trendClub)) {
    viewState.trendClub = [...clubs].sort((a, b) => b.points.length - a.points.length)[0].club;
  }
  const club = clubs.find((entry) => entry.club === viewState.trendClub);
  const metric = viewState.trendMetric;
  const spec = METRICS[metric];
  const hasValues = club.points.some((point) => point[metric] !== null);

  const clubSelect = el(
    'select',
    { onchange: (event) => { viewState.trendClub = event.target.value; render(); } },
    clubs.map((entry) => el('option', { value: entry.club, selected: entry.club === club.club }, entry.club_name)),
  );
  const metricSelect = el(
    'select',
    { onchange: (event) => { viewState.trendMetric = event.target.value; render(); } },
    Object.entries(METRICS).map(([id, entry]) => el('option', { value: id, selected: id === metric }, entry.label)),
  );

  return [
    el('h1', {}, 'Trends'),
    el('p', { class: 'lede' }, 'How a club is changing over time. Each point is one session’s median, so a mishit or two does not move it.'),
    el(
      'div',
      { class: 'controls' },
      el('label', { class: 'field' }, el('span', { class: 'field__label' }, 'Club'), clubSelect),
      el('label', { class: 'field' }, el('span', { class: 'field__label' }, 'Metric'), metricSelect),
    ),
    el(
      'section',
      { class: 'card' },
      el('h2', { class: 'card__title' }, `${club.club_name} · ${spec.label}`),
      el('p', { class: 'card__sub' }, `Median per session${spec.unit ? ` (${spec.unit})` : ''}`),
      hasValues ? lineChart(club.points, metric) : empty(`No ${spec.label.toLowerCase()} readings for this club yet.`),
    ),
    table(
      [{ label: 'Session', left: true }, { label: 'Shots' }, { label: spec.label }],
      [...club.points].reverse().map((point) => [fmtDate(point.started_at), String(point.shots), fmtMetric(point[metric], metric)]),
      (index) => {
        const point = [...club.points].reverse()[index];
        window.location.hash = `#/sessions/${encodeURIComponent(point.session_id)}`;
      },
    ),
  ];
}

async function gappingPage() {
  let since = null;
  if (viewState.gappingRange) {
    const start = new Date();
    start.setDate(start.getDate() - Number(viewState.gappingRange));
    since = start.toISOString().slice(0, 19);
  }
  const [rows, wedges] = await Promise.all([api('/api/gapping', { since }), api('/api/wedges')]);
  const rangeSelect = el(
    'select',
    { onchange: (event) => { viewState.gappingRange = event.target.value; render(); } },
    GAPPING_RANGES.map((range) => el('option', { value: range.id, selected: range.id === viewState.gappingRange }, range.label)),
  );
  return [
    el('h1', {}, 'Club gapping'),
    el('p', { class: 'lede' }, 'Carry distance per club. The bar is where half your shots land; the dot is the middle shot.'),
    el('div', { class: 'controls' }, el('label', { class: 'field' }, el('span', { class: 'field__label' }, 'Range'), rangeSelect)),
    rows.length
      ? el(
          'section',
          { class: 'card' },
          gappingChart(rows),
          el(
            'div',
            { class: 'legend' },
            el('span', {}, el('span', { class: 'legend__key' }), 'Middle half of shots'),
            el('span', {}, el('span', { class: 'legend__key legend__key--median' }), 'Median'),
            el('span', {}, el('span', { class: 'legend__key legend__key--track' }), 'Shortest to longest'),
          ),
        )
      : empty('No carry readings in this range.'),
    rows.length
      ? table(
          [
            { label: 'Club', left: true },
            { label: 'Shots' },
            { label: 'Median' },
            { label: 'Middle half' },
            { label: 'Gap to next' },
            { label: 'Ball speed' },
          ],
          rows.map((row, index) => {
            const next = rows[index + 1];
            return [
              row.club_name,
              String(row.shots),
              `${fmt(row.median)} yd`,
              `${fmt(row.p25)}–${fmt(row.p75)} yd`,
              next ? `${fmt(row.median - next.median)} yd` : '—',
              `${fmt(row.median_ball_speed, 1)} mph`,
            ];
          }),
        )
      : null,
    wedgeMatrix(wedges),
  ];
}

const SWING_LABELS = { full: 'Full', '3/4': '¾', '1/2': '½' };

/* Wedge × swing-length carries, from shots tagged on the kiosk (Practice → Wedges). */
function wedgeMatrix(rows) {
  return el(
    'section',
    { class: 'wedges' },
    el('h2', {}, 'Wedge matrix'),
    el('p', { class: 'lede' }, 'Median carry for each wedge at each swing length. Tag swings on the unit with Practice → Wedges.'),
    rows.length
      ? table(
          [{ label: 'Wedge', left: true }, ...Object.values(SWING_LABELS).map((label) => ({ label }))],
          rows.map((row) => [
            row.club_name,
            ...Object.keys(SWING_LABELS).map((swing) => {
              const cell = row.cells[swing];
              return cell.median === null ? '—' : `${fmt(cell.median)} yd (${cell.shots})`;
            }),
          ]),
        )
      : empty('No tagged wedge shots yet.'),
  );
}

function recordRow(label, record, metric) {
  return el(
    'div',
    { class: 'record__row' },
    el('span', { class: 'eyebrow' }, label),
    el(
      'span',
      {},
      el('span', { class: 'record__value' }, record ? fmtMetric(record.value, metric) : '—'),
      record ? ' ' : null,
      record
        ? el('a', { class: 'record__date', href: `#/sessions/${encodeURIComponent(record.session_id)}` }, fmtShortDate(record.timestamp))
        : null,
    ),
  );
}

async function recordsPage() {
  const records = await api('/api/records');
  if (!records.length) return [el('h1', {}, 'Personal records'), noShotsYet()];
  return [
    el('h1', {}, 'Personal records'),
    el(
      'p',
      { class: 'lede' },
      'Your best with each club. Club speed ignores shots with an impossible smash factor (above 1.6), which are bad reads.',
    ),
    el(
      'div',
      { class: 'records' },
      records.map((record) =>
        el(
          'article',
          { class: 'record' },
          el('h2', { class: 'record__club' }, record.club_name),
          recordRow('Ball speed', record.ball_speed, 'ball_speed'),
          recordRow('Club speed', record.club_speed, 'club_speed'),
          recordRow('Carry', record.carry, 'carry'),
          el('div', { class: 'tile__note' }, `${fmt(record.shots)} shots`),
        ),
      ),
    ),
  ];
}

/* ------------------------------------------------------------- app chrome */

function route() {
  const hash = window.location.hash.replace(/^#\/?/, '');
  const [page, ...rest] = hash.split('/');
  if (page === 'sessions' && rest.length) return { page: 'sessions', render: () => sessionPage(decodeURIComponent(rest.join('/'))) };
  const pages = { sessions: sessionsPage, trends: trendsPage, gapping: gappingPage, records: recordsPage };
  return pages[page] ? { page, render: pages[page] } : { page: 'home', render: homePage };
}

let renderToken = 0;

async function render() {
  const token = ++renderToken;
  const { page, render: build } = route();
  for (const link of document.querySelectorAll('.tabs a')) {
    if (link.dataset.page === page) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  hideTooltip();
  pageEl.dataset.loading = 'true';
  try {
    const content = await build();
    if (token !== renderToken) return;
    pageEl.replaceChildren(...content.filter(Boolean));
    // Calendars start scrolled to the most recent weeks on narrow screens.
    for (const wrap of pageEl.querySelectorAll('.calendar-wrap')) wrap.scrollLeft = wrap.scrollWidth;
  } catch (error) {
    if (token !== renderToken) return;
    pageEl.replaceChildren(el('h1', {}, 'Something went wrong'), empty(String(error.message || error)));
  } finally {
    if (token === renderToken) pageEl.dataset.loading = 'false';
  }
}

async function refreshSummary() {
  const summary = await api('/api/summary');
  syncButton.hidden = !summary.sync_enabled;
  if (!summary.sync_enabled) {
    syncStatus.textContent = 'Not linked to a Pi';
    syncStatus.dataset.state = 'off';
  } else if (summary.last_error) {
    syncStatus.textContent = 'Pi unreachable';
    syncStatus.title = summary.last_error;
    syncStatus.dataset.state = 'error';
  } else {
    syncStatus.textContent = summary.last_sync ? `Synced ${fmtDate(summary.last_sync, true)}` : 'Waiting for first sync';
    if (summary.offload) syncStatus.textContent += ` · ${summary.archived_sessions} sessions moved off the Pi`;
    syncStatus.removeAttribute('title');
    syncStatus.dataset.state = 'ok';
  }
}

async function loadProfiles() {
  const profiles = await api('/api/profiles');
  const stored = readStored(PROFILE_KEY) || '';
  profileSelect.replaceChildren(
    el('option', { value: '' }, 'Everyone'),
    ...profiles.map((profile) => el('option', { value: profile.id }, profile.name || profile.id)),
  );
  profileSelect.value = profiles.some((profile) => profile.id === stored) ? stored : '';
}

profileSelect.addEventListener('change', () => {
  writeStored(PROFILE_KEY, profileSelect.value);
  render();
});

syncButton.addEventListener('click', async () => {
  syncButton.disabled = true;
  try {
    await fetch('/api/sync', { method: 'POST' });
    await Promise.all([refreshSummary(), loadProfiles()]);
    await render();
  } finally {
    syncButton.disabled = false;
  }
});

window.addEventListener('hashchange', render);

let lastWidth = pageEl.clientWidth;
let resizeTimer = 0;
window.addEventListener('resize', () => {
  window.clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(() => {
    if (Math.abs(pageEl.clientWidth - lastWidth) < 40) return;
    lastWidth = pageEl.clientWidth;
    if (pageEl.querySelector('svg.chart')) render();
  }, 200);
});

(async () => {
  await Promise.all([refreshSummary().catch(() => {}), loadProfiles().catch(() => {})]);
  await render();
  window.setInterval(() => refreshSummary().catch(() => {}), 60_000);
})();
