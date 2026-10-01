import { afterEach, describe, expect, it, vi } from 'vitest';
import { CALLOUT_LANG, speakCallout, type SpeechEnvironment } from './voiceCallout';

function fakeSpeech(options: { failSpeak?: boolean } = {}) {
  const calls: string[] = [];
  const spoken: Array<{ text: string; lang: string }> = [];
  const speech: SpeechEnvironment = {
    synth: {
      cancel: () => {
        calls.push('cancel');
      },
      speak: (utterance) => {
        if (options.failSpeak) throw new Error('not-allowed');
        calls.push('speak');
        spoken.push({ text: (utterance as unknown as { text: string }).text, lang: utterance.lang });
      },
    },
    createUtterance: (text) => ({ text, lang: '' }) as unknown as SpeechSynthesisUtterance,
  };
  return { speech, calls, spoken };
}

describe('speakCallout', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('speaks the text in US English, cutting off the previous callout', () => {
    const { speech, calls, spoken } = fakeSpeech();

    speakCallout('214 yards', speech);

    expect(CALLOUT_LANG).toBe('en-US');
    expect(calls).toEqual(['cancel', 'speak']);
    expect(spoken).toEqual([{ text: '214 yards', lang: 'en-US' }]);
  });

  it('is a silent no-op when the browser has no speech synthesis', () => {
    vi.stubGlobal('window', {});

    expect(() => speakCallout('214 yards')).not.toThrow();
    expect(() => speakCallout('214 yards', null)).not.toThrow();
  });

  it('is a silent no-op with no window at all', () => {
    vi.stubGlobal('window', undefined);

    expect(() => speakCallout('214 yards')).not.toThrow();
  });

  it('swallows a speech engine failure (e.g. no voices on the Pi)', () => {
    const { speech } = fakeSpeech({ failSpeak: true });

    expect(() => speakCallout('214 yards', speech)).not.toThrow();
  });

  it('uses window.speechSynthesis when available', () => {
    const speak = vi.fn();
    class FakeUtterance {
      lang = '';
      text: string;
      constructor(text: string) {
        this.text = text;
      }
    }
    vi.stubGlobal('window', { speechSynthesis: { speak, cancel: vi.fn() }, SpeechSynthesisUtterance: FakeUtterance });

    speakCallout('92.0 miles per hour');

    expect(speak).toHaveBeenCalledTimes(1);
    expect(speak.mock.calls[0][0]).toMatchObject({ text: '92.0 miles per hour', lang: 'en-US' });
  });
});
