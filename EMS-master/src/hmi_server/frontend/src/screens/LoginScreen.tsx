import { useState } from "react";
import NumericKeypad from "../components/NumericKeypad";
import { useAuth } from "../context/AuthContext";

/**
 * PIN entry screen. Shown when not authenticated.
 * Uses NumericKeypad for touch-friendly numeric input.
 */
export default function LoginScreen(): React.ReactElement {
  const { login } = useAuth();
  const [pin, setPin] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(false);

  const handleConfirm = async (): Promise<void> => {
    if (pin.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      await login(pin);
    } catch {
      setError("Invalid PIN. Please try again.");
      setPin("");
    } finally {
      setLoading(false);
    }
  };

  const handleCancel = (): void => {
    setPin("");
    setError(null);
  };

  return (
    <div
      data-testid="screen-login"
      className="flex h-screen items-center justify-center bg-bg-primary"
    >
      <div className="w-[360px]">
        <h1 className="mb-6 text-center text-2xl font-bold text-text-primary">
          EMS Login
        </h1>

        {error && (
          <div
            data-testid="login-error"
            className="mb-4 rounded-lg bg-danger/20 px-4 py-2 text-center text-danger"
          >
            {error}
          </div>
        )}

        <NumericKeypad
          value={pin}
          onChange={setPin}
          onConfirm={handleConfirm}
          onCancel={handleCancel}
          title="Enter PIN"
          maxLength={6}
          masked={true}
        />

        {loading && (
          <div className="mt-4 text-center text-text-secondary">
            Authenticating...
          </div>
        )}
      </div>
    </div>
  );
}
