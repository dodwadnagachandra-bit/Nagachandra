interface ConfirmDialogProps {
  title: string;
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: "danger" | "default";
}

/**
 * Modal confirmation dialog for destructive or important actions.
 * Buttons >= 44x44px for WCAG touch target compliance.
 */
export default function ConfirmDialog({
  title,
  message,
  onConfirm,
  onCancel,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  variant = "default",
}: ConfirmDialogProps): React.ReactElement {
  const confirmBg: string = variant === "danger" ? "bg-danger" : "bg-accent";

  return (
    <div
      data-testid="confirm-dialog"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onCancel}
    >
      <div
        className="w-[360px] rounded-xl bg-bg-secondary p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-2 text-lg font-semibold text-text-primary">{title}</h2>
        <p className="mb-6 text-text-secondary">{message}</p>

        <div className="flex gap-3">
          <button
            type="button"
            data-testid="confirm-cancel"
            className="flex min-h-[44px] flex-1 items-center justify-center rounded-lg bg-bg-card text-text-primary active:bg-bg-card/80"
            onClick={onCancel}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            data-testid="confirm-ok"
            className={`flex min-h-[44px] flex-1 items-center justify-center rounded-lg ${confirmBg} text-white active:opacity-80`}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
