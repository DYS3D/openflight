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
export function speakCallout(text: string, lang: string, speech: SpeechEnvironment | null = browserSpeech()): void {
  if (!speech) {
    return;
  }

  try {
    const utterance = speech.createUtterance(text);
    utterance.lang = lang;
    speech.synth.cancel();
    speech.synth.speak(utterance);
  } catch {
    // Chromium on a Pi can lack voices or refuse to speak; stay silent.
  }
}
