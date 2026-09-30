import { test, type Page } from '@playwright/test';
import { expect, gotoApp, resetSession, setClub, waitForEvent, withControlSocket } from './helpers';

async function dismissPicker(page: Page) {
  await page.getByRole('button', { name: 'Close Select club' }).click();
}

async function storeDisplayPreferences(page: Page, preferences: Record<string, boolean>) {
  await page.addInitScript((value) => {
    window.localStorage.setItem('openflight.display-preferences', value);
  }, JSON.stringify(preferences));
}

test.beforeEach(async () => {
  await withControlSocket(async (socket) => {
    await resetSession(socket);
    await setClub(socket, 'driver');
  });
});

test('adds nothing after a shot while display preferences are at their defaults', async ({ page }) => {
  await gotoApp(page);
  await dismissPicker(page);

  await page.getByRole('button', { name: 'Simulate shot' }).click();

  await expect(page.locator('.live-panel__grid .metric-card')).toHaveCount(10);
  await expect(page.locator('.post-shot-takeover')).toHaveCount(0);
  await expect(page.locator('[class*="metric-card--consistency-"]')).toHaveCount(0);
});

test('shows the hero metric full screen after a shot and taps it away', async ({ page }) => {
  await storeDisplayPreferences(page, { bigNumberAfterShot: true });
  await gotoApp(page);
  await dismissPicker(page);

  await page.getByRole('button', { name: 'Simulate shot' }).click();

  const takeover = page.locator('.post-shot-takeover');
  await expect(takeover).toBeVisible();
  await expect(takeover.locator('.post-shot-takeover__label')).toHaveText('Ball speed');
  await expect(takeover.locator('.post-shot-takeover__unit')).toHaveText('mph');

  await takeover.click();
  await expect(takeover).toHaveCount(0);
  await expect(page.locator('.live-panel__grid .metric-card')).toHaveCount(10);
});

test('clears the big number by itself', async ({ page }) => {
  await storeDisplayPreferences(page, { bigNumberAfterShot: true });
  await gotoApp(page);
  await dismissPicker(page);

  await page.getByRole('button', { name: 'Simulate shot' }).click();

  await expect(page.locator('.post-shot-takeover')).toBeVisible();
  await expect(page.locator('.post-shot-takeover')).toHaveCount(0, { timeout: 5_000 });
});

test('switches golfer from the Live header', async ({ page }) => {
  await withControlSocket(async (socket) => {
    const added = waitForEvent(socket, 'profiles');
    socket.emit('add_profile', { name: 'Alex' });
    await added;
  });

  await gotoApp(page);
  await dismissPicker(page);

  const header = page.locator('.panel-header');
  await header.getByRole('button', { name: 'Switch golfer (Alex)' }).click();

  const picker = page.getByRole('dialog', { name: 'Select golfer' });
  await expect(picker).toBeVisible();
  await picker.getByRole('button', { name: 'Profile 1', exact: true }).click();

  await expect(picker).toHaveCount(0);
  await expect(header.getByRole('button', { name: 'Switch golfer (Profile 1)' })).toBeVisible();
  await expect(page.locator('.panel-header__title')).toHaveText('Live');
});

test.describe('display switches on an 800×400 menu sheet', () => {
  test.use({ viewport: { width: 800, height: 400 } });

  test('drag-scrolls the sheet without flipping the switch under the finger', async ({ page }) => {
    await gotoApp(page);
    await dismissPicker(page);
    await page.getByRole('button', { name: 'Open menu' }).click();

    const sheet = page.getByRole('dialog', { name: 'Menu' });
    const voice = sheet.getByRole('switch', { name: 'Voice callout' });
    await expect(voice).toHaveAttribute('aria-checked', 'false');
    const before = await sheet.evaluate((node) => node.scrollTop);
    const box = await voice.boundingBox();
    expect(box).toBeTruthy();

    await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
    await page.mouse.down();
    await page.mouse.move(box!.x + box!.width / 2, box!.y - 120, { steps: 12 });
    await page.mouse.up();

    await expect.poll(async () => sheet.evaluate((node) => node.scrollTop)).toBeGreaterThan(before);
    await expect(voice).toHaveAttribute('aria-checked', 'false');
  });
});

test.describe('display switches on a touchscreen', () => {
  test.use({ hasTouch: true, viewport: { width: 800, height: 400 } });

  test('taps a switch on and remembers it for this browser', async ({ page }) => {
    await gotoApp(page);
    await dismissPicker(page);
    await page.getByRole('button', { name: 'Open menu' }).click();

    const voice = page.getByRole('dialog', { name: 'Menu' }).getByRole('switch', { name: 'Voice callout' });
    await voice.tap();
    await expect(voice).toHaveAttribute('aria-checked', 'true');

    await page.reload();
    await dismissPicker(page);
    await page.getByRole('button', { name: 'Open menu' }).click();
    await expect(
      page.getByRole('dialog', { name: 'Menu' }).getByRole('switch', { name: 'Voice callout' })
    ).toHaveAttribute('aria-checked', 'true');
  });
});
