/**
 * In-app delete confirmation. Replaces window.confirm() so the prompt
 * matches the rest of the app (panel / btn-primary / btn-secondary).
 */
export default function ConfirmDeleteModal({
  open,
  title = "Delete this scan?",
  message,
  confirmLabel = "Delete",
  cancelLabel = "Cancel",
  onConfirm,
  onCancel,
}) {
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-ink-950/70"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-delete-title"
      onClick={onCancel}
    >
      <div
        className="panel w-full max-w-md p-6 space-y-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="confirm-delete-title" className="font-display font-semibold text-mist-100">
          {title}
        </h2>
        <p className="text-sm text-mist-300">{message}</p>
        <div className="flex justify-end gap-3 pt-2">
          <button type="button" className="btn-secondary" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className="btn-primary bg-rose-500 hover:bg-rose-400 text-white"
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
