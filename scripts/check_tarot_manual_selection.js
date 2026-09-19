const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');

const root = path.resolve(__dirname, '..');
const wrapperPath = path.join(root, 'assets/tarot/managed-cards3d.v6.js');
const cardsCorePath = path.join(root, 'assets/tarot/managed-cards3d-core.v6.js');
const navigationPath = path.join(root, 'assets/tarot/managed-canvas-navigation.v6.js');
const upstreamSpecifier = '/tarot/static/js/three/managed-cards3d-core.v6.js';
const CARD_W = 1.6;
const CARD_H = 2.8;
const FOV_DEGREES = 42;

async function wrapperFixture(width, height) {
  const context = vm.createContext({ innerWidth: width, innerHeight: height });
  const calls = { begin: 0, end: 0, fit: [], fly: 0, reveal: 0 };
  const upstreamFactory = () => ({
    beginSelection() { calls.begin += 1; },
    endSelection() { calls.end += 1; },
    fitCamera(...args) { calls.fit.push(args); return { cx: 9, cy: 8, z: 7 }; },
    flyToSlot(entry, slot, done) { calls.fly += 1; done?.(); return entry; },
    revealTogether(entries, done) { calls.reveal += 1; done?.(); return true; },
  });
  const module = new vm.SourceTextModule(fs.readFileSync(wrapperPath, 'utf8'), {
    context,
    identifier: wrapperPath,
  });
  await module.link(async specifier => {
    assert.equal(specifier, upstreamSpecifier);
    return new vm.SyntheticModule(['createRitual'], function init() {
      this.setExport('createRitual', upstreamFactory);
    }, { context, identifier: specifier });
  });
  await module.evaluate();
  const stage = { rig: { base: { x: 0, y: 0, z: 17 } } };
  return { ritual: module.namespace.createRitual(stage), calls };
}

function upstreamFrame(THREE, spread, width, height) {
  const aspect = width / height;
  const fov = FOV_DEGREES * Math.PI / 180;
  const visibleHeight = 2 * Math.tan(fov / 2) * 17;
  const visibleWidth = visibleHeight * aspect;
  const scale = Math.min(
    visibleWidth * 0.86 / (spread.layout.w * CARD_W),
    visibleHeight * 0.78 / (spread.layout.h * CARD_H),
    1.25,
  );
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const slot of spread.slots) {
    const cosine = Math.abs(Math.cos(slot.rot || 0));
    const sine = Math.abs(Math.sin(slot.rot || 0));
    const halfWidth = (cosine * CARD_W + sine * CARD_H) * scale / 2;
    const halfHeight = (sine * CARD_W + cosine * CARD_H) * scale / 2;
    const x = slot.x * scale * CARD_W;
    const y = -slot.y * scale * CARD_W;
    minX = Math.min(minX, x - halfWidth);
    maxX = Math.max(maxX, x + halfWidth);
    minY = Math.min(minY, y - halfHeight);
    maxY = Math.max(maxY, y + halfHeight);
  }
  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2 - 0.3;
  const halfHeight = (maxY - minY) / 2 + 1.5;
  const halfLayoutWidth = (maxX - minX) / 2;
  const panelFraction = Math.min(500, width * 0.94) / width;
  const widthDistance = (halfLayoutWidth / (1 - panelFraction))
    / (Math.tan(fov / 2) * aspect);
  const heightDistance = halfHeight / Math.tan(fov / 2);
  const z = Math.max(widthDistance, heightDistance) * 1.18;
  const visibleFramedWidth = 2 * Math.tan(fov / 2) * z * aspect;
  return {
    scale,
    panelFraction,
    x: centerX + visibleFramedWidth * panelFraction / 2,
    y: centerY,
    z,
  };
}

function cameraFor(THREE, width, height, x, y, z) {
  const camera = new THREE.PerspectiveCamera(FOV_DEGREES, width / height, 0.1, 400);
  camera.position.set(x, y, z);
  camera.lookAt(x, y, 0);
  camera.updateMatrixWorld();
  return camera;
}

function remainingFan(THREE) {
  const count = 77;
  const fanSink = 1;
  const span = Math.min(2.2, Math.max(0.6, count * 0.045));
  const scale = Math.max(0.45, 0.62 * (1 - 0.05 * fanSink));
  const geometry = new THREE.PlaneGeometry(CARD_W, CARD_H);
  const material = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide });
  const meshes = [];
  for (let index = 0; index < count; index += 1) {
    const angle = (index / (count - 1) - 0.5) * span;
    const mesh = new THREE.Mesh(geometry, material);
    mesh.position.set(
      Math.sin(angle) * 13,
      Math.cos(angle) * 13 - 13 + 0.5 - 0.55 * fanSink,
      index * 0.018 - fanSink * 0.06,
    );
    mesh.rotation.z = -angle * 0.92;
    mesh.scale.setScalar(scale);
    mesh.updateMatrixWorld();
    meshes.push(mesh);
  }
  return meshes;
}

function projectedCard(THREE, mesh, camera, width, height) {
  const points = [
    [-CARD_W / 2, -CARD_H / 2],
    [CARD_W / 2, -CARD_H / 2],
    [CARD_W / 2, CARD_H / 2],
    [-CARD_W / 2, CARD_H / 2],
  ].map(([x, y]) => new THREE.Vector3(x, y, 0)
    .applyMatrix4(mesh.matrixWorld)
    .project(camera));
  const xs = points.map(point => (point.x + 1) * width / 2);
  const ys = points.map(point => (1 - point.y) * height / 2);
  const center = mesh.getWorldPosition(new THREE.Vector3()).project(camera);
  return {
    width: Math.max(...xs) - Math.min(...xs),
    height: Math.max(...ys) - Math.min(...ys),
    center: {
      x: (center.x + 1) * width / 2,
      y: (1 - center.y) * height / 2,
    },
  };
}

function rayHits(THREE, meshes, camera, width, height, point, offsetY = 0) {
  const raycaster = new THREE.Raycaster();
  raycaster.setFromCamera(new THREE.Vector2(
    point.x / width * 2 - 1,
    -(point.y + offsetY) / height * 2 + 1,
  ), camera);
  return raycaster.intersectObjects(meshes, false).length > 0;
}

async function checkWrapperLifecycle() {
  for (const [width, height] of [[320, 700], [360, 780], [375, 812], [390, 844]]) {
    const { ritual, calls } = await wrapperFixture(width, height);
    const spread = { id: 'three' };
    ritual.beginSelection();
    ritual.fitCamera(spread, 0.6);
    ritual.flyToSlot({}, {}, () => {});
    ritual.fitCamera(spread, 0.6);
    assert.equal(calls.fit.length, 0, `${width}px must hold the fan camera`);
    ritual.endSelection();
    assert.equal(calls.fit.length, 1, `${width}px must frame once after selection`);
    assert.deepEqual(calls.fit[0], [spread, 0.6]);
    ritual.revealTogether([], () => {});
    assert.deepEqual(
      { begin: calls.begin, end: calls.end, fly: calls.fly, reveal: calls.reveal },
      { begin: 1, end: 1, fly: 1, reveal: 1 },
    );
  }

  const desktop = await wrapperFixture(1280, 800);
  desktop.ritual.beginSelection();
  desktop.ritual.fitCamera({ id: 'three' }, 1);
  assert.equal(desktop.calls.fit.length, 1, 'desktop keeps upstream progressive framing');
  desktop.ritual.endSelection();
  assert.equal(desktop.calls.fit.length, 1, 'desktop must not duplicate framing on completion');
}

async function checkRealProjectionAndRaycast() {
  const THREE = await import(pathToFileURL(path.join(
    root, 'vendor/tarot-ritual/public/vendor/three.module.js',
  )).href);
  const { SPREADS } = await import(pathToFileURL(path.join(
    root, 'vendor/tarot-ritual/public/data/spreads.js',
  )).href);
  const manualSpreads = SPREADS.filter(item => item.count > 1);
  for (const [width, height] of [[320, 700], [360, 780], [375, 812], [390, 844]]) {
    const fan = remainingFan(THREE);
    const centerCard = fan[Math.floor(fan.length / 2)];
    const heldCamera = cameraFor(THREE, width, height, 0, 0, 17);
    const held = projectedCard(THREE, centerCard, heldCamera, width, height);
    assert.ok(held.height > 80, `${width}px held card should remain finger-sized`);
    assert.equal(rayHits(THREE, fan, heldCamera, width, height, held.center, 12), true);
    for (const spread of manualSpreads) {
      const frame = upstreamFrame(THREE, spread, width, height);
      const fittedCamera = cameraFor(THREE, width, height, frame.x, frame.y, frame.z);
      const fitted = projectedCard(THREE, centerCard, fittedCamera, width, height);
      assert.ok(frame.panelFraction >= 0.94 - Number.EPSILON);
      assert.ok(
        fitted.height < 10,
        `${width}px ${spread.id} upstream card should reproduce thin arc`,
      );
      assert.ok(held.height / fitted.height > 10);
      assert.equal(
        rayHits(THREE, fan, fittedCamera, width, height, fitted.center, 12),
        false,
      );
    }
  }
}

class PointerCanvas {
  constructor(rect) {
    this.rect = rect;
    this.style = {};
    this.captures = new Set();
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  getBoundingClientRect() { return { ...this.rect }; }

  setPointerCapture(pointerId) { this.captures.add(pointerId); }

  releasePointerCapture(pointerId) { this.captures.delete(pointerId); }

  fire(type, {
    pointerId = 1, clientX = 0, clientY = 0, pointerType = 'touch', button = 0,
  } = {}) {
    const event = {
      type, pointerId, clientX, clientY, pointerType, button,
      deltaY: 0, deltaMode: 0, ctrlKey: false,
      defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; },
    };
    for (const listener of [...(this.listeners.get(type) || [])]) listener(event);
    return event;
  }
}

async function navigationFactory(context) {
  const module = new vm.SourceTextModule(fs.readFileSync(navigationPath, 'utf8'), {
    context,
    identifier: navigationPath,
  });
  await module.link(async specifier => {
    throw new Error(`unexpected navigation import: ${specifier}`);
  });
  await module.evaluate();
  return module.namespace.attachCanvasNavigation;
}

async function checkTouchNavigationGestures() {
  const THREE = await import(pathToFileURL(path.join(
    root, 'vendor/tarot-ritual/public/vendor/three.module.js',
  )).href);
  const context = vm.createContext({ console });
  const attachCanvasNavigation = await navigationFactory(context);
  const element = new PointerCanvas({ left: 19, top: 37, width: 337, height: 700 });
  const camera = new THREE.PerspectiveCamera(FOV_DEGREES, 337 / 700, 0.1, 400);
  camera.position.z = 17;
  const rig = {
    base: new THREE.Vector3(0, 0, 17),
    target: new THREE.Vector3(0, 0, 17),
  };
  const taps = [];
  const navigation = attachCanvasNavigation({
    element,
    camera,
    rig,
    canPan: () => true,
    canZoom: () => true,
    onInteract() {},
    onTap(point) { taps.push(point); },
  });

  element.fire('pointerdown', { pointerId: 1, clientX: 100, clientY: 300 });
  element.fire('pointerup', { pointerId: 1, clientX: 108, clientY: 305 });
  assert.deepEqual(
    { x: taps[0].x, y: taps[0].y, pointerType: taps[0].pointerType },
    { x: 108, y: 305, pointerType: 'touch' },
    'touch jitter stays a tap and forwards the release coordinates',
  );

  element.fire('pointerdown', { pointerId: 2, clientX: 140, clientY: 300 });
  element.fire('pointerup', { pointerId: 2, clientX: 152, clientY: 300 });
  assert.equal(taps.length, 1, 'a release outside touch slop is not a tap');

  element.fire('pointerdown', { pointerId: 3, clientX: 160, clientY: 300 });
  element.fire('pointermove', { pointerId: 3, clientX: 195, clientY: 300 });
  assert.equal(navigation.isInteracting, true);
  element.fire('pointerup', { pointerId: 3, clientX: 195, clientY: 300 });
  assert.equal(taps.length, 1, 'a real drag never becomes a tap');

  element.fire('pointerdown', { pointerId: 4, clientX: 100, clientY: 360 });
  element.fire('pointerdown', { pointerId: 5, clientX: 250, clientY: 360 });
  element.fire('pointermove', { pointerId: 5, clientX: 290, clientY: 360 });
  assert.ok(camera.zoom > 1, 'two-finger navigation still pinches');
  element.fire('pointerup', { pointerId: 5, clientX: 290, clientY: 360 });
  element.fire('pointerup', { pointerId: 4, clientX: 100, clientY: 360 });
  assert.equal(taps.length, 1, 'a pinch never draws a card');

  element.fire('pointerdown', { pointerId: 6, clientX: 180, clientY: 400 });
  element.fire('pointercancel', { pointerId: 6, clientX: 180, clientY: 400 });
  element.fire('pointerdown', { pointerId: 7, clientX: 180, clientY: 400 });
  element.fire('lostpointercapture', { pointerId: 7, clientX: 180, clientY: 400 });
  assert.equal(taps.length, 1, 'cancelled and lost captures never draw');

  element.fire('pointerdown', { pointerId: 8, clientX: 180, clientY: 400 });
  navigation.reset();
  element.fire('pointerup', { pointerId: 8, clientX: 180, clientY: 400 });
  assert.equal(camera.zoom, 1);
  assert.equal(navigation.isInteracting, false);
  assert.equal(element.captures.size, 0);
  assert.equal(taps.length, 1, 'reset cannot turn a captured gesture into a tap');
}

async function cardsFactory(THREE) {
  const makeCanvas = () => ({
    width: 0,
    height: 0,
    getContext: () => ({
      beginPath() {}, moveTo() {}, arcTo() {}, fill() {},
    }),
  });
  const context = vm.createContext({
    console,
    window: { innerWidth: 900, innerHeight: 900 },
    document: { createElement: makeCanvas },
    setTimeout: () => 0,
    clearTimeout: () => {},
  });
  const threeNames = Object.keys(THREE);
  const threeModule = new vm.SyntheticModule(threeNames, function init() {
    for (const name of threeNames) this.setExport(name, THREE[name]);
  }, { context, identifier: 'three' });
  const cardfaceModule = new vm.SyntheticModule(['renderCardFace'], function init() {
    this.setExport('renderCardFace', makeCanvas);
  }, { context, identifier: '../art/cardface.js' });
  const navigationModule = new vm.SourceTextModule(
    fs.readFileSync(navigationPath, 'utf8'),
    { context, identifier: './canvas-navigation.v6.js' },
  );
  const cardsModule = new vm.SourceTextModule(fs.readFileSync(cardsCorePath, 'utf8'), {
    context,
    identifier: cardsCorePath,
  });
  await cardsModule.link(async specifier => {
    if (specifier === 'three') return threeModule;
    if (specifier === '../art/cardface.js') return cardfaceModule;
    if (specifier === './canvas-navigation.v6.js') return navigationModule;
    throw new Error(`unexpected cards import: ${specifier}`);
  });
  await cardsModule.evaluate();
  return cardsModule.namespace.createRitual;
}

function createCardsHarness(THREE, createRitual) {
  const rect = { left: 31, top: 73, width: 320, height: 700 };
  const element = new PointerCanvas(rect);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(FOV_DEGREES, rect.width / rect.height, 0.1, 400);
  camera.position.set(0, 0, 17);
  camera.lookAt(0, 0, 0);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
  const rig = {
    base: new THREE.Vector3(0, 0, 17),
    target: new THREE.Vector3(0, 0, 17),
  };
  const frames = [];
  const ritual = createRitual({
    scene,
    camera,
    rig,
    renderer: { domElement: element },
    onFrame(callback) { frames.push(callback); },
  });
  ritual.build(['A', 'B', 'C'].map(id => ({ id })), { deterministic: true });
  const entries = ritual.cards;
  entries.forEach((entry, index) => {
    entry.state = 'fan';
    entry.mesh.visible = true;
    entry.mesh.position.set((index - 1) * 2, 0, index * 0.001);
    entry.mesh.rotation.set(0, 0, 0);
    entry.mesh.scale.setScalar(1);
  });
  ritual.beginSelection();
  const selected = [];
  ritual.onSelect = entry => {
    selected.push(entry.card.id);
    ritual.flyToSlot(entry, {
      pos: new THREE.Vector3(0, -5 - selected.length, 1),
      rot: 0,
      scale: 0.7,
      deferReveal: true,
    });
  };
  const tick = () => {
    scene.updateMatrixWorld(true);
    camera.updateMatrixWorld(true);
    frames.forEach(callback => callback(0.016, 1));
  };
  const clientPoint = entry => {
    scene.updateMatrixWorld(true);
    camera.updateMatrixWorld(true);
    const projected = entry.mesh.getWorldPosition(new THREE.Vector3()).project(camera);
    return {
      x: rect.left + (projected.x + 1) * rect.width / 2,
      y: rect.top + (1 - projected.y) * rect.height / 2,
    };
  };
  const touch = (entry, { jitter = false, pointerId = 1 } = {}) => {
    const at = clientPoint(entry);
    element.fire('pointerdown', {
      pointerId,
      clientX: at.x + (jitter ? -4 : 0),
      clientY: at.y + (jitter ? -3 : 0),
    });
    element.fire('pointerup', {
      pointerId,
      clientX: at.x + (jitter ? 3 : 0),
      clientY: at.y + (jitter ? 3 : 0),
    });
  };
  tick();
  return { element, scene, camera, rig, ritual, entries, selected, tick, clientPoint, touch };
}

async function checkRealCardsTouchFlow() {
  const THREE = await import(pathToFileURL(path.join(
    root, 'vendor/tarot-ritual/public/vendor/three.module.js',
  )).href);
  const createRitual = await cardsFactory(THREE);

  const direct = createCardsHarness(THREE, createRitual);
  direct.touch(direct.entries[1]);
  assert.deepEqual(direct.selected, ['B'], 'a touch tap selects without any pointermove frame');

  const stale = createCardsHarness(THREE, createRitual);
  const stalePoint = stale.clientPoint(stale.entries[0]);
  stale.element.fire('pointermove', {
    pointerId: 1, clientX: stalePoint.x, clientY: stalePoint.y,
  });
  stale.tick();
  stale.touch(stale.entries[2]);
  assert.deepEqual(stale.selected, ['C'], 'tap-time raycast replaces a stale hover on another card');

  const repeated = createCardsHarness(THREE, createRitual);
  repeated.touch(repeated.entries[1], { jitter: true, pointerId: 1 });
  repeated.camera.position.set(0.8, 0.35, 17);
  repeated.camera.lookAt(0.8, 0.35, 0);
  repeated.camera.updateMatrixWorld();
  repeated.touch(repeated.entries[2], { pointerId: 2 });
  repeated.touch(repeated.entries[0], { pointerId: 3 });
  assert.deepEqual(
    repeated.selected,
    ['B', 'C', 'A'],
    'each tap re-raycasts after the selected card leaves the fan and after camera movement',
  );
  assert.equal(new Set(repeated.selected).size, 3);

  const gestures = createCardsHarness(THREE, createRitual);
  const center = gestures.clientPoint(gestures.entries[1]);
  gestures.element.fire('pointerdown', {
    pointerId: 10, clientX: center.x, clientY: center.y,
  });
  gestures.element.fire('pointermove', {
    pointerId: 10, clientX: center.x + 36, clientY: center.y,
  });
  gestures.element.fire('pointerup', {
    pointerId: 10, clientX: center.x + 36, clientY: center.y,
  });
  gestures.element.fire('pointerdown', {
    pointerId: 11, clientX: center.x - 50, clientY: center.y,
  });
  gestures.element.fire('pointerdown', {
    pointerId: 12, clientX: center.x + 50, clientY: center.y,
  });
  gestures.element.fire('pointermove', {
    pointerId: 12, clientX: center.x + 80, clientY: center.y,
  });
  gestures.element.fire('pointerup', {
    pointerId: 12, clientX: center.x + 80, clientY: center.y,
  });
  gestures.element.fire('pointerup', {
    pointerId: 11, clientX: center.x - 50, clientY: center.y,
  });
  gestures.element.fire('pointerdown', {
    pointerId: 13, clientX: center.x, clientY: center.y,
  });
  gestures.element.fire('pointercancel', {
    pointerId: 13, clientX: center.x, clientY: center.y,
  });
  gestures.element.fire('pointerdown', {
    pointerId: 14, clientX: center.x, clientY: center.y,
  });
  gestures.element.fire('lostpointercapture', {
    pointerId: 14, clientX: center.x, clientY: center.y,
  });
  assert.deepEqual(gestures.selected, [], 'drag, pinch, cancel, and lost capture do not select');

  const mouse = createCardsHarness(THREE, createRitual);
  const mousePoint = mouse.clientPoint(mouse.entries[1]);
  mouse.element.fire('pointermove', {
    pointerId: 20, clientX: mousePoint.x, clientY: mousePoint.y, pointerType: 'mouse',
  });
  mouse.tick();
  mouse.element.fire('pointerdown', {
    pointerId: 20, clientX: mousePoint.x, clientY: mousePoint.y, pointerType: 'mouse',
  });
  mouse.element.fire('pointerup', {
    pointerId: 20, clientX: mousePoint.x, clientY: mousePoint.y, pointerType: 'mouse',
  });
  assert.deepEqual(mouse.selected, ['B'], 'desktop mouse hover/click behavior remains available');
}

Promise.resolve()
  .then(checkWrapperLifecycle)
  .then(checkRealProjectionAndRaycast)
  .then(checkTouchNavigationGestures)
  .then(checkRealCardsTouchFlow)
  .then(() => console.log('tarot manual selection (real cards3d/navigation/Three.js touch flow): ok'))
  .catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
