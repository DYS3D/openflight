'use strict';

/* Home page: the night-range view, bag, trends, bests and practice calendar.
   Uses the helpers defined in app.js (el, svg, fmt, api, tooltip, ...). */

const homeState = { club: null, days: '90' };

const HOME_RANGES = [
  { id: '30', label: '30 days' },
  { id: '90', label: '90 days' },
  { id: '', label: 'All time' },
];

// Practice calendar steps, one copper hue from few shots to many.
const CALENDAR_RAMP = ['#6a5139', '#926b43', '#ba864d', '#e3a965'];

// Half-angle of the fan drawn as the "fairway" of the range.
const FAN_DEG = 11;

function clubShort(club) {
  const match = /^(\d+)-(wood|hybrid|iron)$/.exec(club);
  if (match) return `${match[1]}${{ wood: 'W', hybrid: 'H', iron: 'I' }[match[2]]}`;
  return { driver: 'DR', pw: 'PW', gw: 'GW', sw: 'SW', lw: 'LW' }[club] ?? club.slice(0, 3).toUpperCase();
}

function niceStep(maxValue, targetLines) {
  return [10, 25, 50, 100].find((step) => maxValue / step <= targetLines) ?? 100;
}

/**
 * Top-down range with yardage arcs and every shot where it landed. Wide
 * screens put the tee on the left; phones stand it upright with the tee at
 * the bottom. One scale serves both axes so the pattern keeps its true shape.
 */
function rangeChart(range, selectedClub) {
  const width = chartWidth();
  const vertical = width < 560;
  const carries = range.shots.map((shot) => shot.carry).concat(range.clubs.map((club) => club.median));
  const longest = Math.max(100, ...carries);
  const step = niceStep(longest, vertical ? 4 : 6);
  const maxDistance = Math.ceil((longest * 1.06) / step) * step;
  const fan = (FAN_DEG * Math.PI) / 180;
  const labels = range.clubs.map((club) => {
    const text = `${clubShort(club.club)} ${club.median}`;
    return { club, text, size: text.length * 7 + 14 };
  });

  let height;
  let scale;
  let place; // (carry, side) -> [x, y]
  let labelSlot; // label -> { x, y } of the pill's top-left, plus the stem end
  if (vertical) {
    const labelColumn = 78;
    const fanHalf = (width - labelColumn - 16) / 2;
    scale = Math.min(fanHalf / (maxDistance * Math.sin(fan)), 520 / maxDistance);
    height = Math.round(maxDistance * scale + 40);
    const midX = labelColumn + 8 + fanHalf;
    const teeY = height - 14;
    place = (carry, side) => [midX + side * scale, teeY - carry * scale];
    const columns = [];
    labelSlot = (label) => {
      const [, y] = place(label.club.median, 0);
      let column = columns.findIndex((spans) => spans.every(([top, bottom]) => y + 13 < top || y - 13 > bottom));
      if (column < 0) column = Math.min(columns.length, 1);
      (columns[column] ??= []).push([y - 9, y + 9]);
      const x = 4 + column * 40;
      return { x, y: y - 9, stem: [x + label.size, y, midX, y], labelX: x + label.size / 2 };
    };
  } else {
    const rowStep = 20;
    const top = 3 * rowStep + 14;
    height = Math.round(Math.min(520, Math.max(320, width * 0.42)));
    const left = 40;
    const halfHeight = (height - top - 18) / 2;
    scale = Math.min((width - left - 28) / maxDistance, halfHeight / (maxDistance * Math.sin(fan)));
    const midY = top + halfHeight;
    place = (carry, side) => [left + carry * scale, midY + side * scale];
    const rows = [];
    labelSlot = (label) => {
      const [x] = place(label.club.median, 0);
      const half = label.size / 2;
      let row = rows.findIndex((spans) => spans.every(([start, end]) => x + half + 4 < start || x - half - 4 > end));
      if (row < 0) row = Math.min(rows.length, 2);
      (rows[row] ??= []).push([x - half, x + half]);
      const y = 2 + row * rowStep;
      return { x: x - half, y, stem: [x, y + 18, x, midY], labelX: x };
    };
  }

  const root = svg('svg', {
    class: 'chart range-chart',
    viewBox: `0 0 ${width} ${height}`,
    role: 'img',
    'aria-label': `Top-down view of ${range.shots.length} shots by landing spot`,
  });

  const [teeX, teeY] = place(0, 0);
  const edge = (distance, sign) => place(distance * Math.cos(fan), sign * distance * Math.sin(fan));
  const arc = (distance) => {
    const r = distance * scale;
    const [x1, y1] = edge(distance, -1);
    const [x2, y2] = edge(distance, 1);
    return [`M${x1},${y1} A${r},${r} 0 0 1 ${x2},${y2}`, [x1, y1]];
  };
  const [fanArc] = arc(maxDistance);
  root.append(svg('path', { class: 'range-fan', d: `M${teeX},${teeY} L${fanArc.slice(1)} Z` }));
  for (let distance = step; distance <= maxDistance; distance += step) {
    const [d, [lx, ly]] = arc(distance);
    root.append(svg('path', { class: 'range-arc', d }));
    root.append(
      svg(
        'text',
        vertical ? { class: 'range-yardage', x: lx - 4, y: ly + 4, 'text-anchor': 'end' } : { class: 'range-yardage', x: lx + 4, y: ly - 4 },
        `${distance}`,
      ),
    );
  }
  const [endX, endY] = place(maxDistance, 0);
  root.append(svg('line', { class: 'range-target', x1: teeX, y1: teeY, x2: endX, y2: endY }));
  root.append(svg('circle', { class: 'range-tee', cx: teeX, cy: teeY, r: 4 }));

  for (const label of labels) {
    const slot = labelSlot(label);
    const active = !selectedClub || selectedClub === label.club.club;
    const [x1, y1, x2, y2] = slot.stem;
    root.append(
      svg(
        'g',
        { class: active ? 'range-label' : 'range-label range-label--dim' },
        svg('line', { class: 'range-label__stem', x1, y1, x2, y2 }),
        svg('rect', { class: 'range-label__pill', x: slot.x, y: slot.y, width: label.size, height: 18, rx: 9 }),
        svg('text', { class: 'range-label__text', x: slot.labelX, y: slot.y + 13, 'text-anchor': 'middle' }, label.text),
      ),
    );
  }

  const points = range.shots.map((shot) => {
    const [x, y] = place(shot.carry, shot.side);
    return { shot, x: Math.min(width - 4, Math.max(4, x)), y: Math.min(height - 4, Math.max(4, y)) };
  });
  const isActive = (point) => !selectedClub || point.shot.club === selectedClub;
  const dots = svg('g', {});
  for (const point of [...points].sort((a, b) => Number(isActive(a)) - Number(isActive(b)))) {
    const active = isActive(point);
    dots.append(
      svg('circle', {
        class: active ? (selectedClub ? 'range-dot range-dot--focus' : 'range-dot') : 'range-dot range-dot--dim',
        cx: point.x,
        cy: point.y,
        r: active && selectedClub ? 4.5 : 3.5,
      }),
    );
  }
  root.append(dots);

  const halo = svg('circle', { class: 'range-halo', r: 9, visibility: 'hidden' });
  root.append(halo);
  const clear = () => {
    halo.setAttribute('visibility', 'hidden');
    hideTooltip();
  };
  root.addEventListener('pointermove', (event) => {
    const box = root.getBoundingClientRect();
    const sx = ((event.clientX - box.left) / box.width) * width;
    const sy = ((event.clientY - box.top) / box.height) * height;
    let best = null;
    let bestDistance = 18;
    for (const point of points) {
      if (!isActive(point)) continue;
      const distance = Math.hypot(point.x - sx, point.y - sy);
      if (distance < bestDistance) {
        best = point;
        bestDistance = distance;
      }
    }
    if (!best) {
      clear();
      return;
    }
    halo.setAttribute('cx', best.x);
    halo.setAttribute('cy', best.y);
    halo.setAttribute('visibility', 'visible');
    const side = best.shot.side;
    showTooltip(event, `${fmt(best.shot.carry)} yd`, [
      ['Club', clubName(best.shot.club, range)],
      ['Direction', Math.abs(side) < 0.5 ? 'On line' : `${fmt(Math.abs(side))} yd ${side < 0 ? 'left' : 'right'}`],
      ['Ball speed', `${fmt(best.shot.ball_speed, 1)} mph`],
      ['Date', fmtShortDate(best.shot.timestamp)],
    ]);
  });
  root.addEventListener('pointerleave', clear);
  return root;
}

function clubName(club, range) {
  return range.clubs.find((entry) => entry.club === club)?.club_name ?? club;
}

/** GitHub-style grid: one column per week, newest on the right. */
function calendarChart(days) {
  const cell = 13;
  const gap = 3;
  const top = 18;
  const left = 26;
  const first = new Date(`${days[0].date}T00:00:00`);
  const offset = first.getDay();
  const weeks = Math.ceil((days.length + offset) / 7);
  const width = left + weeks * (cell + gap) + 18;
  const height = top + 7 * (cell + gap);
  const max = Math.max(1, ...days.map((day) => day.shots));
  const root = svg('svg', {
    class: 'chart calendar',
    viewBox: `0 0 ${width} ${height}`,
    role: 'img',
    'aria-label': `Practice days: ${days.filter((day) => day.shots).length} in the last ${Math.round(days.length / 7)} weeks`,
  });
  ['Mon', 'Wed', 'Fri'].forEach((label, index) => {
    root.append(svg('text', { x: 0, y: top + (index * 2 + 1) * (cell + gap) + cell - 2 }, label));
  });
  let lastMonth = -1;
  days.forEach((day, index) => {
    const position = index + offset;
    const week = Math.floor(position / 7);
    const weekday = position % 7;
    const date = new Date(`${day.date}T00:00:00`);
    if (weekday === 0 && date.getMonth() !== lastMonth) {
      lastMonth = date.getMonth();
      root.append(svg('text', { x: left + week * (cell + gap), y: 11 }, date.toLocaleDateString(undefined, { month: 'short' })));
    }
    const level = day.shots ? Math.min(3, Math.floor((day.shots / max) * 4 - 1e-9)) : -1;
    const rect = svg('rect', {
      class: level < 0 ? 'calendar__day calendar__day--empty' : 'calendar__day',
      x: left + week * (cell + gap),
      y: top + weekday * (cell + gap),
      width: cell,
      height: cell,
      rx: 3,
      fill: level < 0 ? null : CALENDAR_RAMP[level],
      tabindex: day.shots ? '0' : null,
    });
    const show = (event) =>
      showTooltip(event, day.shots ? `${day.shots} shots` : 'No practice', [
        ['Date', date.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })],
      ]);
    rect.addEventListener('pointermove', show);
    rect.addEventListener('focus', show);
    rect.addEventListener('pointerleave', hideTooltip);
    rect.addEventListener('blur', hideTooltip);
    root.append(rect);
  });
  return root;
}

function trendRow(trend) {
  const up = trend.delta > 0;
  const better = trend.metric === 'carry' || trend.metric.endsWith('speed') ? up : !up;
  return el(
    'li',
    { class: 'trend' },
    el('span', { class: `trend__arrow trend__arrow--${better ? 'good' : 'bad'}`, 'aria-hidden': 'true' }, up ? '▲' : '▼'),
    el(
      'span',
      { class: 'trend__text' },
      el('strong', {}, `${up ? '+' : '−'}${fmt(Math.abs(trend.delta), 1)} ${trend.unit}`),
      ` ${trend.club_name} ${trend.label.toLowerCase()}`,
      el('span', { class: 'trend__detail' }, `${fmt(trend.baseline, 1)} → ${fmt(trend.recent, 1)} ${trend.unit}, last 30 days vs the 90 before`),
    ),
  );
}

function homeEmpty() {
  return [
    el(
      'section',
      { class: 'home-hero home-hero--empty' },
      el('div', { class: 'eyebrow' }, 'Welcome'),
      el('h1', { class: 'home-hero__title' }, 'Your range is waiting'),
      el(
        'p',
        { class: 'lede' },
        'Hit a few shots and they land here: every ball on a top-down range, your bag’s real distances, and how you are trending.',
      ),
      noShotsYet(),
    ),
  ];
}

async function homePage() {
  const data = await api('/api/home', { days: homeState.days });
  if (!data.lifetime.shots) return homeEmpty();
  const range = data.range;
  if (homeState.club && !range.clubs.some((club) => club.club === homeState.club)) homeState.club = null;

  const chip = (label, active, onclick) =>
    el('button', { type: 'button', class: active ? 'chip chip--active' : 'chip', 'aria-pressed': String(active), onclick }, label);
  const clubChips = el(
    'div',
    { class: 'chips', role: 'group', 'aria-label': 'Highlight a club' },
    chip('All clubs', !homeState.club, () => { homeState.club = null; render(); }),
    range.clubs.map((club) =>
      chip(clubShort(club.club), homeState.club === club.club, () => {
        homeState.club = homeState.club === club.club ? null : club.club;
        render();
      }),
    ),
  );
  const rangeSelect = el(
    'select',
    { 'aria-label': 'Range', onchange: (event) => { homeState.days = event.target.value; render(); } },
    HOME_RANGES.map((option) => el('option', { value: option.id, selected: option.id === homeState.days }, option.label)),
  );
  const lifetime = data.lifetime;
  const last = data.last_session;

  return [
    el(
      'section',
      { class: 'home-hero' },
      el(
        'div',
        { class: 'home-hero__head' },
        el(
          'div',
          {},
          el('div', { class: 'eyebrow' }, 'Range view'),
          el('h1', { class: 'home-hero__title' }, 'Every ball, where it landed'),
        ),
        el('label', { class: 'field' }, el('span', { class: 'field__label' }, 'Show'), rangeSelect),
      ),
      range.shots.length
        ? rangeChart(range, homeState.club)
        : empty('No shots with a direction reading in this range yet.'),
      clubChips,
      el(
        'p',
        { class: 'home-hero__note' },
        `${fmt(range.shots.length)} shots · yards from the tee · direction from start line only (curve not included)`,
        range.without_direction ? ` · ${range.without_direction} without a direction reading` : '',
      ),
    ),
    el(
      'div',
      { class: 'home-grid' },
      el(
        'section',
        { class: 'card odometer' },
        el('div', { class: 'eyebrow' }, 'Lifetime carry'),
        el('div', { class: 'odometer__value' }, fmt(lifetime.carry_miles, 1), el('span', { class: 'tile__unit' }, 'miles')),
        el(
          'dl',
          { class: 'odometer__facts' },
          el('div', {}, el('dt', {}, 'Shots'), el('dd', {}, fmt(lifetime.shots))),
          el('div', {}, el('dt', {}, 'Sessions'), el('dd', {}, fmt(lifetime.sessions))),
          el('div', {}, el('dt', {}, 'Practice days'), el('dd', {}, fmt(lifetime.practice_days))),
        ),
        el('div', { class: 'tile__note' }, `Since ${fmtDate(lifetime.first_shot)}`),
      ),
      last
        ? el(
            'a',
            { class: 'card card--link last-session', href: `#/sessions/${encodeURIComponent(last.id)}` },
            el('div', { class: 'eyebrow' }, 'Last session'),
            el('div', { class: 'last-session__date' }, fmtDate(last.started_at, true)),
            el('div', { class: 'last-session__line' }, `${last.shots} shots · ${last.clubs.join(', ')}`),
            el(
              'div',
              { class: 'last-session__stat' },
              el('span', { class: 'eyebrow' }, 'Fastest ball'),
              el('span', { class: 'last-session__value' }, fmt(last.top_ball_speed, 1), el('span', { class: 'tile__unit' }, 'mph')),
            ),
            el('span', { class: 'last-session__more' }, 'See every shot →'),
          )
        : null,
      el(
        'section',
        { class: 'card' },
        el('div', { class: 'eyebrow' }, 'Trending'),
        data.trends.length
          ? el('ul', { class: 'trends' }, data.trends.map(trendRow))
          : el('p', { class: 'muted' }, 'Keep practising: after a month with the same clubs, changes in your speed and carry show up here.'),
      ),
    ),
    el(
      'section',
      { class: 'home-section' },
      el('div', { class: 'home-section__head' }, el('h2', {}, 'Your bag'), el('a', { class: 'back', href: '#/gapping' }, 'Gapping →')),
      el(
        'div',
        { class: 'bag' },
        data.bag.map((club) =>
          el(
            'button',
            {
              type: 'button',
              class: homeState.club === club.club ? 'bag__club bag__club--active' : 'bag__club',
              'aria-pressed': String(homeState.club === club.club),
              onclick: () => {
                homeState.club = homeState.club === club.club ? null : club.club;
                render();
              },
            },
            el('span', { class: 'bag__short' }, clubShort(club.club)),
            el('span', { class: 'bag__name' }, club.club_name),
            el('span', { class: 'bag__carry' }, fmt(club.median), el('span', { class: 'tile__unit' }, 'yd')),
            el(
              'span',
              { class: 'bag__meta' },
              club.spread === null ? `${club.shots} shot` : `±${club.spread} yd · longest ${club.longest}`,
            ),
          ),
        ),
      ),
    ),
    el(
      'div',
      { class: 'home-grid home-grid--two' },
      el(
        'section',
        { class: 'card' },
        el('div', { class: 'eyebrow' }, 'Practice calendar'),
        el('div', { class: 'calendar-wrap' }, calendarChart(data.calendar)),
        el(
          'div',
          { class: 'legend' },
          'Fewer shots',
          CALENDAR_RAMP.map((color) => el('span', { class: 'legend__key legend__key--cell', style: `background:${color}` })),
          'More',
        ),
      ),
      el(
        'section',
        { class: 'card' },
        el('div', { class: 'home-section__head' }, el('div', { class: 'eyebrow' }, 'Latest personal bests'), el('a', { class: 'back', href: '#/records' }, 'All →')),
        el(
          'ul',
          { class: 'bests' },
          data.recent_bests.map((best) =>
            el(
              'li',
              {},
              el('a', { href: `#/sessions/${encodeURIComponent(best.session_id)}` },
                el('span', { class: 'bests__value' }, `${fmt(best.value, best.unit === 'yd' ? 0 : 1)} ${best.unit}`),
                el('span', { class: 'bests__what' }, `${best.club_name} · ${best.label.toLowerCase()}`),
                el('span', { class: 'bests__date' }, fmtShortDate(best.timestamp)),
              ),
            ),
          ),
        ),
      ),
    ),
  ];
}
