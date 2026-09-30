import { test, type Page } from '@playwright/test';
import { expect, gotoApp, KIOSK_VIEWPORTS, resetSession, setClub, simulateShot, withControlSocket } from './helpers';

/** Finger-sized minimum for anything tappable on the kiosk, in CSS px. */
const MIN_TARGET_PX = 44;

/** Everything a finger can act on. Sliders and text inputs count too. */
const INTERACTIVE_SELECTOR = 'button, [role=button], a[href], input, select, [role=tab], [role=switch]';

/**
 * Elements that legitimately cannot reach 44×44. Keep this list short and say
 * why each entry is here; a control that merely *happens* to be small is a bug
 * to fix in CSS, not an exclusion.
 */
const EXCLUSIONS: ReadonlyArray<{ selector: string; reason: string }> = [];

/** Sub-pixel layout rounding (e.g. 43.99px from a vw-based gap) is not a violation. */
const SLOP_PX = 0.5;

interface Undersized {
  screen: string;
  tag: string;
  label: string;
  width: number;
  height: number;
}

/**
 * Every visible, enabled interactive element on the page whose bounding box is
 * under 44×44. Disabled controls are skipped (they are not tappable), as are
 * elements CSS hides or that have no layout box.
 */
async function undersizedTargets(page: Page, screen: string): Promise<Undersized[]> {
  await page.evaluate(() => document.fonts.ready);
  return page.evaluate(
    ({ selector, min, slop, exclusions, screen: screenName }) => {
      const found: Undersized[] = [];
      for (const el of document.querySelectorAll<HTMLElement>(selector)) {
        if (exclusions.some((exclusion) => el.matches(exclusion))) continue;
        if (el instanceof HTMLInputElement && el.type === 'hidden') continue;
        if ((el as HTMLButtonElement | HTMLInputElement | HTMLSelectElement).disabled) continue;
        if (el.getAttribute('aria-disabled') === 'true') continue;
        if (el.closest('[inert]')) continue;
        if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) continue;

        const rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) continue;
        if (rect.width + slop >= min && rect.height + slop >= min) continue;

        found.push({
          screen: screenName,
          tag: `${el.tagName.toLowerCase()}${el.className ? `.${String(el.className).trim().split(/\s+/).join('.')}` : ''}`,
          label: el.getAttribute('aria-label') ?? el.textContent?.trim() ?? '',
          width: Math.round(rect.width * 10) / 10,
          height: Math.round(rect.height * 10) / 10,
        });
      }
      return found;
    },
    {
      selector: INTERACTIVE_SELECTOR,
      min: MIN_TARGET_PX,
      slop: SLOP_PX,
      exclusions: EXCLUSIONS.map((exclusion) => exclusion.selector),
      screen,
    }
  );
}

function describeViolations(violations: Undersized[]): string[] {
  return violations.map(
    (violation) =>
      `${violation.screen}: <${violation.tag}> "${violation.label}" is ${violation.width}×${violation.height}`
  );
}

for (const viewport of KIOSK_VIEWPORTS) {
  test.describe(`touch targets at ${viewport.width}×${viewport.height}`, () => {
    test.use({ hasTouch: true, viewport });

    test.beforeEach(async () => {
      await withControlSocket(async (socket) => {
        await resetSession(socket);
        await setClub(socket, 'driver');
        await simulateShot(socket);
        await setClub(socket, '7-iron');
        await simulateShot(socket);
      });
    });

    test('every tappable control is at least 44×44 on each panel and overlay', async ({ page }) => {
      const violations: Undersized[] = [];
      const check = async (screen: string) => {
        violations.push(...(await undersizedTargets(page, screen)));
      };

      await gotoApp(page);

      // Club picker opens on every load: header close, group tabs, option grid.
      const picker = page.getByRole('dialog', { name: 'Select club' });
      await expect(picker).toBeVisible();
      await check('club picker (woods)');
      await picker.getByRole('button', { name: 'Irons' }).click();
      await check('club picker (irons)');
      await page.getByRole('button', { name: 'Close Select club' }).click();
      await expect(picker).toHaveCount(0);

      // Live: header actions, ten metric tiles, footer, simulate bubble.
      await expect(page.locator('.live-panel__grid .metric-card')).toHaveCount(10);
      await check('live');

      // Header status menu (LED + title tap).
      await page.getByLabel('Server connected').click();
      await expect(page.getByRole('dialog', { name: 'System status' })).toBeVisible();
      await check('status menu');
      await page.getByLabel('Close status').click();

      // Footer menu sheet: segmented controls, language select, shut down.
      await page.getByRole('button', { name: 'Open menu' }).click();
      await expect(page.getByRole('dialog', { name: 'Menu' })).toBeVisible();
      await check('menu sheet');
      // The scrim spans the app; its centre sits under the sheet on 800-wide screens.
      await page.getByRole('button', { name: 'Close menu' }).click({ position: { x: 8, y: 8 } });

      // Shutdown confirmation.
      await page.locator('.panel-footer').getByRole('button', { name: 'Shut down' }).click();
      await expect(page.getByRole('dialog', { name: 'Shut down OpenFlight?' })).toBeVisible();
      await check('shutdown dialog');
      await page.getByRole('button', { name: 'Cancel' }).click();

      // Stats: club filter chips and the Clear session flow.
      await page.getByRole('button', { name: 'Stats' }).click();
      await expect(page.getByRole('group', { name: 'Filter by club' })).toBeVisible();
      await check('stats');
      await page.locator('.panel-header').getByRole('button', { name: 'Clear session' }).click();
      await expect(page.getByRole('dialog', { name: "Clear Profile 1's session?" })).toBeVisible();
      await check('clear session dialog');
      await page
        .getByRole('dialog', { name: "Clear Profile 1's session?" })
        .getByRole('button', { name: 'Cancel' })
        .click();

      // Stats sub-views: header switch, dispersion legend chips, mishit toggle.
      const statsViews = page.getByRole('group', { name: 'Stats view' });
      for (const view of ['Flight', 'Dispersion', 'Gapping']) {
        await statsViews.getByRole('button', { name: view, exact: true }).click();
        await expect(statsViews.getByRole('button', { name: view, exact: true })).toHaveAttribute(
          'aria-pressed',
          'true'
        );
        await check(`stats (${view.toLowerCase()})`);
      }
      await statsViews.getByRole('button', { name: 'Summary', exact: true }).click();

      // Shots: row buttons, delete, and the expanded validation editor.
      await page.getByRole('button', { name: 'Shots' }).click();
      await expect(page.locator('.shots-panel__row')).toHaveCount(2);
      await check('shots');
      await page.locator('.shots-panel__row-main').first().click();
      await expect(page.locator('.shots-panel__validation')).toBeVisible();
      await check('shots (row expanded)');

      // Camera: mock backend has no camera, so this is the unavailable state.
      await page.getByRole('button', { name: 'Camera' }).click();
      await expect(page.getByRole('heading', { name: 'Camera Not Available' })).toBeVisible();
      await check('camera');

      // Profiles: add a second profile through the on-screen keyboard so the
      // roster shows rename and remove controls.
      await page.getByRole('button', { name: 'Profiles' }).click();
      await expect(page.getByRole('region', { name: 'Profiles' })).toBeVisible();
      await check('profiles');
      await page.locator('.panel-header').getByRole('button', { name: 'Add profile' }).click();
      const nameDialog = page.getByRole('dialog', { name: 'Add profile' });
      await expect(nameDialog).toBeVisible();
      await check('add profile (letters)');
      await nameDialog.getByRole('button', { name: 'Numbers' }).click();
      await check('add profile (numbers)');
      await nameDialog.getByRole('button', { name: 'Letters' }).click();
      for (const key of ['A', 'L', 'E', 'X']) {
        await nameDialog.getByRole('button', { name: key, exact: true }).click();
      }
      await nameDialog.getByRole('button', { name: 'Add profile', exact: true }).click();
      await expect(nameDialog).toHaveCount(0);
      // The new profile is active; select the original so "Alex" becomes removable.
      await page.locator('.profiles-panel__card').filter({ hasText: 'Profile 1' }).click();
      await page.getByRole('button', { name: 'Profiles' }).click();
      await expect(page.getByLabel('Remove Alex')).toBeVisible();
      await check('profiles (two profiles)');

      // Debug: sub-tabs plus the Record header action.
      await page.getByRole('button', { name: 'Debug' }).click();
      await expect(page.getByRole('heading', { name: 'System Status' })).toBeVisible();
      await check('debug (status)');
      await page.getByRole('button', { name: 'Tuning' }).click();
      await expect(page.getByRole('heading', { name: 'Radar Tuning' })).toBeVisible();
      await check('debug (tuning)');

      expect(describeViolations(violations)).toEqual([]);
    });
  });
}
