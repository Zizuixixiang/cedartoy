// ============================================================================
// 卡牌圣仪 · 三维卡牌系统：牌堆、涡旋洗牌、扇形、飞牌、翻转、星爆
// ============================================================================
import * as THREE from 'three';
import { renderCardFace } from '../art/cardface.js';
import { attachCanvasNavigation } from './canvas-navigation.v6.js';

const CARD_W = 1.6, CARD_H = 2.8, CARD_T = 0.05;
const FAN_R = 13, FAN_BASE_Y = 0.5, FAN_SCALE = 0.62;

// ---- 微型补间引擎 -----------------------------------------------------------
const EASE = {
  outQuint: t => 1 - Math.pow(1 - t, 5),
  outQuart: t => 1 - Math.pow(1 - t, 4),
  outCubic: t => 1 - Math.pow(1 - t, 3),
  inOutSine: t => 0.5 - 0.5 * Math.cos(Math.PI * t),
  outBack: t => 1 + 2.2 * Math.pow(t - 1, 3) + 1.2 * Math.pow(t - 1, 2),
};

class Twain {
  constructor() { this.items = []; }
  to(target, prop, toV, dur, ease = 'outQuint', onDone = null, onUpdate = null) {
    this.kill(target, prop);
    this.items.push({ target, prop, toV, dur, ease: EASE[ease], onDone, onUpdate, t: 0, from: null });
  }
  kill(target, prop) {
    // 新补间对同一目标同一属性拥有唯一控制权（防止旧补间后续帧覆盖）
    this.items = this.items.filter(it => !(it.target === target && it.prop === prop));
  }
  update(dt) {
    for (let i = this.items.length - 1; i >= 0; i--) {
      const it = this.items[i];
      if (it.from === null) {
        const v = it.target[it.prop];
        it.from = (v && v.clone) ? v.clone() : v;
      }
      it.t += dt / it.dur;
      const k = Math.min(it.t, 1), e = it.ease(k);
      const v = it.target[it.prop];
      if (v && v.lerp && it.from && it.from.lerp) v.lerpVectors(it.from, it.toV, e);
      else if (typeof v === 'number' || typeof v === 'boolean') it.target[it.prop] = it.from + (it.toV - it.from) * e;
      // 其余情形（如仅由 onUpdate 驱动的路径）只推进时间
      if (it.onUpdate) it.onUpdate(e);
      if (k >= 1) { this.items.splice(i, 1); if (it.onDone) it.onDone(); }
    }
  }
}

// ---- 牌背 shader -------------------------------------------------------------
const BACK_FRAG = /* glsl */`
  varying vec2 vUv;
  uniform float uTime;
  void main(){
    vec2 p = vUv - 0.5;
    p.x *= 0.5714;
    float r = length(p);
    float a = atan(p.y, p.x);

    vec3 base = vec3(0.052, 0.045, 0.098);
    vec3 deep = vec3(0.030, 0.026, 0.062);
    vec3 gold = vec3(0.85, 0.70, 0.42);
    vec3 col = mix(base, deep, smoothstep(0.0, 0.52, r));

    float rings = 0.0;
    rings += smoothstep(0.012, 0.0, abs(r - 0.40) - 0.0025);
    rings += smoothstep(0.010, 0.0, abs(r - 0.33) - 0.002) * 0.7;
    float dash = smoothstep(0.2, 0.9, sin(a * 48.0 + uTime * 0.4) * 0.5 + 0.5);
    rings += smoothstep(0.008, 0.0, abs(r - 0.455) - 0.0018) * dash * 0.85;
    col += gold * rings * 0.85;

    float k = pow(abs(cos(a * 4.0)), 6.0);
    float star = k * smoothstep(0.30, 0.05, r);
    col += gold * star * 0.9;
    float core = smoothstep(0.085, 0.0, r);
    col += vec3(0.95, 0.85, 0.6) * core * 0.9;

    float flow = sin(a * 8.0 - uTime * 0.8 + r * 14.0);
    col += gold * smoothstep(0.86, 1.0, flow) * smoothstep(0.5, 0.30, r) * 0.35;

    float frame = smoothstep(0.008, 0.0, abs(r - 0.50) - 0.003);
    col += gold * frame * 0.9;
    col *= 1.0 - smoothstep(0.44, 0.56, r) * 0.4;

    gl_FragColor = vec4(col, 1.0);
  }
`;
const BACK_VERT = /* glsl */`
  varying vec2 vUv;
  void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }
`;

// ---- 几何与材质 ---------------------------------------------------------------
let _geo = null, _frontGeo = null, _backGeo = null, _glowTex = null;
const bodyMat = new THREE.MeshBasicMaterial({ color: '#2a2440' });
const edgeMat = new THREE.MeshBasicMaterial({ color: '#3a3352' });
const faceMats = new Map();

function cardGeometry() {
  if (_geo) return _geo;
  const s = new THREE.Shape();
  const x = -CARD_W / 2, y = -CARD_H / 2, r = 0.14;
  s.moveTo(x + r, y);
  s.lineTo(x + CARD_W - r, y);
  s.quadraticCurveTo(x + CARD_W, y, x + CARD_W, y + r);
  s.lineTo(x + CARD_W, y + CARD_H - r);
  s.quadraticCurveTo(x + CARD_W, y + CARD_H, x + CARD_W - r, y + CARD_H);
  s.lineTo(x + r, y + CARD_H);
  s.quadraticCurveTo(x, y + CARD_H, x, y + CARD_H - r);
  s.lineTo(x, y + r);
  s.quadraticCurveTo(x, y, x + r, y);
  _geo = new THREE.ExtrudeGeometry(s, { depth: CARD_T, bevelEnabled: false, curveSegments: 8 });
  return _geo;
}
function frontGeo() {
  if (!_frontGeo) _frontGeo = new THREE.PlaneGeometry(CARD_W - 0.03, CARD_H - 0.03);
  return _frontGeo;
}
function backGeo() {
  if (!_backGeo) _backGeo = new THREE.PlaneGeometry(CARD_W - 0.03, CARD_H - 0.03);
  return _backGeo;
}
function faceMaterial(card) {
  if (faceMats.has(card.id)) return faceMats.get(card.id);
  const tex = new THREE.CanvasTexture(renderCardFace(card));
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  const m = new THREE.MeshBasicMaterial({ map: tex, transparent: true });
  faceMats.set(card.id, m);
  return m;
}
let _backMat = null;
function backMaterial() {
  if (_backMat) return _backMat;
  _backMat = new THREE.ShaderMaterial({
    vertexShader: BACK_VERT,
    fragmentShader: BACK_FRAG,
    uniforms: { uTime: { value: 0 } },
  });
  return _backMat;
}
function glowTexture() {
  if (_glowTex) return _glowTex;
  const c = document.createElement('canvas');
  c.width = 128; c.height = 192;
  const g = c.getContext('2d');
  g.shadowColor = 'rgba(220,185,120,1)';
  g.shadowBlur = 30;
  g.fillStyle = 'rgba(220,185,120,0.14)';
  const r = 14, x = 20, y = 20, w = 88, h = 152;
  g.beginPath();
  g.moveTo(x + r, y);
  g.arcTo(x + w, y, x + w, y + h, r);
  g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r);
  g.arcTo(x, y, x + w, y, r);
  g.fill(); g.fill();
  _glowTex = new THREE.CanvasTexture(c);
  return _glowTex;
}

// ---- 星爆粒子池 ---------------------------------------------------------------
class BurstPool {
  constructor(scene) { this.scene = scene; this.active = []; }
  spawn(pos, color = '#e8c98a', n = 110) {
    const geo = new THREE.BufferGeometry();
    const positions = new Float32Array(n * 3);
    const vel = [];
    for (let i = 0; i < n; i++) {
      positions[i * 3] = pos.x; positions[i * 3 + 1] = pos.y; positions[i * 3 + 2] = pos.z + 0.1;
      const a = Math.random() * Math.PI * 2, b = Math.acos(2 * Math.random() - 1);
      const sp = 1.2 + Math.random() * 3.2;
      vel.push(new THREE.Vector3(
        Math.sin(b) * Math.cos(a) * sp,
        Math.sin(b) * Math.sin(a) * sp,
        Math.cos(b) * sp * 0.7 + 0.8,
      ));
    }
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const mat = new THREE.PointsMaterial({
      size: 0.09, color, transparent: true, opacity: 1,
      depthWrite: false, blending: THREE.AdditiveBlending,
    });
    const pts = new THREE.Points(geo, mat);
    this.scene.add(pts);
    this.active.push({ pts, vel, life: 0, max: 1.1 });
  }
  update(dt) {
    for (let i = this.active.length - 1; i >= 0; i--) {
      const b = this.active[i];
      b.life += dt;
      const arr = b.pts.geometry.attributes.position.array;
      for (let k = 0; k < b.vel.length; k++) {
        b.vel[k].multiplyScalar(1 - 2.2 * dt);
        arr[k * 3] += b.vel[k].x * dt;
        arr[k * 3 + 1] += b.vel[k].y * dt;
        arr[k * 3 + 2] += b.vel[k].z * dt;
      }
      b.pts.geometry.attributes.position.needsUpdate = true;
      b.pts.material.opacity = Math.max(0, 1 - b.life / b.max);
      if (b.life >= b.max) {
        this.scene.remove(b.pts);
        b.pts.geometry.dispose(); b.pts.material.dispose();
        this.active.splice(i, 1);
      }
    }
  }
}

// ---- 主系统 -------------------------------------------------------------------
export function createRitual(stage) {
  const { scene, camera, rig, onFrame } = stage;
  const twain = new Twain();
  const bursts = new BurstPool(scene);
  const group = new THREE.Group();
  scene.add(group);

  const cards = [];
  let fanSink = 0; // 已抽取张数：剩余扇形随之下沉、缩小、退场
  let mode = 'idle'; // idle | intro | fan | shuffle | select | revealing | layout
  let generation = 0;
  let hovered = null;
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2(-2, -2);
  let onSelect = null, onHover = null, onEmptyClick = null, onShuffleDone = null;
  let shuffleState = null;

  function newEntry(card, idx) {
    const g3 = new THREE.Group();
    const body = new THREE.Mesh(cardGeometry(), bodyMat);
    const front = new THREE.Mesh(frontGeo(), faceMaterial(card));
    front.position.z = CARD_T + 0.002;
    const back = new THREE.Mesh(backGeo(), backMaterial());
    back.rotation.y = Math.PI;
    back.position.z = -0.002;
    const glow = new THREE.Mesh(
      new THREE.PlaneGeometry(CARD_W * 1.5, CARD_H * 1.35),
      new THREE.MeshBasicMaterial({
        map: glowTexture(), transparent: true, opacity: 0, depthWrite: false,
        blending: THREE.AdditiveBlending, color: '#d9b877',
      }),
    );
    glow.position.z = -0.06;
    g3.add(body, front, back, glow);
    g3.rotation.y = Math.PI; // 初始背面示人
    const entry = { mesh: g3, glow, card, state: 'deck', idx };
    [body, front, back].forEach(ch => { ch.userData.entry = entry; });
    return entry;
  }

  function fanPose(i, count) {
    const span = Math.min(2.2, Math.max(0.6, count * 0.045));
    const a = count === 1 ? 0 : (i / (count - 1) - 0.5) * span;
    const sc = Math.max(0.45, FAN_SCALE * (1 - 0.05 * fanSink));
    return {
      x: Math.sin(a) * FAN_R,
      y: Math.cos(a) * FAN_R - FAN_R + FAN_BASE_Y - 0.55 * fanSink,
      z: i * 0.018 - fanSink * 0.06,
      rot: -a * 0.92,
      sc,
    };
  }

  function shuffledEntries() {
    const arr = [...cards];
    for (let i = arr.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    return arr;
  }

  function dealFan(order, instant = false) {
    order.forEach((entry, i) => {
      const p = fanPose(i, order.length);
      const m = entry.mesh;
      if (instant) {
        m.position.set(p.x, p.y, p.z);
        m.rotation.z = p.rot;
        m.scale.setScalar(p.sc);
      } else {
        twain.to(m.position, 'x', p.x, 0.75, 'outQuint');
        twain.to(m.position, 'y', p.y, 0.75, 'outQuint');
        twain.to(m.position, 'z', p.z, 0.75, 'outQuint');
        twain.to(m.rotation, 'z', p.rot, 0.85, 'inOutSine');
        twain.to(m.scale, 'x', p.sc, 0.75, 'outQuint');
        twain.to(m.scale, 'y', p.sc, 0.75, 'outQuint');
      }
      entry.state = 'fan';
    });
  }

  // ---- 公开 API ----
  function build(deck, { deterministic = false } = {}) {
    generation++;
    mode = 'idle';
    fanSink = 0;
    cards.length = 0;
    while (group.children.length) group.remove(group.children[0]);
    deck.forEach((card, i) => {
      const entry = newEntry(card, i);
      entry.mesh.position.set(deterministic ? 0 : (Math.random() - 0.5) * 0.2, deterministic ? 0 : (Math.random() - 0.5) * 0.2, -5 - i * 0.02);
      entry.mesh.visible = false;
      group.add(entry.mesh);
      cards.push(entry);
    });
  }

  function intro(onDone) {
    mode = 'intro';
    const order = shuffledEntries();
    order.forEach((entry, i) => {
      const m = entry.mesh;
      m.visible = true;
      const target = new THREE.Vector3((Math.random() - 0.5) * 0.5, (Math.random() - 0.5) * 0.5, 0.8 - i * 0.022);
      twain.to(m.position, 'x', target.x, 1.5 + Math.random() * 0.5, 'outQuart');
      twain.to(m.position, 'y', target.y, 1.5 + Math.random() * 0.5, 'outQuart');
      twain.to(m.position, 'z', target.z, 1.5 + Math.random() * 0.5, 'outQuart');
      twain.to(m.rotation, 'z', (Math.random() - 0.5) * 0.12, 1.8, 'inOutSine');
    });
    setTimeout(() => onDone && onDone(), 2200);
  }

  function shuffle(onDone) {
    generation++;
    fanSink = 0;
    mode = 'shuffle';
    onShuffleDone = onDone;
    const entries = cards.filter(c => c.state !== 'drawn');
    const order = shuffledEntries();
    entries.forEach((entry, i) => {
      const m = entry.mesh;
      const dx = m.position.x, dy = m.position.y - (FAN_BASE_Y - 1);
      entry.shufflePhase = Math.atan2(dy, dx);
      entry.r0 = Math.hypot(dx, dy);
      entry.z0 = m.position.z;
      entry.rot0 = m.rotation.z;
      entry.shuffleR = 3.2 + Math.random() * 2.4;
      entry.shuffleI = i / entries.length;
    });
    shuffleState = { t: 0, entries, dur: 3.2, order };
  }

  function beginSelection() { mode = 'select'; }
  function retireFan() {
    fanEntries().forEach((e, i) => {
      twain.to(e.mesh.position, 'y', e.mesh.position.y - 13 - i * 0.02, 1.3, 'inOutSine');
      setTimeout(() => { if (e.state === 'fan') e.mesh.visible = false; }, 1200);
    });
  }
  function endSelection() { mode = 'layout'; setHovered(null); retireFan(); }
  function restoreLayout(placed) {
    generation++;
    mode = 'layout';
    setHovered(null);
    twain.items = [];
    cards.forEach(entry => { entry.mesh.visible = false; });
    for (const { entry, slotWorld: slot, reversed, revealed } of placed) {
      if (!cards.includes(entry)) throw new Error('Restored card is not in this deck');
      entry.state = 'drawn';
      entry.mesh.visible = true;
      entry.mesh.position.copy(slot.pos);
      entry.mesh.rotation.set(0, revealed ? 0 : Math.PI, slot.rot + (revealed && reversed ? Math.PI : 0));
      entry.mesh.scale.set(slot.scale, slot.scale, 1);
      entry.pendingReveal = revealed ? null : { ...slot, reversed };
    }
  }
  function fanEntries() { return cards.filter(c => c.state === 'fan'); }
  function refan() { dealFan(fanEntries()); }

  function flyToSlot(entry, slotWorld, onDone) {
    const flightGeneration = generation;
    entry.pendingReveal = null;
    entry.state = 'drawn';
    fanSink += 1;
    entry.mesh.visible = true;
    setHovered(null);
    const m = entry.mesh;
    // 起飞前清空此牌的旧补间（洗牌/退场/归扇），飞行拥有完全控制权
    ['x', 'y', 'z'].forEach(k => {
      twain.kill(m.position, k);
      twain.kill(m.scale, k);
      twain.kill(m.rotation, k);
    });
    const p0 = m.position.clone();
    const p2 = slotWorld.pos;
    const p1 = p0.clone().lerp(p2, 0.5); p1.y += 3.4; p1.z += 4.5;
    const rotObj = { v: m.rotation.z };
    const dur = 1.0;
    if (slotWorld.deferReveal) {
      m.rotation.y = Math.PI;
      twain.to(m.scale, 'x', slotWorld.scale, dur, 'outQuart');
      twain.to(m.scale, 'y', slotWorld.scale, dur, 'outQuart');
    }
    twain.to(rotObj, 'v', slotWorld.rot, dur, 'inOutSine');
    twain.to({ v: 0 }, 'v', 0, dur, 'outQuart', () => {
      if (flightGeneration !== generation) return;
      if (slotWorld.deferReveal) {
        entry.pendingReveal = slotWorld;
        onDone && onDone();
        return;
      }
      // 到位：翻牌显面
      twain.to(m.rotation, 'y', 0, 0.55, 'outQuart', () => {
        bursts.spawn(p2, '#e8c98a', 130);
        onDone && onDone();
      });
      twain.to(m.scale, 'x', slotWorld.scale, 0.55, 'outBack');
      twain.to(m.scale, 'y', slotWorld.scale, 0.55, 'outBack');
      twain.to(m.position, 'z', p2.z + 0.1, 0.55, 'outQuart');
      if (slotWorld.reversed) {
        setTimeout(() => twain.to(m.rotation, 'z', slotWorld.rot + Math.PI, 0.6, 'inOutSine'), 150);
      }
    }, t => {
      const u = 1 - t;
      m.position.set(
        u * u * p0.x + 2 * u * t * p1.x + t * t * p2.x,
        u * u * p0.y + 2 * u * t * p1.y + t * t * p2.y,
        u * u * p0.z + 2 * u * t * p1.z + t * t * p2.z,
      );
      m.rotation.z = rotObj.v;
    });
    setTimeout(() => { if (mode !== 'layout') refan(); }, dur * 700);
    return entry;
  }

  // 所有牌落定后统一翻开；旧牌局的延时或完成回调不能启动新解读。
  function revealTogether(entries, onDone) {
    if (mode === 'revealing' || !entries.length || new Set(entries).size !== entries.length
      || entries.some(e => !cards.includes(e) || !e.pendingReveal)) return false;
    const revealGeneration = generation;
    const batch = entries.map(entry => ({entry, slot:entry.pendingReveal}));
    batch.forEach(({entry}) => { entry.pendingReveal = null; });
    setHovered(null);
    mode = 'revealing';
    setTimeout(() => {
      if (generation !== revealGeneration || mode !== 'revealing') return;
      let remaining = batch.length;
      for (const {entry, slot} of batch) {
        const m = entry.mesh;
        twain.to(m.rotation, 'y', 0, .75, 'inOutSine', () => {
          if (generation !== revealGeneration || mode !== 'revealing') return;
          bursts.spawn(slot.pos, '#e8c98a', 130);
          if (--remaining === 0) { mode = 'layout'; onDone && onDone(); }
        });
        twain.to(m.rotation, 'z', slot.rot + (slot.reversed ? Math.PI : 0), .75, 'inOutSine');
        twain.to(m.scale, 'x', slot.scale, .75, 'outBack');
        twain.to(m.scale, 'y', slot.scale, .75, 'outBack');
        twain.to(m.position, 'z', slot.pos.z + .1, .75, 'outQuart');
      }
    }, 300);
    return true;
  }

  function setHovered(entry) {
    if (hovered === entry) return;
    if (hovered && hovered.state === 'fan') {
      const m = hovered.mesh;
      const rest = fanEntries();
      const i = rest.indexOf(hovered);
      if (i >= 0) {
        const p = fanPose(i, rest.length);
        twain.to(m.position, 'z', p.z, 0.3, 'outCubic');
        twain.to(m.scale, 'x', p.sc, 0.3, 'outCubic');
        twain.to(m.scale, 'y', p.sc, 0.3, 'outCubic');
      }
      twain.to(hovered.glow.material, 'opacity', 0, 0.3, 'outCubic');
      twain.to(m.scale, 'x', FAN_SCALE, 0.3, 'outCubic');
      twain.to(m.scale, 'y', FAN_SCALE, 0.3, 'outCubic');
    } else if (hovered && hovered.state === 'drawn') {
      twain.to(hovered.mesh.position, 'z', (hovered.mesh.userData.slotZ ?? hovered.mesh.position.z), 0.3, 'outCubic');
      twain.to(hovered.glow.material, 'opacity', 0, 0.3, 'outCubic');
    }
    hovered = entry;
    if (entry && mode === 'select' && entry.state === 'fan') {
      const m = entry.mesh;
      twain.to(m.position, 'z', m.position.z + 0.9, 0.25, 'outCubic');
      twain.to(entry.glow.material, 'opacity', 0.85, 0.25, 'outCubic');
      twain.to(m.scale, 'x', FAN_SCALE * 1.08, 0.25, 'outCubic');
      twain.to(m.scale, 'y', FAN_SCALE * 1.08, 0.25, 'outCubic');
    } else if (entry && mode === 'layout' && entry.state === 'drawn') {
      entry.mesh.userData.slotZ = entry.mesh.position.z;
      twain.to(entry.mesh.position, 'z', entry.mesh.position.z + 0.35, 0.3, 'outCubic');
      twain.to(entry.glow.material, 'opacity', 0.5, 0.3, 'outCubic');
    }
    onHover && onHover(entry ? entry.card : null);
  }

  const el = stage.renderer.domElement;
  function updatePointer(clientX, clientY) {
    const rect = el.getBoundingClientRect();
    if (!rect.width || !rect.height) return false;
    pointer.set(
      ((clientX - rect.left) / rect.width) * 2 - 1,
      -((clientY - rect.top) / rect.height) * 2 + 1,
    );
    return true;
  }

  function pickEntry() {
    raycaster.setFromCamera(pointer, camera);
    const meshes = (mode === 'select' ? fanEntries() : cards.filter(e => e.state === 'drawn'))
      .map(e => e.mesh);
    const hit = raycaster.intersectObjects(meshes, true)[0];
    return hit ? hit.object.userData.entry : null;
  }

  el.addEventListener('pointermove', e => {
    updatePointer(e.clientX, e.clientY);
  }, { passive: true });

  const navigation = attachCanvasNavigation({
    element:el, camera, rig,
    canPan:() => mode === 'select' || mode === 'layout',
    canZoom:() => mode === 'layout',
    onInteract:() => {
      setHovered(null);
      for (const axis of ['x','y','z']) twain.kill(rig.base, axis);
    },
    onTap:tapPoint => {
      const tapped = tapPoint && updatePointer(tapPoint.x, tapPoint.y)
        ? pickEntry()
        : hovered;
      setHovered(tapped);
      if (tapped) onSelect && onSelect(tapped);
      else onEmptyClick && onEmptyClick();
    },
  });

  function layoutSlots(spread, scale) {
    return spread.slots.map((s, i) => ({
      i,
      pos: new THREE.Vector3(s.x * scale * CARD_W, -s.y * scale * CARD_W, 1.2 + (s.z || 0) + i * 0.02),
      rot: s.rot || 0,
      scale,
      reversed: false,
      label: s.label, hint: s.hint,
    }));
  }

  function fitCamera(spread, scale) {
    navigation.reset();
    let minX = 1e9, maxX = -1e9, minY = 1e9, maxY = -1e9;
    for (const s of spread.slots) {
      const c = Math.abs(Math.cos(s.rot || 0)), si = Math.abs(Math.sin(s.rot || 0));
      const rw = (c * CARD_W + si * CARD_H) * scale / 2;
      const rh = (si * CARD_W + c * CARD_H) * scale / 2;
      minX = Math.min(minX, s.x * scale * CARD_W - rw);
      maxX = Math.max(maxX, s.x * scale * CARD_W + rw);
      minY = Math.min(minY, -s.y * scale * CARD_W - rh);
      maxY = Math.max(maxY, -s.y * scale * CARD_W + rh);
    }
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2 - 0.3;
    const halfH = (maxY - minY) / 2 + 1.5;
    const halfW = (maxX - minX) / 2 + 1.1;
    const fov = camera.fov * Math.PI / 180;
    // 解读面板固定在右侧：取景宽度须同时容纳「牌阵 + 让位」，联立求解
    const panelW = Math.min(500, window.innerWidth * 0.94);
    const panelFrac = panelW / window.innerWidth;
    const halfLayoutW = (maxX - minX) / 2;
    const zW = (halfLayoutW / (1 - panelFrac)) / (Math.tan(fov / 2) * camera.aspect);
    const zH = halfH / Math.tan(fov / 2);
    const z = Math.max(zH, zW) * 1.18;
    const visW = 2 * Math.tan(fov / 2) * z * camera.aspect;
    const shift = (visW * panelFrac) / 2;
    twain.to(rig.base, 'x', cx + shift, 1.5, 'inOutSine');
    twain.to(rig.base, 'y', cy, 1.5, 'inOutSine');
    twain.to(rig.base, 'z', z, 1.7, 'inOutSine');
    return { cx: cx + shift, cy, z };
  }

  function resetCamera() {
    navigation.reset();
    twain.to(rig.base, 'x', 0, 1.2, 'inOutSine');
    twain.to(rig.base, 'y', 0, 1.2, 'inOutSine');
    twain.to(rig.base, 'z', 17, 1.2, 'inOutSine');
  }

  onFrame((dt, t) => {
    twain.update(dt);
    bursts.update(dt);
    backMaterial().uniforms.uTime.value = t;

    if (shuffleState) {
      const st = shuffleState;
      st.t += dt;
      const T = st.dur;
      for (const e of st.entries) {
        const m = e.mesh;
        const ph = e.shufflePhase;
        const k = Math.min(st.t / T, 1);
        if (k < 0.5) {
          const u = k / 0.5, s = 1 - Math.pow(1 - u, 3);
          const swirl = ph + s * Math.PI * 2 * 1.4;
          const r = e.r0 + (e.shuffleR - e.r0) * u;
          const zT = 0.9 + Math.sin(u * Math.PI) * 3.2 + e.shuffleI * 1.4;
          m.position.set(
            Math.cos(swirl) * r * 1.25,
            Math.sin(swirl) * r * 0.55 + FAN_BASE_Y - 1,
            e.z0 + (zT - e.z0) * u,
          );
          m.rotation.z = e.rot0 + ((swirl + Math.PI / 2) - e.rot0) * u;
        } else {
          const u = (k - 0.5) / 0.5;
          const idx = st.order.indexOf(e);
          const p = fanPose(idx, st.order.length);
          const ease = 1 - Math.pow(1 - u, 4);
          const swirlEnd = ph + Math.PI * 2 * 1.4;
          const sx = Math.cos(swirlEnd) * e.shuffleR * 1.25;
          const sy = Math.sin(swirlEnd) * e.shuffleR * 0.55 + FAN_BASE_Y - 1;
          const sz = 0.9 + e.shuffleI * 1.4;
          void 0;
          m.position.set(
            sx + (p.x - sx) * ease,
            sy + (p.y - sy) * ease,
            sz + (p.z - sz) * ease,
          );
          m.rotation.z = (swirlEnd + Math.PI / 2) * (1 - ease) + p.rot * ease;
        }
      }
      if (st.t >= T) {
        st.entries.forEach(e => { e.state = 'fan'; });
        mode = 'fan';
        shuffleState = null;
        onShuffleDone && onShuffleDone();
      }
    }

    // 布局后卡牌的微息浮动
    if (!shuffleState) {
      for (const e of cards) {
        if (e.state === 'drawn') {
          e.mesh.position.y += Math.sin(t * 1.15 + e.idx * 1.71) * 0.00035;
        }
      }
    }

    // 悬停检测（拖拽中暂停）
    if ((mode === 'select' || mode === 'layout') && !navigation.isInteracting) {
      const entry = pickEntry();
      if (mode === 'select') el.style.cursor = entry ? 'pointer' : 'default';
      else el.style.cursor = entry ? 'pointer' : 'default';
      setHovered(entry);
    } else if (hovered) {
      setHovered(null);
      el.style.cursor = 'default';
    }
  });

  return {
    build, intro, shuffle, beginSelection, endSelection,
    flyToSlot, revealTogether, restoreLayout, layoutSlots, fitCamera, resetCamera, refan,
    set onSelect(cb) { onSelect = cb; },
    set onHover(cb) { onHover = cb; },
    set onEmptyClick(cb) { onEmptyClick = cb; },
    get mode() { return mode; },
    cards,
  };
}
