/** The kiosk UI ships in English only, so every callout is spoken as US English. */
export const CALLOUT_LANG = 'en-US';

export interface SpeechEnvironment {
  synth: Pick<SpeechSynthesis, 'cancel' | 'speak'>;
  createUtterance: (text: string) => SpeechSynthesisUtterance;
}

function browserSpeech(): SpeechEnvironment | null {
  if (
    typeof window === 'undefined' ||
    !window.speechSynthesis ||
    typeof window.SpeechSynthesisUtterance !== 'function'
  ) {
    return null;
  }

  return {
    synth: window.speechSynthesis,
    createUtterance: (text) => new window.SpeechSynthesisUtterance(text),
  };
}

/** Replaces anything still being spoken, so rapid shots never queue up. */
export function speakCallout(text: string, speech: SpeechEnvironment | null = browserSpeech()): void {
  if (!speech) {
    return;
  }

  try {
    const utterance = speech.createUtterance(text);
    utterance.lang = CALLOUT_LANG;
    speech.synth.cancel();
    speech.synth.speak(utterance);
  } catch {
    // Chromium on a Pi can lack voices or refuse to speak; stay silent.
  }
}

type GestureTarget = Pick<EventTarget, 'addEventListener'>;

const primedTargets = new WeakSet<GestureTarget>();

/**
 * Chromium refuses speechSynthesis.speak() until the page has had a user gesture, and
 * the Pi's speech-dispatcher can drop the first utterance while it starts. A silent
 * utterance spoken inside the first tap unlocks and warms the engine for later shots.
 */
export function primeSpeechOnFirstGesture(
  target: GestureTarget | null = typeof window === 'undefined' ? null : window,
  speech: SpeechEnvironment | null = browserSpeech()
): void {
  if (!target || !speech || primedTargets.has(target)) {
    return;
  }
  primedTargets.add(target);

  const prime = () => {
    try {
      const utterance = speech.createUtterance('');
      utterance.volume = 0;
      speech.synth.speak(utterance);
    } catch {
      // A Pi without voices must not break the tap.
    }
  };
  target.addEventListener('pointerdown', prime, { capture: true, once: true });
}

/** Chromium loads voices asynchronously; re-read this on `voiceschanged`. */
export function installedVoiceCount(): number {
  if (typeof window === 'undefined' || !window.speechSynthesis) {
    return 0;
  }
  return window.speechSynthesis.getVoices().length;
}
