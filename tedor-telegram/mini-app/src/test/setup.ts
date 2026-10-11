/**
 * Test setup: jsdom does not implement these, and both the client and the
 * components rely on them.
 */

import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, vi } from "vitest";

// jsdom has no window.scrollIntoView; the form calls it to bring an error
// into view.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = vi.fn();
}

// The Telegram SDK script is not loaded in tests. Every test sets up the shape
// it needs explicitly, so the global starts empty rather than half-populated.
beforeEach(() => {
  window.sessionStorage.clear();
  delete window.Telegram;
});

afterEach(() => {
  vi.restoreAllMocks();
});