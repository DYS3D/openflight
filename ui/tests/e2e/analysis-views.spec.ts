import { expect, test, type Locator, type Page } from '@playwright/test';
import { gotoApp, KIOSK_VIEWPORTS } from './helpers';

const FIXTURE = '/tests/e2e/fixtures/analysis-views.html';

async function dragHorizontally(page: Page, target: Locator) {
  const box = await target.boundingBox();
  expect(box).toBeTruthy();
  await page.mouse.move(box!.x + box!.width - 40, box!.y + box!.height / 2);
  await page.mouse.down();
  await page.mouse.move(box!.x + 40, box!.y + box!.height / 2, { steps: 12 });
  await page.mouse.up();
}

test('drags the dispersion legend without highlighting a club', async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 480 });
  await gotoApp(page, `${FIXTURE}?view=dispersion`);

  const legend = page.getByRole('group', { name: 'Highlight a club' });
  await expect(legend.getByRole('button')).toHaveCount(14);
  await dragHorizontally(page, legend);

  await expect.poll(() => legend.evaluate((node) => node.scrollLeft)).toBeGreaterThan(0);
  await expect(legend.locator('[aria-pressed="true"]')).toHaveCount(0);
  await expect(page.locator('.dispersion-chart__group--dimmed')).toHaveCount(0);
});

test('drags the gapping list on a short kiosk', async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 400 });
  await gotoApp(page, `${FIXTURE}?view=gapping`);

  const list = page.getByRole('list', { name: 'Gapping' });
  await expect(list.getByRole('listitem')).toHaveCount(14);
  const box = await list.boundingBox();
  expect(box).toBeTruthy();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height - 10);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + 10, { steps: 12 });
  await page.mouse.up();

  await expect.poll(() => list.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
});

test.describe('dispersion legend on a touchscreen', () => {
  test.use({ hasTouch: true, viewport: { width: 800, height: 480 } });

  test('taps a club to highlight it and taps again to clear', async ({ page }) => {
    await gotoApp(page, `${FIXTURE}?view=dispersion`);
    const chip = page.getByRole('group', { name: 'Highlight a club' }).getByRole('button', { name: /^driver/ });

    await chip.tap();
    await expect(chip).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('.dispersion-chart__group--dimmed')).toHaveCount(13);

    await chip.tap();
    await expect(chip).toHaveAttribute('aria-pressed', 'false');
    await expect(page.locator('.dispersion-chart__group--dimmed')).toHaveCount(0);
  });
});

for (const viewport of KIOSK_VIEWPORTS) {
  test(`fits both flight views and finger-sized controls at ${viewport.width}×${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await gotoApp(page, `${FIXTURE}?view=flight`);

    const charts = page.locator('.flight-chart');
    await expect(charts).toHaveCount(2);
    const panel = await page.locator('.stats-panel').boundingBox();
    for (const chart of await charts.all()) {
      const box = await chart.boundingBox();
      expect(box!.height).toBeGreaterThan(70);
      expect(box!.y + box!.height).toBeLessThanOrEqual(panel!.y + panel!.height + 0.5);
    }
    for (const button of await page.getByRole('group', { name: 'Stats view' }).getByRole('button').all()) {
      const box = await button.boundingBox();
      expect(box!.height).toBeGreaterThanOrEqual(44);
      expect(box!.width).toBeGreaterThanOrEqual(44);
    }
  });
}

for (const viewport of [
  { width: 1920, height: 1080 },
  { width: 1280, height: 720 },
]) {
  test(`lays the TV display out without overflow at ${viewport.width}×${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await gotoApp(page, `${FIXTURE}?view=tv`);

    const display = page.getByRole('main', { name: 'TV display' });
    await expect(display.locator('.flight-chart__latest')).toHaveCount(1);
    await expect(display.locator('.tv-display__shot')).toHaveCount(5);

    const overflow = await display.evaluate((root) => {
      const pageOverflows = root.scrollHeight > root.clientHeight + 1 || root.scrollWidth > root.clientWidth + 1;
      const widthOverflows = [
        ...root.querySelectorAll<HTMLElement>('.tv-display__hero, .tv-display__shot, .chart-legend__item'),
      ]
        .filter((element) => element.scrollWidth > element.clientWidth + 1)
        .map((element) => element.className);
      return pageOverflows ? ['tv-display', ...widthOverflows] : widthOverflows;
    });
    expect(overflow).toEqual([]);
  });
}
