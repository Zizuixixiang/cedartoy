export * from '/tarot/static/platform/managed-core.v1.js';

import { chat as upstreamChat } from '/tarot/static/platform/managed-core.v1.js';

// Upstream redraws the whole reading on every streamed delta and then forces
// the scroller to its bottom. Preserve the reader's current position around
// that callback: a new reading starts at zero, while a person who has scrolled
// manually stays where they chose as later chunks arrive.
export async function chat(options = {}) {
  const onDelta = options.onDelta;
  if (typeof onDelta !== 'function') return upstreamChat(options);
  return upstreamChat({
    ...options,
    onDelta(value) {
      const stream = globalThis.document?.getElementById('readingStream');
      const preserve = Boolean(stream?.classList?.contains('streaming'));
      const scrollTop = preserve ? Number(stream.scrollTop) : null;
      try {
        onDelta(value);
      } finally {
        if (preserve && Number.isFinite(scrollTop)) stream.scrollTop = scrollTop;
      }
    },
  });
}
