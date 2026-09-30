import { test, type Page } from '@playwright/test';
import { expect, gotoApp, resetSession, setClub, simulateShot, waitForEvent, withControlSocket } from './helpers';

async function dismissPicker(page: Page) {
  await page.getByRole('button', { name: 'Close Select club' }).click();
}

async function storeDisplayPreferences(page: Page, preferences: Record<string, boolean>) {
  await page.addInitScript((value) => {
    window.localStorage.setItem('openflight.display-preferences', value);
  }, JSON.stringify(preferences));
}

/** Records the most takeovers ever mounted at once, so a race cannot hide between assertions. */
async function trackTakeovers(page: Page) {
  await page.addInitScript(() => {
    const w = window as Window & { __takeoverMax?: number };
    w.__takeoverMax = 0;
    new MutationObserver(() => {
      w.__takeoverMax = Math.max(w.__takeoverMax ?? 0, document.querySelectorAll('.post-shot-takeover').length);
    }).observe(document, { childList: true, subtree: true });
  });
}

function collectPageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  return errors;
}

test.beforeEach(async () => {
  await withControlSocket(async (socket) => {
    await resetSession(socket);
    await setClub(socket, 'driver');
  });
});

test('five rapid shots show one takeover at a time and none gets stuck', async ({ page }) => {
  await storeDisplayPreferences(page, { bigNumberAfterShot: true, voiceCallout: true });
  await trackTakeovers(page);
  const errors = collectPageErrors(page);
  await gotoApp(page);
  await dismissPicker(page);

  const bubble = page.locator('.simulate-bubble');
  const shotsSeen = withControlSocket(async (socket) => {
    for (let index = 0; index < 5; index += 1) await waitForEvent(socket, 'shot');
  });
  // dispatchEvent bypasses the takeover covering the bubble, like a finger mashing before it appears.
  for (let index = 0; index < 5; index += 1) {
    await bubble.dispatchEvent('click');
    await page.waitForTimeout(50);
  }
  await shotsSeen;

  await expect(page.locator('.post-shot-takeover')).toHaveCount(1);
  await expect(page.locator('.post-shot-takeover')).toHaveCount(0, { timeout: 5_000 });
  expect(await page.evaluate(() => (window as Window & { __takeoverMax?: number }).__takeoverMax)).toBe(1);
  await expect(page.locator('.live-panel__grid .metric-card')).toHaveCount(10);
  await expect(page.locator('.panel-footer__shot-count, .panel-footer')).toContainText('5');
  expect(errors).toEqual([]);
});

test('keeps working when speechSynthesis throws', async ({ page }) => {
  await storeDisplayPreferences(page, { bigNumberAfterShot: true, voiceCallout: true });
  await page.addInitScript(() => {
    Object.defineProperty(window, 'speechSynthesis', {
      configurable: true,
      value: {
        cancel: () => {
          throw new Error('no voices');
        },
        speak: () => {
          throw new Error('no voices');
        },
      },
    });
  });
  const errors = collectPageErrors(page);
  await gotoApp(page);
  await dismissPicker(page);

  await page.getByRole('button', { name: 'Simulate shot' }).click();
  const takeover = page.locator('.post-shot-takeover');
  await expect(takeover).toBeVisible();
  await takeover.click();
  await expect(takeover).toHaveCount(0);

  await withControlSocket((socket) => simulateShot(socket));
  await expect(takeover).toBeVisible();
  await expect(page.locator('.live-panel__grid .metric-card')).toHaveCount(10);
  expect(errors).toEqual([]);
});

test('switching golfer mid-round starts a fresh Practice round for the new golfer', async ({ page }) => {
  const alexId = await withControlSocket(async (socket) => {
    const added = waitForEvent<{ profiles: Array<{ id: string; name: string }> }>(socket, 'profiles');
    socket.emit('add_profile', { name: 'Alex' });
    const snapshot = await added;
    const alex = snapshot.profiles.find((profile) => profile.name === 'Alex')!;
    // add_profile activates the new profile; play the first round as Profile 1.
    const activated = waitForEvent(socket, 'profiles');
    socket.emit('set_active_profile', { profile_id: snapshot.profiles.find((p) => p.name === 'Profile 1')!.id });
    await activated;
    return alex.id;
  });

  await gotoApp(page);
  await dismissPicker(page);
  await page.getByRole('button', { name: 'Open menu' }).click();
  await page.getByRole('dialog', { name: 'Menu' }).getByRole('button', { name: 'Practice' }).click();
  await expect(page.locator('.panel-header__title')).toHaveText('Practice');

  await withControlSocket(async (socket) => {
    await simulateShot(socket);
    await simulateShot(socket);
  });
  await expect(page.locator('.practice__totals')).toContainText('2 / 10');
  await expect(page.locator('.practice__cell--current')).toHaveCount(1);

  await withControlSocket(async (socket) => {
    const activated = waitForEvent(socket, 'profiles');
    socket.emit('set_active_profile', { profile_id: alexId });
    await activated;
  });
  await expect(page.locator('.panel-header__subtitle')).toHaveText('Alex');
  await expect(page.locator('.practice__totals')).toContainText('0 / 10');
  await expect(page.locator('.practice__cell')).toHaveText(['', '', '', '', '', '', '', '', '', '']);

  await withControlSocket((socket) => simulateShot(socket));
  await expect(page.locator('.practice__totals')).toContainText('1 / 10');
});

test('clearing the session while Dispersion is open leaves no stale chart or highlight', async ({ page }) => {
  await withControlSocket(async (socket) => {
    for (let index = 0; index < 3; index += 1) await simulateShot(socket);
  });
  const errors = collectPageErrors(page);
  await gotoApp(page);
  await dismissPicker(page);

  await page.getByRole('button', { name: 'Stats' }).click();
  await page.getByRole('group', { name: 'Stats view' }).getByRole('button', { name: 'Dispersion' }).click();
  const legend = page.getByRole('group', { name: 'Highlight a club' });
  await legend.getByRole('button', { name: /^driver/ }).click();
  await expect(legend.locator('[aria-pressed="true"]')).toHaveCount(1);

  await page.locator('.panel-header').getByRole('button', { name: 'Clear session' }).click();
  const dialog = page.getByRole('dialog', { name: "Clear Profile 1's session?" });
  await dialog.getByRole('button', { name: 'Clear session' }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.locator('.panel-header__title')).toHaveText('Live');

  await page.getByRole('button', { name: 'Stats' }).click();
  await page.getByRole('group', { name: 'Stats view' }).getByRole('button', { name: 'Dispersion' }).click();
  await expect(page.locator('.panel__empty-title')).toHaveText('No landing data yet');
  await expect(page.locator('.dispersion-chart')).toHaveCount(0);
  await expect(page.locator('[aria-pressed="true"]').filter({ hasText: /driver/ })).toHaveCount(0);

  await withControlSocket((socket) => simulateShot(socket));
  await expect(page.locator('.dispersion-chart .dispersion-chart__marker')).toHaveCount(1);
  await expect(legend.locator('[aria-pressed="true"]')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('the update dialog opens above a showing takeover and closes without disturbing it', async ({ page }) => {
  await storeDisplayPreferences(page, { bigNumberAfterShot: true });
  await gotoApp(page);
  await dismissPicker(page);

  await page.getByRole('button', { name: 'Simulate shot' }).click();
  const takeover = page.locator('.post-shot-takeover');
  await expect(takeover).toBeVisible();

  // A failed update opens the dialog for everyone; push that state through the live store module.
  await page.evaluate(async () => {
    const { useSystemStore } = (await import('/src/stores/useSystemStore.ts')) as {
      useSystemStore: { getState: () => { setUpdateStatus: (status: unknown) => void } };
    };
    useSystemStore.getState().setUpdateStatus({ enabled: true, state: 'failed', can_apply: true, rolled_back: true });
  });
  const dialog = page.getByRole('alertdialog', { name: 'Update failed' });
  await expect(dialog).toBeVisible();
  const closeButton = dialog.getByRole('button', { name: 'Close' });
  const box = await closeButton.boundingBox();
  const topmost = await page.evaluate(
    ([x, y]) => document.elementFromPoint(x, y)?.closest('.shutdown-dialog, .post-shot-takeover')?.className ?? '',
    [box!.x + box!.width / 2, box!.y + box!.height / 2]
  );
  expect(topmost).toContain('shutdown-dialog');

  await closeButton.click();
  await expect(dialog).toHaveCount(0);
  await expect(takeover).toBeVisible();
  await takeover.click();
  await expect(takeover).toHaveCount(0);
  await expect(page.locator('.live-panel__grid .metric-card')).toHaveCount(10);
});
