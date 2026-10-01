import { useEffect } from 'react';
import { DRAG_SCROLL_THRESHOLD_PX } from '../utils/dragScroll';

export const CAMERA_DRAG_SCROLL_SELECTOR = '.camera-settings, .camera-feed__workspace';

const INPUT_SELECTOR = "input, textarea, select, [contenteditable='true']";

type PointerStart = Pick<PointerEvent, 'button' | 'isPrimary' | 'pointerType'>;

export function shouldStartCameraPointerDrag(event: PointerStart): boolean {
  return event.pointerType === 'mouse' && event.isPrimary && event.button === 0;
}

export function dragScrollTop(startScrollTop: number, startY: number, currentY: number): number {
  return startScrollTop + startY - currentY;
}

function findScrollableRegion(target: EventTarget | null): HTMLElement | null {
  if (!(target instanceof Element) || target.closest(INPUT_SELECTOR)) return null;

  let region = target.closest<HTMLElement>(CAMERA_DRAG_SCROLL_SELECTOR);
  while (region && region.scrollHeight <= region.clientHeight + 1) {
    region = region.parentElement?.closest<HTMLElement>(CAMERA_DRAG_SCROLL_SELECTOR) ?? null;
  }
  return region;
}

type CameraDragEvent = Pick<
  PointerEvent,
  'button' | 'isPrimary' | 'pointerType' | 'pointerId' | 'clientX' | 'clientY' | 'target' | 'preventDefault'
>;
type ClickEvent = Pick<MouseEvent, 'preventDefault' | 'stopPropagation'>;

export function createCameraPointerDragHandlers(
  findRegion: (target: EventTarget | null) => HTMLElement | null = findScrollableRegion
) {
  let pointerId: number | null = null;
  let region: HTMLElement | null = null;
  let startX = 0;
  let startY = 0;
  let startScrollTop = 0;
  let dragging = false;
  let suppressClick = false;

  return {
    onPointerDown(event: CameraDragEvent) {
      if (!shouldStartCameraPointerDrag(event)) return;
      const scrollRegion = findRegion(event.target);
      if (!scrollRegion) return;

      pointerId = event.pointerId;
      region = scrollRegion;
      startX = event.clientX;
      startY = event.clientY;
      startScrollTop = scrollRegion.scrollTop;
      dragging = false;
      suppressClick = false;
    },

    onPointerMove(event: CameraDragEvent) {
      if (event.pointerId !== pointerId || !region) return;
      if (!dragging) {
        if (Math.hypot(event.clientX - startX, event.clientY - startY) < DRAG_SCROLL_THRESHOLD_PX) return;
        dragging = true;
      }
      event.preventDefault();
      region.scrollTop = dragScrollTop(startScrollTop, startY, event.clientY);
    },

    onPointerEnd(event: Pick<PointerEvent, 'pointerId'>) {
      if (event.pointerId !== pointerId) return;
      suppressClick = dragging;
      pointerId = null;
      region = null;
      dragging = false;
    },

    onClickCapture(event: ClickEvent) {
      if (!suppressClick) return;
      suppressClick = false;
      event.preventDefault();
      event.stopPropagation();
    },
  };
}

/** Support Pi touchscreen drivers that expose finger drags as mouse pointers. */
export function useCameraPointerDragScroll(): void {
  useEffect(() => {
    const { onPointerDown, onPointerMove, onPointerEnd, onClickCapture } = createCameraPointerDragHandlers();

    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('pointermove', onPointerMove, { capture: true, passive: false });
    document.addEventListener('pointerup', onPointerEnd, true);
    document.addEventListener('pointercancel', onPointerEnd, true);
    document.addEventListener('click', onClickCapture, true);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('pointermove', onPointerMove, true);
      document.removeEventListener('pointerup', onPointerEnd, true);
      document.removeEventListener('pointercancel', onPointerEnd, true);
      document.removeEventListener('click', onClickCapture, true);
    };
  }, []);
}
