// Read-aloud with the browser's speech synthesis, for hands-free and low-vision use.

export const ttsSupported = "speechSynthesis" in window;

export function plain(text) {
  return String(text || "")
    .replace(/\*\*|__|`/g, "")
    .replace(/^#+\s*/gm, "")
    .replace(/^\s*[-*•]\s+/gm, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/https?:\/\/\S+/g, "link")
    .replace(/\(#\d+\)/g, "");
}

export function speak(text, { lang, onstart, onend } = {}) {
  if (!ttsSupported || !text) return false;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(plain(text));
  if (lang) utterance.lang = lang;
  utterance.onstart = () => onstart?.(utterance);
  utterance.onend = () => onend?.();
  utterance.onerror = () => onend?.();
  window.speechSynthesis.speak(utterance);
  return true;
}

export function stopSpeaking() {
  if (ttsSupported) window.speechSynthesis.cancel();
}

export function speaking() {
  return ttsSupported && window.speechSynthesis.speaking;
}
