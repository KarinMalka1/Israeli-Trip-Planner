import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { PlaceImage } from "../types";

interface LightboxProps {
  images: PlaceImage[];
  name: string;
  onClose: () => void;
}

const FOCUSABLE_SELECTOR = 'button, a[href], [tabindex]:not([tabindex="-1"])';

// Navigation lives ONLY here, never on the card — StopCard's own image
// block still ever shows images[0] and nothing else. Without the count
// badge on the card (see StopCard), the other images would be reachable
// only by guessing there's a click-through to more of them, which is
// exactly the "hidden content" our accessibility rules forbid; the badge
// is what earns this component the right to hide images[1..] behind a
// click at all.
export default function Lightbox({ images, name, onClose }: LightboxProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const [visible, setVisible] = useState(false);
  const [index, setIndex] = useState(0);

  const total = images.length;
  const hasMultiple = total > 1;
  const current = images[index];

  // Wrap-around, not stop-at-ends: chosen so the two arrows are always
  // both enabled whenever there is more than one image — no separate
  // disabled-state logic to keep consistent with the "arrows are just
  // absent, not disabled" rule for the single-image case.
  function showPrevious() {
    setIndex((i) => (i - 1 + total) % total);
  }
  function showNext() {
    setIndex((i) => (i + 1) % total);
  }

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButtonRef.current?.focus();
    const frame = requestAnimationFrame(() => setVisible(true));
    return () => {
      document.body.style.overflow = previousOverflow;
      cancelAnimationFrame(frame);
    };
  }, []);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      // ArrowRight/ArrowLeft mirror whichever on-screen arrow sits on that
      // physical side (see the arrow buttons below) — previous is on the
      // right in RTL, next on the left — rather than a fixed logical
      // "forward/back" unrelated to where the buttons actually are.
      if (hasMultiple && event.key === "ArrowRight") {
        event.preventDefault();
        showPrevious();
        return;
      }
      if (hasMultiple && event.key === "ArrowLeft") {
        event.preventDefault();
        showNext();
        return;
      }
      // Focus trap: Tab/Shift+Tab cycles only through the dialog's own
      // focusable elements so keyboard focus can never land on whatever is
      // behind the dimmed backdrop.
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onClose, hasMultiple, total]);

  function handleBackdropClick(event: React.MouseEvent<HTMLDivElement>) {
    if (event.target === event.currentTarget) onClose();
  }

  return createPortal(
    <div
      className={
        "fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 transition-opacity duration-150 " +
        (visible ? "opacity-100" : "opacity-0")
      }
      onClick={handleBackdropClick}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={`תמונה של ${name}`}
        className="relative flex max-h-full max-w-full flex-col items-center gap-2"
      >
        <button
          ref={closeButtonRef}
          type="button"
          onClick={onClose}
          aria-label="סגירה"
          className="absolute top-2 end-2 z-10 grid cursor-pointer h-11 w-11 place-items-center rounded-full bg-black/60 text-white transition-colors hover:bg-black/80 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
        >
          <span aria-hidden="true" className="text-xl leading-none">
            ✕
          </span>
        </button>

        {hasMultiple && (
          <button
            type="button"
            onClick={showPrevious}
            aria-label="התמונה הקודמת"
            className="absolute top-1/2 start-2 z-10 grid cursor-pointer h-11 w-11 -translate-y-1/2 place-items-center rounded-full bg-black/60 text-white transition-colors hover:bg-black/80 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
          >
            <span aria-hidden="true" className="text-2xl leading-none">
              →
            </span>
          </button>
        )}

        <img src={current.url} alt={name} className="max-h-[80vh] max-w-full rounded-lg object-contain" />

        {hasMultiple && (
          <button
            type="button"
            onClick={showNext}
            aria-label="התמונה הבאה"
            className="absolute top-1/2 end-2 z-10 grid cursor-pointer h-11 w-11 -translate-y-1/2 place-items-center rounded-full bg-black/60 text-white transition-colors hover:bg-black/80 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
          >
            <span aria-hidden="true" className="text-2xl leading-none">
              ←
            </span>
          </button>
        )}

        {hasMultiple && (
          <p aria-live="polite" className="text-sm text-white/80">
            {index + 1} / {total}
          </p>
        )}

        <a
          href={current.source_url}
          target="_blank"
          rel="noopener noreferrer"
          title={current.credit}
          className="shrink-0 text-center text-sm text-white/80 hover:text-white"
        >
          {current.credit}
        </a>
      </div>
    </div>,
    document.body,
  );
}
