import { useEffect, useRef, type ReactNode } from "react";
import { Button } from "./Button";

/** Native <dialog>: focus trap, Escape to close and inert background come from the browser. */
export function ConfirmDialog({ open, title, children, confirmLabel, danger, busy, onConfirm, onClose }: {
  open: boolean; title: string; children: ReactNode; confirmLabel: string; danger?: boolean; busy?: boolean;
  onConfirm: () => void; onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal?.();
    if (!open && d.open) d.close?.();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onCancel={(e) => { if (busy) e.preventDefault(); }}
      aria-labelledby="confirm-title"
      className="panel m-auto w-[min(92vw,30rem)] p-0 text-fg backdrop:bg-[#111318]/25 backdrop:backdrop-blur-sm"
    >
      {open && (
        <div className="p-5">
          <h2 id="confirm-title" className="text-base font-semibold">{title}</h2>
          <div className="mt-2 space-y-3 text-sm text-muted">{children}</div>
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
            <Button variant={danger ? "danger" : "primary"} onClick={onConfirm} loading={busy}>{confirmLabel}</Button>
          </div>
        </div>
      )}
    </dialog>
  );
}
