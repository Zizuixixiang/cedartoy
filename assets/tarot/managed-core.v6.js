export * from '/tarot/static/platform/managed-core.v5.js';

import { chat as managedChat } from '/tarot/static/platform/managed-core.v5.js';

const READING_STATE_EVENT = 'cedartoy:tarot-reading-state';
let readingRequestSerial = 0;

function notifyReadingState(detail) {
  const doc = globalThis.document;
  if (!doc?.dispatchEvent || typeof globalThis.CustomEvent !== 'function') return;
  doc.dispatchEvent(new CustomEvent(READING_STATE_EVENT, { detail }));
}

export async function chat(options = {}) {
  const stream = globalThis.document?.getElementById?.('readingStream');
  const isReadingRequest = Boolean(stream?.classList?.contains('streaming'));
  if (!isReadingRequest) return managedChat(options);

  const requestId = ++readingRequestSerial;
  let terminalState = '';
  notifyReadingState({ requestId, state: 'start' });

  try {
    return await managedChat({
      ...options,
      onDelta(value) {
        options.onDelta?.(value);
        notifyReadingState({
          requestId,
          state: 'delta',
          hasText: String(value ?? '').trim().length > 0,
        });
      },
      onError(message) {
        terminalState = 'error';
        options.onError?.(message);
        notifyReadingState({ requestId, state: 'error' });
      },
      onDone(ok) {
        options.onDone?.(ok);
        if (terminalState === 'error') return;
        terminalState = 'done';
        notifyReadingState({ requestId, state: 'done', ok: ok !== false });
      },
    });
  } finally {
    if (!terminalState) {
      terminalState = options.signal?.aborted ? 'cancelled' : 'done';
      notifyReadingState({
        requestId,
        state: terminalState,
        ok: false,
      });
    }
  }
}
