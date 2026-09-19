import { createRitual as createUpstreamRitual } from '/tarot/static/js/three/upstream-cards3d.v1.js';

// The upstream reading panel occupies 94% of a phone viewport. Its fitCamera
// calculation reserves that width even while the user is still selecting,
// leaving only 6% for the spread and shrinking the remaining fan to a few
// pixels. Keep the selection camera stable on crowded viewports, then apply
// the latest requested spread framing once every chosen card has landed.
const PANEL_MAX_WIDTH = 500;
const PANEL_VIEWPORT_FRACTION = 0.94;
const MIN_SELECTION_VIEWPORT_FRACTION = 0.28;

function selectionViewportIsCrowded() {
  const width = Number(globalThis.innerWidth);
  if (!Number.isFinite(width) || width <= 0) return false;
  const panelWidth = Math.min(PANEL_MAX_WIDTH, width * PANEL_VIEWPORT_FRACTION);
  return 1 - panelWidth / width < MIN_SELECTION_VIEWPORT_FRACTION;
}

export function createRitual(stage) {
  const ritual = createUpstreamRitual(stage);
  const upstreamBeginSelection = ritual.beginSelection.bind(ritual);
  const upstreamEndSelection = ritual.endSelection.bind(ritual);
  const upstreamFitCamera = ritual.fitCamera.bind(ritual);
  let selecting = false;
  let deferredFrame = null;

  ritual.beginSelection = (...args) => {
    deferredFrame = null;
    selecting = true;
    return upstreamBeginSelection(...args);
  };

  ritual.fitCamera = (...args) => {
    if (selecting && selectionViewportIsCrowded()) {
      deferredFrame = args;
      const base = stage?.rig?.base;
      return {
        cx: Number.isFinite(base?.x) ? base.x : 0,
        cy: Number.isFinite(base?.y) ? base.y : 0,
        z: Number.isFinite(base?.z) ? base.z : 17,
      };
    }
    deferredFrame = null;
    return upstreamFitCamera(...args);
  };

  ritual.endSelection = (...args) => {
    const frame = deferredFrame;
    deferredFrame = null;
    selecting = false;
    const result = upstreamEndSelection(...args);
    if (frame) upstreamFitCamera(...frame);
    return result;
  };

  return ritual;
}
