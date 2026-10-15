import { createCompanionAdapter as createUpstreamAdapter } from '/tarot/static/platform/upstream-companion-adapter.v1.js';

const FLASH_MODEL = 'gemini-3.5-flash';
const PRO_MODEL = 'gemini-3.1-pro-preview';
const AUTO_MODEL = 'auto';
const MODELS = new Set([AUTO_MODEL, FLASH_MODEL, PRO_MODEL, 'gemini-3.8-flash']);
const USABLE = new Set(['available', 'retry_ready']);
const BLOCKED_MESSAGES = Object.freeze({
  cooling: '解读渠道暂不可用，请稍后重查。',
  probing: '解读渠道正在探测中，请稍后在设置中重查。',
  disabled: '解读渠道暂不可用，请稍后重查。',
  unconfigured: '解读渠道尚未配置，请稍后重查。',
});

function validatedStatus(payload, model) {
  if (!payload || !Array.isArray(payload.models) || payload.models.length !== 1) {
    throw new Error('暂时无法确认渠道状态，请在设置中重查。');
  }
  const item = payload.models.find(entry => entry?.model === model);
  if (!item || typeof item.status !== 'string') {
    throw new Error('暂时无法确认渠道状态，请在设置中重查。');
  }
  return item.status;
}

export function createCompanionAdapter(config, options = {}) {
  const fetchImpl = options.fetchImpl || globalThis.fetch;
  const upstream = createUpstreamAdapter(config, { ...options, fetchImpl });
  if (!upstream) return null;
  const upstreamRead = upstream.read.bind(upstream);

  return {
    ...upstream,
    read: async (body, readOptions) => {
      // Restoring an existing attempt is read-only and must remain available
      // even while its model is cooling or the status bridge is unavailable.
      if (body?.attempt_id !== undefined) {
        return upstreamRead(body, readOptions);
      }
      if (!MODELS.has(body?.model)) {
        throw new Error('本站托管解读仅支持自动渠道');
      }

      let response;
      let payload;
      try {
        response = await fetchImpl('/api/tarot/models/status?routing=auto', {
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { Accept: 'application/json' },
          ...(readOptions?.signal ? { signal: readOptions.signal } : {}),
        });
        payload = await response.json();
      } catch {
        throw new Error('暂时无法确认渠道状态，请在设置中重查。');
      }
      if (!response.ok) {
        throw new Error('暂时无法确认渠道状态，请在设置中重查。');
      }
      const status = validatedStatus(payload, AUTO_MODEL);
      if (!USABLE.has(status)) {
        throw new Error(
          BLOCKED_MESSAGES[status]
          || '解读渠道当前不可用，请稍后在设置中重查。',
        );
      }
      return upstreamRead({ ...body, model: AUTO_MODEL }, readOptions);
    },
  };
}
