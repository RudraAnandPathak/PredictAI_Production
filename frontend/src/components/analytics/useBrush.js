import { useCallback, useRef, useState } from 'react';
import { rectOf } from '../../features/analytics/chartMath.js';

export const VIEW = Object.freeze({ w: 720, h: 380 });

/** Converts pointer positions to SVG viewBox coordinates and tracks a drag (range selection). `mode` is 'x' (a band) or 'xy' (a rectangle). */
export function useBrush({ mode = 'xy', area, onDone, onMove }) {
  const svgRef = useRef(null);
  const start = useRef(null);
  const [rect, setRect] = useState(null);

  const toView = useCallback((event) => {
    const box = svgRef.current.getBoundingClientRect();
    return { x: ((event.clientX - box.left) / box.width) * VIEW.w, y: ((event.clientY - box.top) / box.height) * VIEW.h };
  }, []);
  const clampPt = (p) => ({ x: Math.min(area.x1, Math.max(area.x0, p.x)), y: Math.min(area.y1, Math.max(area.y0, p.y)) });
  const shape = (a, b) => {
    const r = rectOf(a, b);
    return mode === 'x' ? { ...r, y0: area.y0, y1: area.y1 } : r;
  };

  const handlers = {
    onPointerDown(event) {
      if (event.button !== 0) return;
      event.currentTarget.setPointerCapture?.(event.pointerId);
      start.current = clampPt(toView(event));
      setRect(null);
    },
    onPointerMove(event) {
      const p = clampPt(toView(event));
      onMove?.(p, event);
      if (start.current) setRect(shape(start.current, p));
    },
    onPointerUp(event) {
      if (!start.current) return;
      const p = clampPt(toView(event));
      const r = shape(start.current, p);
      const moved = Math.abs(p.x - start.current.x) > 4 || (mode === 'xy' && Math.abs(p.y - start.current.y) > 4);
      start.current = null;
      setRect(null);
      if (moved) onDone?.(r);
    },
    onPointerLeave() { onMove?.(null); },
  };
  return { svgRef, rect, handlers, clear: () => setRect(null) };
}
