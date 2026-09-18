import { createCompanionAdapter as createUpstreamAdapter } from '/tarot/static/platform/upstream-companion-adapter.v1.js';

const FLASH_MODEL = 'gemini-3.5-flash';
const PRO_MODEL = 'gemini-3.1-pro-preview';
const MODELS = new Set([FLASH_MODEL, PRO_MODEL]);
const USABLE = new Set(['available', 'retry_ready']);
const BLOCKED_MESSAGES = Object.freeze({
  cooling: '所选模型暂不可用，请稍后重查或改选可用模型。',
  probing: '所选模型正在探测中，请稍后在设置中重查。',
  disabled: '所选模型暂不可用，请改选可用模型。',
  unconfigured: '所选模型尚未配置，请改选可用模型。',
});

function validatedStatus(payload, model) {
  if (!payload || !Array.isArray(payload.models) || payload.models.length !== 2) {
    throw new Error('暂时无法确认模型状态，请在设置中重查。');
  }
  const item = payload.models.find(entry => entry?.model === model);
  if (!item || typeof item.status !== 'string') {
    throw new Error('暂时无法确认模型状态，请在设置中重查。');
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
        throw new Error('只能选择本站提供的 Flash 或 Pro 模型');
      }

      let response;
      let payload;
      try {
        response = await fetchImpl('/api/tarot/models/status', {
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { Accept: 'application/json' },
          ...(readOptions?.signal ? { signal: readOptions.signal } : {}),
        });
        payload = await response.json();
      } catch {
        throw new Error('暂时无法确认模型状态，请在设置中重查。');
      }
      if (!response.ok) {
        throw new Error('暂时无法确认模型状态，请在设置中重查。');
      }
      const status = validatedStatus(payload, body.model);
      if (!USABLE.has(status)) {
        throw new Error(
          BLOCKED_MESSAGES[status]
          || '所选模型当前不可用，请改选或稍后在设置中重查。',
        );
      }
      return upstreamRead(body, readOptions);
    },
  };
}
