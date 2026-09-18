// Managed provider boundary for the upstream ARCANUM UI.
// This module deliberately has no credential import, entry, persistence or
// forwarding path. The server reconstructs the canonical reading prompt.

export * from '/tarot/static/data/cards.js';
export * from '/tarot/static/data/spreads.js';
export * from '/tarot/static/js/reading.js';

const PROVIDER_ID = 'managed:cedartoy-tarot';
const FLASH_MODEL = 'gemini-3.5-flash';
const PRO_MODEL = 'gemini-3.1-pro-preview';
const MODELS = Object.freeze([FLASH_MODEL, PRO_MODEL]);
const LS_CUSTOM = 'arcana.customProviders.v1';
const LS_SELECTED = 'arcana.selectedProvider.v1';

const provider = Object.freeze({
  id: PROVIDER_ID,
  label: '本站',
  kind: 'managed',
  models: MODELS,
  hasKey: true,
});

export const providerState = {
  dsh: { found: true, enabled: true, providers: [provider] },
  custom: [],
  selectedId: PROVIDER_ID,
  modelByProvider: { [PROVIDER_ID]: FLASH_MODEL },
};

function allowedModel(value) {
  return MODELS.includes(value) ? value : FLASH_MODEL;
}

function persist() {
  try {
    localStorage.removeItem(LS_CUSTOM);
    localStorage.setItem(LS_SELECTED, JSON.stringify({
      id: PROVIDER_ID,
      models: { [PROVIDER_ID]: currentModel(PROVIDER_ID) },
    }));
  } catch { /* The current page still keeps its in-memory choice. */ }
}

export function initProviderState() {
  let selected = FLASH_MODEL;
  try {
    localStorage.removeItem(LS_CUSTOM);
    const saved = JSON.parse(localStorage.getItem(LS_SELECTED) || 'null');
    selected = allowedModel(saved?.models?.[PROVIDER_ID]);
  } catch { /* Bad or unavailable storage falls back to Flash. */ }
  providerState.custom = [];
  providerState.selectedId = PROVIDER_ID;
  providerState.modelByProvider = { [PROVIDER_ID]: selected };
  persist();
}

export async function loadDsh() {
  return providerState.dsh;
}

export async function importDsh() {
  throw new Error('本站托管版不支持导入配置');
}

export function allProviders() {
  return [provider];
}

export function getProvider(id) {
  return id === PROVIDER_ID ? provider : null;
}

export function addCustomProvider() {
  throw new Error('本站托管版仅支持所提供的模型');
}

export function removeCustomProvider() {
  throw new Error('本站托管版没有自定义模型');
}

export function selectProvider(id, model = null) {
  if (id !== PROVIDER_ID) throw new Error('模型来源不可用');
  providerState.selectedId = PROVIDER_ID;
  if (model !== null) setModel(id, model);
  else persist();
}

export function setModel(id, model) {
  if (id !== PROVIDER_ID || !MODELS.includes(model)) {
    throw new Error('只能选择本站提供的 Flash 或 Pro 模型');
  }
  providerState.modelByProvider[PROVIDER_ID] = model;
  persist();
}

export function currentModel(id) {
  if (id !== PROVIDER_ID) return null;
  return allowedModel(providerState.modelByProvider[PROVIDER_ID]);
}

export async function fetchModels(id) {
  if (id !== PROVIDER_ID) throw new Error('模型来源不可用');
  return [...MODELS];
}

export async function chat(options) {
  const {
    model, onDelta, onDone, onError, signal, transport,
  } = options || {};
  let reader;
  let errored = false;
  try {
    if (!transport) {
      throw new Error('本站托管版仅允许当前会话发起专业解读');
    }
    if (!MODELS.includes(model)) {
      throw new Error('只能选择本站提供的 Flash 或 Pro 模型');
    }
    // Only the fixed model ID crosses the browser/session boundary. Prompts
    // and provider credentials are never accepted from this managed page.
    const response = await transport({ model }, { signal });
    if (!response.ok || !response.body) {
      const error = await response.json().catch(() => null);
      throw new Error(error?.error || `服务返回 ${response.status}`);
    }
    reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) throw new Error('响应提前中断，请重试');
      if (signal?.aborted) return;
      buffer += decoder.decode(chunk.value, { stream: true });
      let boundary;
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        const event = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        const data = event.split(/\r?\n/)
          .filter(line => line.startsWith('data:'))
          .map(line => line.slice(5).trimStart()).join('\n');
        if (!data) continue;
        const parsed = JSON.parse(data);
        if (parsed.t === 'delta') onDelta?.(parsed.v);
        else if (parsed.t === 'error') { errored = true; onError?.(parsed.v); }
        else if (parsed.t === 'done') { onDone?.(!errored); return; }
      }
    }
  } catch (error) {
    if (signal?.aborted) return;
    if (!errored) onError?.(error.message || '无法连接本站服务');
    onDone?.(false);
  } finally {
    await reader?.cancel().catch(() => {});
    reader?.releaseLock();
  }
}
