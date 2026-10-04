import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { UpdateDialog } from './UpdateDialog';
import type { UpdateStatus } from '../types/socket';

const noop = () => {};

const available: UpdateStatus = {
  enabled: true,
  state: 'available',
  current: 'aaaaaaa',
  latest: 'bbbbbbb',
  behind: 7,
  commits: [
    { sha: 'bbbbbbb', subject: 'Fix carry for wedges' },
    { sha: 'ccccccc', subject: 'Faster rolling buffer' },
  ],
  can_apply: true,
  restart: 'systemd',
};

function render(status: UpdateStatus, error: string | null = null) {
  return renderToString(<UpdateDialog status={status} error={error} onConfirm={noop} onClose={noop} />).replace(
    /<!-- -->/g,
    ''
  );
}

describe('UpdateDialog', () => {
  it('asks before installing and lists what changes', () => {
    const html = render(available);
    expect(html).toContain('Install update?');
    expect(html).toContain('aaaaaaa → bbbbbbb');
    expect(html).toContain('Changes: 7');
    expect(html).toContain('<li>Fix carry for wedges</li>');
    expect(html).toContain('<li>…</li>'); // 7 changes, 2 listed subjects shown, more exist
    expect(html).toContain('>Update now</button>');
    expect(html).toContain('>Not now</button>');
  });

  it('shows why the server refused', () => {
    const html = render(available, 'A shot is being processed; try again in a few seconds');
    expect(html).toContain('A shot is being processed');
  });

  it('never offers the install button to a phone', () => {
    const html = render({ ...available, can_apply: false });
    expect(html).not.toContain('Update now');
    expect(html).toContain('Update available');
  });

  it('shows progress with the translated step and no buttons', () => {
    const html = render({ ...available, state: 'updating', step: 'Building the interface' });
    expect(html).toContain('Updating Copperline…');
    expect(html).toContain('Building the interface');
    expect(html).not.toContain('<button');
  });

  it('explains the restart for systemd and manual launches', () => {
    expect(render({ ...available, state: 'restarting' })).toContain('comes back by itself');
    expect(render({ ...available, state: 'restarting', restart: 'manual' })).toContain(
      'Relaunch Copperline from the desktop'
    );
  });

  it('reports a failure and whether the rollback worked', () => {
    const failed = { ...available, state: 'failed' as const, error: 'npm run build failed (exit 1)' };
    const rolledBack = render({ ...failed, rolled_back: true });
    expect(rolledBack).toContain('Update failed');
    expect(rolledBack).toContain('npm run build failed');
    expect(rolledBack).toContain('previous version was restored');
    expect(render({ ...failed, rolled_back: false })).toContain('could not be fully restored');
  });

  it('says up to date when there is nothing to install', () => {
    expect(render({ ...available, state: 'up_to_date' })).toContain('Copperline is up to date.');
  });
});
