interface NumericKeypadProps {
  value: string;
  onChange: (value: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
  title?: string;
  maxLength?: number;
  masked?: boolean;
}

const DIGIT_KEYS: string[] = ["1", "2", "3", "4", "5", "6", "7", "8", "9"];

/**
 * Custom on-screen numeric input overlay for touch panels.
 * Renders a 3x4 grid: digits 1-9, backspace, 0, confirm.
 * All buttons >= 44x44px for WCAG touch target compliance.
 */
export default function NumericKeypad({
  value,
  onChange,
  onConfirm,
  onCancel,
  title = "Enter Value",
  maxLength = 10,
  masked = false,
}: NumericKeypadProps): React.ReactElement {
  const handleDigit = (digit: string): void => {
    if (value.length < maxLength) {
      onChange(value + digit);
    }
  };

  const handleBackspace = (): void => {
    onChange(value.slice(0, -1));
  };

  const displayValue: string = masked ? "\u2022".repeat(value.length) : value;

  return (
    <div
      data-testid="numeric-keypad"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onCancel}
    >
      <div
        className="w-[320px] rounded-xl bg-bg-secondary p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-2 text-center text-lg font-semibold text-text-primary">
          {title}
        </h2>

        <div
          data-testid="keypad-display"
          className="mb-4 flex min-h-[48px] items-center justify-center rounded-lg bg-bg-card px-4 text-2xl font-mono text-text-primary"
        >
          {displayValue || "\u00A0"}
        </div>

        <div className="grid grid-cols-3 gap-2">
          {DIGIT_KEYS.map((digit) => (
            <button
              key={digit}
              type="button"
              data-testid={`key-${digit}`}
              className="flex min-h-[52px] min-w-[52px] items-center justify-center rounded-lg bg-bg-card text-xl font-semibold text-text-primary active:bg-accent"
              onClick={() => handleDigit(digit)}
            >
              {digit}
            </button>
          ))}

          <button
            type="button"
            data-testid="key-backspace"
            className="flex min-h-[52px] min-w-[52px] items-center justify-center rounded-lg bg-bg-card text-xl text-text-secondary active:bg-accent"
            onClick={handleBackspace}
          >
            &#x232B;
          </button>

          <button
            type="button"
            data-testid="key-0"
            className="flex min-h-[52px] min-w-[52px] items-center justify-center rounded-lg bg-bg-card text-xl font-semibold text-text-primary active:bg-accent"
            onClick={() => handleDigit("0")}
          >
            0
          </button>

          <button
            type="button"
            data-testid="key-confirm"
            className="flex min-h-[52px] min-w-[52px] items-center justify-center rounded-lg bg-accent text-xl font-semibold text-text-primary active:bg-accent/80"
            onClick={onConfirm}
          >
            &#x2713;
          </button>
        </div>
      </div>
    </div>
  );
}
