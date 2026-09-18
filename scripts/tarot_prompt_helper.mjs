import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ritualRoot = path.join(ROOT, 'vendor', 'tarot-ritual', 'public');
const { DECK } = await import(path.join(ritualRoot, 'data', 'cards.js'));
const { SPREADS } = await import(path.join(ritualRoot, 'data', 'spreads.js'));
const { buildReadingMessages } = await import(path.join(ritualRoot, 'js', 'reading.js'));

function fail(message) {
  throw new Error(message);
}

function boundedText(value, max, name) {
  if (typeof value !== 'string' || value.length > max || value.includes('\0')) {
    fail(`invalid ${name}`);
  }
  return value;
}

function canonicalDraw(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) fail('invalid payload');
  const question = boundedText(payload.question, 4000, 'question');
  const spreadId = boundedText(payload.spread_id, 64, 'spread_id');
  const spread = SPREADS.find(item => item.id === spreadId);
  if (!spread) fail('unknown spread');
  if (!Array.isArray(payload.draws) || payload.draws.length !== spread.count) fail('invalid draw count');
  const draws = payload.draws.map(item => {
    if (!item || !Number.isSafeInteger(item.position) || item.position < 0 ||
        typeof item.card_id !== 'string' || typeof item.reversed !== 'boolean') {
      fail('invalid draw');
    }
    const card = DECK.find(candidate => candidate.id === item.card_id);
    if (!card) fail('unknown card');
    return { position: item.position, card_id: card.id, reversed: item.reversed };
  }).sort((left, right) => left.position - right.position);
  if (draws.some((item, index) => item.position !== index) ||
      new Set(draws.map(item => item.card_id)).size !== draws.length) {
    fail('duplicate cards or positions');
  }
  return {
    question,
    spread_id: spread.id,
    spread: {
      id: spread.id,
      zh: spread.zh,
      en: spread.en,
      count: spread.count,
      desc: spread.desc,
      slots: spread.slots.map(({ label, hint }) => ({ label, hint })),
    },
    draws,
  };
}

function resultFacts(canonical) {
  return {
    ...canonical,
    draws: canonical.draws.map(item => {
      const card = DECK.find(candidate => candidate.id === item.card_id);
      const slot = canonical.spread.slots[item.position];
      return {
        ...item,
        zh: card.zh,
        en: card.en,
        slot: slot.label,
      };
    }),
  };
}

function readingMessages(canonical) {
  const spread = SPREADS.find(item => item.id === canonical.spread_id);
  const placed = canonical.draws.map(item => ({
    card: DECK.find(candidate => candidate.id === item.card_id),
    reversed: item.reversed,
    slot: spread.slots[item.position],
  }));
  return buildReadingMessages({ question: canonical.question, spread, placed });
}

const raw = fs.readFileSync(0, 'utf8');
if (raw.length > 1_000_000) fail('input too large');
const request = JSON.parse(raw || '{}');
const canonical = canonicalDraw(request.payload);
const output = request.action === 'messages'
  ? { canonical: resultFacts(canonical), messages: readingMessages(canonical) }
  : { canonical: resultFacts(canonical) };
process.stdout.write(JSON.stringify(output));
