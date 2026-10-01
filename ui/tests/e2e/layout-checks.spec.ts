import { test, type Page } from '@playwright/test';
import { expect, gotoApp, resetSession, setClub, simulateShot, withControlSocket } from './helpers';

const FIXTURE = '/tests/e2e/fixtures/analysis-views.html';
const SHORT_KIOSK = { width: 800, height: 400 };
const TV_VIEWPORTS = [
  { width: 1280, height: 720 },
  { width: 1920, height: 1080 },
];

async function seedBrowser(page: Page, theme: 'dark' | 'light', preferences: Record<string, boolean>) {
  await page.addInitScript(
    ({ theme, preferences }) => {
      window.localStorage.setItem('openflight.theme', theme);
      window.localStorage.setItem('openflight.display-preferences', JSON.stringify(preferences));
    },
    { theme, preferences }
  );
}

/** Controls and headings whose text is cut off, plus any horizontal page scroll. */
async function layoutDefects(page: Page, screen: string): Promise<string[]> {
  await page.evaluate(() => document.fonts.ready);
  return page.evaluate((screenName) => {
    const defects: string[] = [];
    const root = document.documentElement;
    if (root.scrollWidth > root.clientWidth + 1) defects.push(`${screenName}: page scrolls horizontally`);
    const selector =
      'button, [role=switch], select, .panel-header__title, .stats-gapping__columns > span, .practice__label, .practice-stepper__label';
    // Live tiles have their own layout spec (live-metrics-layout); items inside a drag scroller may sit off screen.
    const scroller = (el: HTMLElement) =>
      [
        ...(function* walk(node: HTMLElement | null) {
          for (let current = node; current; current = current.parentElement) yield current;
        })(el.parentElement),
      ].find((node) => node.scrollWidth > node.clientWidth + 1 && node !== root);
    for (const el of document.querySelectorAll<HTMLElement>(selector)) {
      if (el.closest('.live-panel__grid')) continue;
      if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) continue;
      const text = (el.getAttribute('aria-label') ?? el.textContent ?? '').trim().slice(0, 40);
      const offScreen = rect.right > window.innerWidth + 1 || rect.left < -1;
      if (offScreen && !scroller(el)) defects.push(`${screenName}: "${text}" off screen`);
      if (el.scrollWidth > el.clientWidth + 1) defects.push(`${screenName}: "${text}" clipped`);
    }
    return defects;
  }, screen);
}

test.describe('kiosk views on an 800×400 kiosk', () => {
  test.use({ viewport: SHORT_KIOSK });

  test.beforeEach(async () => {
    await withControlSocket(async (socket) => {
      await resetSession(socket);
      await setClub(socket, 'driver');
      await simulateShot(socket);
    });
  });

  test('fits the new kiosk views without clipping', async ({ page }) => {
    await seedBrowser(page, 'dark', {
      bigNumberAfterShot: true,
      consistencyColors: true,
      showNormalizedCarry: true,
      moreMetrics: true,
    });
    const defects: string[] = [];
    await gotoApp(page);
    await page.locator('.picker-overlay__close').click();
    await expect(page.locator('.live-panel__grid .metric-card')).toHaveCount(10);
    await expect(page.locator('.live-panel__derived .metric-card').first()).toBeVisible();
    defects.push(...(await layoutDefects(page, 'live')));

    await page.locator('.simulate-bubble').click();
    const takeover = page.locator('.post-shot-takeover');
    await expect(takeover).toBeVisible();
    defects.push(...(await layoutDefects(page, 'takeover')));
    await takeover.click();

    await page.locator('.panel-footer__menu').click();
    const sheet = page.locator('.menu-sheet');
    await expect(sheet).toBeVisible();
    await expect(sheet.getByRole('switch')).toHaveCount(5);
    await expect(sheet.locator('select')).toHaveCount(0);
    defects.push(...(await layoutDefects(page, 'menu')));

    await sheet.locator('.menu-sheet__practice').first().click();
    await expect(page.locator('.practice-panel')).toBeVisible();
    defects.push(...(await layoutDefects(page, 'practice target')));
    await page.locator('.practice__mode button').nth(1).click();
    defects.push(...(await layoutDefects(page, 'practice ladder')));

    for (const view of ['flight', 'dispersion', 'gapping', 'level']) {
      await gotoApp(page, `${FIXTURE}?view=${view}`);
      await expect(page.locator('.panel-header__title')).toBeVisible();
      defects.push(...(await layoutDefects(page, view)));
    }

    expect(defects).toEqual([]);
  });
});

for (const viewport of TV_VIEWPORTS) {
  for (const shotCount of [0, 1, 30]) {
    test(`renders /display and the TV layout at ${viewport.width}×${viewport.height} with ${shotCount} shots`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      await withControlSocket(async (socket) => {
        await resetSession(socket);
        await setClub(socket, '7-iron');
        for (let index = 0; index < shotCount; index += 1) await simulateShot(socket);
      });
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(error.message));

      for (const path of ['/display', '/display?layout=tv']) {
        await gotoApp(page, path);
        const root = page.locator(path.includes('tv') ? '.tv-display' : '.display-mode');
        await expect(root).toBeVisible();
        if (path.includes('tv')) {
          await expect(root.locator('.tv-display__shot')).toHaveCount(Math.min(shotCount, 5));
          await expect(root.locator('.flight-chart__latest')).toHaveCount(shotCount > 0 ? 1 : 0);
          await expect(root.locator('.dispersion-chart .dispersion-chart__marker')).toHaveCount(shotCount);
        }
        const text = await page.locator('body').innerText();
        expect(text, `${path} shows a non-number`).not.toMatch(/NaN|Infinity|undefined/);
        // The default /display page already scrolls vertically on a 16:9 TV; the TV layout must not.
        const overflow = await page.evaluate((fullScreen) => {
          const root = document.documentElement;
          return root.scrollWidth > root.clientWidth + 1 || (fullScreen && root.scrollHeight > root.clientHeight + 1);
        }, path.includes('tv'));
        expect(overflow, `${path} overflows the screen`).toBe(false);
      }
      expect(errors).toEqual([]);
    });
  }
}

interface Rgba {
  r: number;
  g: number;
  b: number;
  a: number;
}

test.describe('light theme contrast', () => {
  test.use({ viewport: { width: 800, height: 480 } });

  test('consistency colours and chart marks stand out from their backgrounds', async ({ page }) => {
    await withControlSocket(async (socket) => {
      await resetSession(socket);
      await setClub(socket, '7-iron');
      for (let index = 0; index < 6; index += 1) await simulateShot(socket);
    });
    await seedBrowser(page, 'light', { consistencyColors: true });

    // Parses `rgb()` / `rgba()`, walks up to the first opaque ancestor background, and returns the WCAG ratio.
    const contrastScript = ([selector, property]: [string, string]) => {
      const parse = (value: string): Rgba => {
        const match = value.match(/rgba?\(([^)]+)\)/);
        if (!match) return { r: 0, g: 0, b: 0, a: value === 'transparent' ? 0 : 1 };
        const [r, g, b, a = '1'] = match[1].split(/[\s,/]+/).filter(Boolean);
        return { r: Number(r), g: Number(g), b: Number(b), a: Number(a) };
      };
      const luminance = ({ r, g, b }: Rgba) => {
        const channel = (c: number) => {
          const s = c / 255;
          return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
        };
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
      };
      const backgroundOf = (el: Element | null): Rgba => {
        for (let node = el; node; node = node.parentElement) {
          const bg = parse(getComputedStyle(node).backgroundColor);
          if (bg.a > 0.5) return bg;
        }
        return parse(getComputedStyle(document.body).backgroundColor);
      };
      return [...document.querySelectorAll<HTMLElement>(selector)].map((el) => {
        const fg = parse(getComputedStyle(el).getPropertyValue(property));
        const bg = backgroundOf(el);
        const [light, dark] = [luminance(fg), luminance(bg)].sort((a, b) => b - a);
        return { alpha: fg.a, ratio: (light + 0.05) / (dark + 0.05) };
      });
    };

    await gotoApp(page);
    await page.locator('.picker-overlay__close').click();
    const tinted = page.locator('[class*="metric-card--consistency-"] .metric-card__value');
    await expect(tinted.first()).toBeVisible();
    const values = await page.evaluate(contrastScript, [
      '[class*="metric-card--consistency-"] .metric-card__value',
      'color',
    ]);
    expect(values.length).toBeGreaterThan(0);
    for (const value of values) {
      expect(value.alpha).toBe(1);
      expect(value.ratio).toBeGreaterThanOrEqual(3);
    }

    await gotoApp(page, `${FIXTURE}?view=flight&theme=light`);
    await expect(page.locator('.flight-chart__latest')).toHaveCount(2);
    for (const [selector, property] of [
      ['.flight-chart__latest', 'stroke'],
      ['.flight-chart__ghost', 'stroke'],
      ['.chart__tick', 'fill'],
    ] as const) {
      const marks = await page.evaluate(contrastScript, [selector, property]);
      expect(marks.length, selector).toBeGreaterThan(0);
      for (const mark of marks) {
        expect(mark.alpha, selector).toBeGreaterThan(0);
        expect(mark.ratio, selector).toBeGreaterThanOrEqual(3);
      }
    }

    await gotoApp(page, `${FIXTURE}?view=dispersion&theme=light`);
    const markers = await page.evaluate(contrastScript, ['.dispersion-chart .dispersion-chart__marker', 'fill']);
    expect(markers.length).toBe(70);
    // WCAG non-text contrast: every series marker must reach 3:1 on the cream background.
    for (const marker of markers) {
      expect(marker.alpha).toBeGreaterThan(0);
      expect(marker.ratio).toBeGreaterThanOrEqual(3);
    }
  });
});
