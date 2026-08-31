import { useEffect } from "react";

interface ToastProps {
  message: string;
  actionLabel?: string;
  onAction?: () => void;
  onDismiss: () => void;
  durationMs?: number;
}

// Generic toast: a plain notice (the sparse-result fallback message, SPEC
// section 4) or an undo toast (US-2's "any removal can be undone from a
// toast — never a confirmation dialog"). It never blocks interaction with
// the page behind it.
export default function Toast({ message, actionLabel, onAction, onDismiss, durationMs = 6000 }: ToastProps) {
  useEffect(() => {
    const timer = window.setTimeout(onDismiss, durationMs);
    return () => window.clearTimeout(timer);
  }, [onDismiss, durationMs]);

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed bottom-4 start-4 end-4 z-50 flex items-center justify-between gap-3 rounded-xl bg-slate-900 px-4 py-3 text-sm text-white shadow-lg sm:start-auto sm:max-w-md"
    >
      <span>{message}</span>
      <div className="flex shrink-0 items-center gap-2">
        {actionLabel && onAction && (
          <button
            type="button"
            onClick={() => {
              onAction();
              onDismiss();
            }}
            className="rounded-full bg-white/10 px-3 py-1 font-medium transition-colors hover:bg-white/20"
          >
            {actionLabel}
          </button>
        )}
        <button
          type="button"
          aria-label="סגור הודעה"
          onClick={onDismiss}
          className="text-white/70 transition-colors hover:text-white"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
