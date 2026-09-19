const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');

const root = path.resolve(__dirname, '..');
const wrapperPath = path.join(root, 'assets/tarot/managed-cards3d.v5.js');
const upstreamSpecifier = '/tarot/static/js/three/upstream-cards3d.v1.js';
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

async function checkTouchNavigationReset() {
  const THREE = await import(pathToFileURL(path.join(
    root, 'vendor/tarot-ritual/public/vendor/three.module.js',
  )).href);
  const { attachCanvasNavigation } = await import(pathToFileURL(path.join(
    root, 'vendor/tarot-ritual/public/js/three/canvas-navigation.js',
  )).href);
  const element = new EventTarget();
  element.style = {};
  element.getBoundingClientRect = () => ({ left: 0, top: 0, width: 375, height: 812 });
  const captures = new Set();
  element.setPointerCapture = id => captures.add(id);
  element.releasePointerCapture = id => captures.delete(id);
  const camera = new THREE.PerspectiveCamera(FOV_DEGREES, 375 / 812, 0.1, 400);
  camera.position.z = 17;
  const rig = {
    base: new THREE.Vector3(0, 0, 17),
    target: new THREE.Vector3(0, 0, 17),
  };
  let mode = 'select';
  let taps = 0;
  const navigation = attachCanvasNavigation({
    element,
    camera,
    rig,
    canPan: () => true,
    canZoom: () => mode === 'layout',
    onInteract() {},
    onTap() { taps += 1; },
  });
  const fire = (type, pointerId, clientX, clientY) => {
    const event = new Event(type, { cancelable: true });
    Object.assign(event, {
      pointerId, clientX, clientY, button: 0, pointerType: 'touch',
    });
    element.dispatchEvent(event);
  };

  fire('pointerdown', 1, 100, 400);
  fire('pointerdown', 2, 275, 400);
  fire('pointermove', 2, 350, 400);
  fire('pointerup', 2, 350, 400);
  fire('pointerup', 1, 100, 400);
  assert.equal(camera.zoom, 1, 'selection pinch must not leave latent zoom');
  assert.equal(navigation.isInteracting, false);
  assert.equal(captures.size, 0);
  assert.equal(taps, 0);

  mode = 'layout';
  fire('pointerdown', 1, 100, 400);
  fire('pointerdown', 2, 275, 400);
  fire('pointermove', 2, 350, 400);
  assert.ok(camera.zoom > 1, 'reading layout can zoom in');
  fire('pointermove', 2, 275, 400);
  assert.equal(camera.zoom, 1, 'reading layout can pinch back to its original zoom');
  fire('pointerup', 2, 275, 400);
  fire('pointerup', 1, 100, 400);
  assert.equal(navigation.isInteracting, false);
  assert.equal(captures.size, 0);

  fire('pointerdown', 3, 180, 400);
  navigation.reset();
  fire('pointerup', 3, 180, 400);
  assert.equal(camera.zoom, 1);
  assert.equal(navigation.isInteracting, false);
  assert.equal(captures.size, 0);
  assert.equal(taps, 0, 'reset must not turn a captured gesture into a card tap');
}

Promise.resolve()
  .then(checkWrapperLifecycle)
  .then(checkRealProjectionAndRaycast)
  .then(checkTouchNavigationReset)
  .then(() => console.log('tarot mobile manual selection (Three.js projection/raycast + touch navigation): ok'))
  .catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
