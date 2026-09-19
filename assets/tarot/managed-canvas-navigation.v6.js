// Canvas-local navigation with touch tap coordinates preserved for immediate picking.
export function attachCanvasNavigation({element, camera, rig, canPan, canZoom, onInteract, onTap}) {
  const pointers = new Map();
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const tapSlop = pointerType => pointerType === 'touch' ? 10 : pointerType === 'pen' ? 8 : 6;
  let origin, moved = false, gesture = null;
  const point = e => ({x:e.clientX, y:e.clientY, pointerType:e.pointerType || 'mouse'});
  function geometry() {
    const [a,b] = [...pointers.values()];
    return b ? {x:(a.x+b.x)/2, y:(a.y+b.y)/2, distance:Math.hypot(a.x-b.x,a.y-b.y)} : a;
  }
  function transform(from, to, zoom = camera.zoom) {
    if (!Number.isFinite(zoom) || zoom <= 0) return;
    const rect = element.getBoundingClientRect();
    if (!rect.height || !rect.width) return;
    const next = clamp(zoom, .5, 4);
    const wpp = 2 * Math.tan(camera.fov * Math.PI / 360) * Math.max(rig.base.z, 2) / rect.height;
    const cx = rect.left + rect.width/2, cy = rect.top + rect.height/2;
    onInteract();
    rig.base.x = clamp(rig.base.x + (from.x-cx)*wpp/camera.zoom - (to.x-cx)*wpp/next, -9, 9);
    rig.base.y = clamp(rig.base.y - (from.y-cy)*wpp/camera.zoom + (to.y-cy)*wpp/next, -6, 6);
    if (next !== camera.zoom) {
      camera.zoom = next;
      camera.updateProjectionMatrix();
      // Keep the anchor in place instead of easing pan behind an immediate zoom.
      if (rig.target) { rig.target.x = rig.base.x; rig.target.y = rig.base.y; }
    }
  }
  element.addEventListener('pointerdown', e => {
    if (!canPan() || (e.pointerType === 'mouse' && e.button !== 0) || pointers.size >= 2) return;
    pointers.set(e.pointerId, point(e));
    origin = geometry();
    if (pointers.size === 1) moved = false;
    else { moved = true; onInteract(); }
    try { element.setPointerCapture(e.pointerId); } catch {}
  });
  element.addEventListener('pointermove', e => {
    if (!pointers.has(e.pointerId)) return;
    if (!canPan()) { clearPointers(); return; }
    const before = geometry();
    pointers.set(e.pointerId, point(e));
    const after = geometry();
    if (pointers.size === 2) {
      moved = true;
      if (!gesture && canZoom() && before.distance > 0 && after.distance > 0) {
        transform(before, after, camera.zoom * after.distance / before.distance);
      }
    } else {
      if (!moved && Math.hypot(after.x-origin.x, after.y-origin.y) <= tapSlop(e.pointerType)) return;
      transform(moved ? before : origin, after);
      moved = true;
    }
    element.style.cursor = 'grabbing';
  });
  function release(id) {
    pointers.delete(id);
    try { element.releasePointerCapture(id); } catch {}
  }
  function end(e, cancelled) {
    if (!pointers.has(e.pointerId)) return;
    const finalPoint = point(e);
    const withinTapSlop = origin && Math.hypot(finalPoint.x-origin.x, finalPoint.y-origin.y)
      <= tapSlop(finalPoint.pointerType);
    const tap = !cancelled && !moved && !gesture && pointers.size === 1
      && withinTapSlop && canPan();
    release(e.pointerId);
    if (pointers.size) { moved = true; origin = geometry(); }
    else { moved = false; element.style.cursor = 'default'; }
    if (tap) onTap(finalPoint);
  }
  element.addEventListener('pointerup', e => end(e, false));
  element.addEventListener('pointercancel', e => end(e, true));
  element.addEventListener('lostpointercapture', e => end(e, true));
  function clearPointers() {
    for (const id of [...pointers.keys()]) release(id);
    moved = false; gesture = null; element.style.cursor = 'default';
  }
  element.addEventListener('wheel', e => {
    if (!canZoom() || !Number.isFinite(e.deltaY)) return;
    e.preventDefault();
    if (gesture || pointers.size > 1) return;
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? element.getBoundingClientRect().height : 1;
    const exponent = clamp(-e.deltaY * unit * (e.ctrlKey ? .01 : .002), -20, 20);
    transform(point(e), point(e), camera.zoom * Math.exp(exponent));
  }, {passive:false});
  // Safari trackpads emit GestureEvents instead of (or alongside) ctrl+wheel.
  element.addEventListener('gesturestart', e => {
    if (!canZoom()) return;
    e.preventDefault();
    gesture = {zoom:camera.zoom, scale:Number.isFinite(e.scale) && e.scale > 0 ? e.scale : 1, point:point(e)};
    moved = true; onInteract();
  }, {passive:false});
  element.addEventListener('gesturechange', e => {
    if (!gesture || !canZoom()) return;
    e.preventDefault();
    if (!Number.isFinite(e.scale) || e.scale <= 0) return;
    transform(gesture.point, point(e), gesture.zoom * e.scale / gesture.scale);
    gesture.point = point(e);
  }, {passive:false});
  element.addEventListener('gestureend', e => {
    if (!gesture) return;
    e.preventDefault(); gesture = null;
    if (!pointers.size) moved = false;
  }, {passive:false});
  return {
    get isInteracting() { return Boolean(gesture) || (pointers.size > 0 && moved); },
    reset() { clearPointers(); camera.zoom = 1; camera.updateProjectionMatrix(); },
  };
}
