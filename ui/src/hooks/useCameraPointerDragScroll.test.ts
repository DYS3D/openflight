import { describe, expect, it, vi } from 'vitest';
import {
  CAMERA_DRAG_SCROLL_SELECTOR,
  createCameraPointerDragHandlers,
  dragScrollTop,
  shouldStartCameraPointerDrag,
} from './useCameraPointerDragScroll';

function pointer(clientY: number) {
  return {
    button: 0,
    isPrimary: true,
    pointerType: 'mouse',
    pointerId: 1,
    clientX: 0,
    clientY,
    target: null,
    preventDefault: vi.fn(),
  };
}

function click() {
  return { preventDefault: vi.fn(), stopPropagation: vi.fn() };
}

function setup() {
  const region = { scrollTop: 100 } as HTMLElement;
  return { region, handlers: createCameraPointerDragHandlers(() => region) };
}

describe('camera pointer drag scrolling', () => {
  it('moves the settings panel opposite the pointer drag', () => {
    expect(dragScrollTop(120, 100, 140)).toBe(80);
    expect(dragScrollTop(120, 100, 60)).toBe(160);
  });

  it('handles the mouse-emulated primary pointer used by the kiosk touchscreen', () => {
    expect(shouldStartCameraPointerDrag({ button: 0, isPrimary: true, pointerType: 'mouse' })).toBe(true);
    expect(shouldStartCameraPointerDrag({ button: 0, isPrimary: true, pointerType: 'touch' })).toBe(false);
    expect(shouldStartCameraPointerDrag({ button: 1, isPrimary: true, pointerType: 'mouse' })).toBe(false);
  });

  it('is scoped to camera regions', () => {
    expect(CAMERA_DRAG_SCROLL_SELECTOR).toContain('.camera-settings');
    expect(CAMERA_DRAG_SCROLL_SELECTOR).toContain('.camera-feed__workspace');
    expect(CAMERA_DRAG_SCROLL_SELECTOR).not.toContain('.shot-list__rows');
  });

  it('treats jitter below the shared tap threshold as a tap', () => {
    const { region, handlers } = setup();
    handlers.onPointerDown(pointer(200));
    handlers.onPointerMove(pointer(190));
    handlers.onPointerEnd(pointer(190));

    expect(region.scrollTop).toBe(100);
    const tap = click();
    handlers.onClickCapture(tap);
    expect(tap.preventDefault).not.toHaveBeenCalled();
    expect(tap.stopPropagation).not.toHaveBeenCalled();
  });

  it('swallows the click that ends a drag, then lets the next tap through', () => {
    const { region, handlers } = setup();
    handlers.onPointerDown(pointer(200));
    handlers.onPointerMove(pointer(160));
    handlers.onPointerEnd(pointer(160));

    expect(region.scrollTop).toBe(140);
    const afterDrag = click();
    handlers.onClickCapture(afterDrag);
    expect(afterDrag.preventDefault).toHaveBeenCalled();
    expect(afterDrag.stopPropagation).toHaveBeenCalled();

    const nextTap = click();
    handlers.onClickCapture(nextTap);
    expect(nextTap.stopPropagation).not.toHaveBeenCalled();
  });
});
