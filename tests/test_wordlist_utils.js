const test = require("node:test");
const assert = require("node:assert/strict");

const {
  parseWordlistLines,
  applyCleanup,
  dedupeWords,
  sortWords,
  removeByIndices,
  parseUploadText,
  isPerformanceSafeMode,
  getSearchToggleConfig,
} = require("../app/static/wordlist-utils.js");

test("parseWordlistLines normalizes and tracks stats", () => {
  const parsed = parseWordlistLines(" Alpha\n#comment\nBeta\nalpha\n\n");
  assert.deepEqual(parsed.words, ["alpha", "beta", "alpha"]);
  assert.deepEqual(parsed.dedupedWords, ["alpha", "beta"]);
  assert.equal(parsed.totalLines, 3);
  assert.equal(parsed.uniqueCount, 2);
  assert.equal(parsed.filteredOutCount, 1);
  assert.equal(parsed.usableCount, 2);
});

test("applyCleanup handles min max include exclude and allowed chars", () => {
  const words = ["alpha", "a", "beta-2", "bad!", "zzalpha"];
  const result = applyCleanup(words, {
    minLen: 2,
    maxLen: 6,
    includeText: "a",
    excludeText: "zz",
    allowedCharsOnly: true,
  });
  assert.deepEqual(result, ["alpha", "beta-2"]);
});

test("dedupeWords keeps first occurrence", () => {
  assert.deepEqual(dedupeWords(["a", "b", "a", "c", "b"]), ["a", "b", "c"]);
});

test("sortWords handles az and za", () => {
  assert.deepEqual(sortWords(["beta", "alpha"], "az"), ["alpha", "beta"]);
  assert.deepEqual(sortWords(["beta", "alpha"], "za"), ["beta", "alpha"]);
});

test("removeByIndices removes selected rows", () => {
  const removed = removeByIndices(["a", "b", "c", "d"], new Set([1, 3]));
  assert.deepEqual(removed, ["a", "c"]);
});

test("parseUploadText dedupes for upload flow", () => {
  const words = parseUploadText("one\nOne\none\ntwo\n");
  assert.deepEqual(words, ["one", "two"]);
});

test("performance safe mode threshold", () => {
  assert.equal(isPerformanceSafeMode(50000, 50000), false);
  assert.equal(isPerformanceSafeMode(50001, 50000), true);
});

test("search toggle config states", () => {
  assert.deepEqual(getSearchToggleConfig("idle"), { label: "Start Search", disabled: false, mode: "start" });
  assert.deepEqual(getSearchToggleConfig("running"), { label: "Stop Search", disabled: false, mode: "stop" });
  assert.deepEqual(getSearchToggleConfig("cancelling"), {
    label: "Stopping...",
    disabled: true,
    mode: "stopping",
  });
});
