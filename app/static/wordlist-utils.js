(function (globalScope) {
  const DEFAULT_THRESHOLD = 50000;
  const MAX_SUPPORTED_WORDS = 50000;
  const ALLOWED_WORD_RE = /^[a-z0-9-]+$/;

  function normalizeLine(line) {
    return String(line || "").trim().toLowerCase();
  }

  function parseWordlistLines(rawText) {
    const words = [];
    const rawLines = String(rawText || "").split(/\r?\n/);

    for (const line of rawLines) {
      const normalized = normalizeLine(line);
      if (!normalized || normalized.startsWith("#")) {
        continue;
      }
      words.push(normalized);
    }

    const dedupedWords = dedupeWords(words);
    return {
      words,
      dedupedWords,
      totalLines: words.length,
      uniqueCount: dedupedWords.length,
      filteredOutCount: words.length - dedupedWords.length,
      usableCount: dedupedWords.length,
    };
  }

  function dedupeWords(words) {
    const seen = new Set();
    const result = [];
    for (const word of words) {
      if (seen.has(word)) {
        continue;
      }
      seen.add(word);
      result.push(word);
    }
    return result;
  }

  function applyCleanup(words, options) {
    const opts = options || {};
    const minLen = Number.isFinite(opts.minLen) ? opts.minLen : null;
    const maxLen = Number.isFinite(opts.maxLen) ? opts.maxLen : null;
    const includeText = normalizeLine(opts.includeText || "");
    const excludeText = normalizeLine(opts.excludeText || "");
    const allowedCharsOnly = Boolean(opts.allowedCharsOnly);

    return words.filter((word) => {
      if (minLen !== null && word.length < minLen) {
        return false;
      }
      if (maxLen !== null && word.length > maxLen) {
        return false;
      }
      if (includeText && !word.includes(includeText)) {
        return false;
      }
      if (excludeText && word.includes(excludeText)) {
        return false;
      }
      if (allowedCharsOnly && !ALLOWED_WORD_RE.test(word)) {
        return false;
      }
      return true;
    });
  }

  function sortWords(words, mode) {
    const sorted = words.slice();
    if (mode === "za") {
      sorted.sort((a, b) => b.localeCompare(a));
    } else {
      sorted.sort((a, b) => a.localeCompare(b));
    }
    return sorted;
  }

  function removeByIndices(words, indices) {
    const blocked = indices instanceof Set ? indices : new Set(indices || []);
    return words.filter((_word, index) => !blocked.has(index));
  }

  function wordsToText(words) {
    return words.join("\n");
  }

  function parseUploadText(rawText) {
    return parseWordlistLines(rawText).dedupedWords;
  }

  function isPerformanceSafeMode(wordCount, threshold) {
    const cap = Number.isFinite(threshold) ? threshold : DEFAULT_THRESHOLD;
    return wordCount > cap;
  }

  function isOverWordLimit(wordCount, maxWords) {
    const cap = Number.isFinite(maxWords) ? maxWords : MAX_SUPPORTED_WORDS;
    return wordCount > cap;
  }

  function getSearchToggleConfig(scanState) {
    if (scanState === "running") {
      return { label: "Stop Search", disabled: false, mode: "stop" };
    }
    if (scanState === "cancelling") {
      return { label: "Stopping...", disabled: true, mode: "stopping" };
    }
    return { label: "Start Search", disabled: false, mode: "start" };
  }

  const api = {
    DEFAULT_THRESHOLD,
    MAX_SUPPORTED_WORDS,
    ALLOWED_WORD_RE,
    normalizeLine,
    parseWordlistLines,
    dedupeWords,
    applyCleanup,
    sortWords,
    removeByIndices,
    wordsToText,
    parseUploadText,
    isPerformanceSafeMode,
    isOverWordLimit,
    getSearchToggleConfig,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }

  globalScope.WordlistUtils = api;
})(typeof window !== "undefined" ? window : globalThis);
