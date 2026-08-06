import { useCallback, useEffect, useState } from "react";
import ConfirmDialog from "../components/ConfirmDialog";
import NumericKeypad from "../components/NumericKeypad";
import { useAuth } from "../context/AuthContext";
import { useApi } from "../hooks/useApi";
import { useTelemetry } from "../hooks/useTelemetry";
import type { ZmqResponse } from "../types/api";
import { CONTROL_STATE_NAMES } from "../types/enums";

/** Mode buttons for mode selector */
const MODE_OPTIONS: { label: string; stateNum: number }[] = [
  { label: "IDLE", stateNum: 1 },
  { label: "STANDBY", stateNum: 2 },
  { label: "CHARGING", stateNum: 3 },
  { label: "DISCHARGING", stateNum: 4 },
];

/** Color class for control state display */
function stateColorClass(state: number): string {
  if (state === 5 || state === 6) return "text-danger";
  if (state === 7) return "text-warning";
  if (state >= 1 && state <= 4) return "text-success";
  return "text-text-primary";
}

/** Background color class for state badge */
function stateBgClass(state: number): string {
  if (state === 5 || state === 6) return "bg-danger/20";
  if (state === 7) return "bg-warning/20";
  if (state >= 1 && state <= 4) return "bg-success/20";
  return "bg-bg-card";
}

interface Feedback {
  text: string;
  type: "success" | "error";
}

export default function ControlScreen(): React.ReactElement {
  const system = useTelemetry("system");
  const { state: authState } = useAuth();
  const { apiFetch } = useApi();

  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [confirmDialog, setConfirmDialog] = useState<{
    title: string;
    message: string;
    onConfirm: () => void;
    variant?: "danger" | "default";
  } | null>(null);
  const [showKeypad, setShowKeypad] = useState<boolean>(false);
  const [keypadValue, setKeypadValue] = useState<string>("");

  // Auto-clear feedback after 3 seconds
  useEffect(() => {
    if (feedback) {
      const t = setTimeout(() => setFeedback(null), 3000);
      return () => clearTimeout(t);
    }
  }, [feedback]);

  /** Send a command and show feedback */
  const sendCommand = useCallback(
    async (path: string, body?: Record<string, unknown>): Promise<void> => {
      try {
        const resp = await apiFetch<ZmqResponse>(path, {
          method: "POST",
          ...(body ? { body: JSON.stringify(body) } : {}),
        });
        if (resp.status === "ok") {
          setFeedback({ text: "Command sent successfully", type: "success" });
        } else {
          setFeedback({
            text: resp.error_msg ?? "Command failed",
            type: "error",
          });
        }
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : "Command failed";
        setFeedback({ text: msg, type: "error" });
      }
    },
    [apiFetch],
  );

  /** Handle mode button click */
  const handleModeClick = (label: string): void => {
    setConfirmDialog({
      title: "Change Mode",
      message: `Change mode to ${label}?`,
      onConfirm: () => {
        setConfirmDialog(null);
        sendCommand("/api/control/mode", {
          target_state: label.toLowerCase(),
        });
      },
    });
  };

  /** Handle setpoint confirmation from keypad */
  const handleSetpointConfirm = (): void => {
    const value = parseFloat(keypadValue);
    if (isNaN(value)) {
      setShowKeypad(false);
      setKeypadValue("");
      return;
    }
    setShowKeypad(false);
    setConfirmDialog({
      title: "Set Power",
      message: `Set power to ${value} kW?`,
      onConfirm: () => {
        setConfirmDialog(null);
        setKeypadValue("");
        sendCommand("/api/control/setpoint", { power_kw: value });
      },
    });
  };

  /** Handle maintenance enter/exit */
  const handleMaintenance = (action: "enter" | "exit"): void => {
    const isEnter = action === "enter";
    setConfirmDialog({
      title: isEnter ? "Enter Maintenance" : "Exit Maintenance",
      message: isEnter
        ? "Enter maintenance mode?"
        : "Exit maintenance mode?",
      variant: isEnter ? "danger" : "default",
      onConfirm: () => {
        setConfirmDialog(null);
        sendCommand("/api/control/maintenance", { action });
      },
    });
  };

  /** Handle fault reset */
  const handleFaultReset = (): void => {
    setConfirmDialog({
      title: "Fault Reset",
      message: "Reset fault condition?",
      onConfirm: () => {
        setConfirmDialog(null);
        sendCommand("/api/control/fault-reset");
      },
    });
  };

  // Null telemetry guard
  if (!system) {
    return (
      <div
        data-testid="screen-control"
        className="flex h-full items-center justify-center"
      >
        <p className="text-lg text-text-secondary">Waiting for data...</p>
      </div>
    );
  }

  const controlName: string =
    CONTROL_STATE_NAMES[system.control_state] ?? "UNKNOWN";
  const isAdmin: boolean = authState.level === "admin";
  const isFault: boolean = system.control_state === 5;
  const isMaintenance: boolean = system.control_state === 7;
  const numFmt = new Intl.NumberFormat("en", { maximumFractionDigits: 1 });

  return (
    <div data-testid="screen-control" className="p-4">
      <h2 className="mb-4 text-2xl font-bold">Control</h2>

      {/* Feedback message */}
      {feedback && (
        <div
          data-testid="feedback-msg"
          className={`mb-4 rounded-lg px-4 py-2 text-sm ${
            feedback.type === "success"
              ? "bg-success/20 text-success"
              : "bg-danger/20 text-danger"
          }`}
        >
          {feedback.text}
        </div>
      )}

      {/* Current State Display */}
      <div className="mb-6 rounded-lg bg-bg-card p-6">
        <h3 className="mb-2 text-sm font-medium text-text-secondary">
          Current State
        </h3>
        <div className="flex items-center gap-4">
          <span
            className={`inline-block rounded-lg px-4 py-2 text-2xl font-bold ${stateColorClass(system.control_state)} ${stateBgClass(system.control_state)}`}
          >
            {controlName}
          </span>
          <div className="text-text-secondary">
            <span className="text-lg font-semibold text-text-primary">
              {numFmt.format(system.active_setpoint_kw)}
            </span>{" "}
            kW setpoint
          </div>
        </div>
      </div>

      {/* Mode Selector */}
      <div className="mb-6">
        <h3 className="mb-3 text-sm font-medium text-text-secondary">
          Mode Selector
        </h3>
        <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          {MODE_OPTIONS.map((mode) => {
            const isCurrentState = system.control_state === mode.stateNum;
            return (
              <button
                key={mode.label}
                type="button"
                data-testid={`mode-btn-${mode.label}`}
                disabled={isCurrentState}
                className={`min-h-[44px] rounded-lg px-4 py-3 text-sm font-semibold ${
                  isCurrentState
                    ? "bg-accent text-white opacity-60"
                    : "bg-bg-card text-text-primary active:bg-accent"
                }`}
                onClick={() => handleModeClick(mode.label)}
              >
                {mode.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Source Priority Selector */}
      <div className="mb-6">
        <h3 className="mb-3 text-sm font-medium text-text-secondary">
          Source Priority
        </h3>
        <div className="grid grid-cols-3 gap-3">
          {(["day", "night", "manual"] as const).map((mode) => {
            const current = system.source_priority === (mode === "day" ? 0 : mode === "night" ? 1 : 2);
            return (
              <button
                key={mode}
                type="button"
                data-testid={`priority-btn-${mode}`}
                disabled={current}
                className={`min-h-[44px] rounded-lg px-4 py-3 text-sm font-semibold uppercase ${
                  current
                    ? "bg-accent text-white opacity-60"
                    : "bg-bg-card text-text-primary active:bg-accent"
                }`}
                onClick={() => {
                  setConfirmDialog({
                    title: "Change Source Priority",
                    message: `Switch source priority to ${mode.toUpperCase()}?`,
                    onConfirm: () => {
                      setConfirmDialog(null);
                      sendCommand("/api/control/priority", { mode });
                    },
                  });
                }}
              >
                {mode}
              </button>
            );
          })}
        </div>
      </div>

      {/* Setpoint Input */}
      <div className="mb-6">
        <h3 className="mb-3 text-sm font-medium text-text-secondary">
          Power Setpoint
        </h3>
        <button
          type="button"
          data-testid="setpoint-btn"
          className="min-h-[44px] rounded-lg bg-bg-card px-6 py-3 text-text-primary active:bg-accent"
          onClick={() => {
            setKeypadValue("");
            setShowKeypad(true);
          }}
        >
          Set Power (kW)
        </button>
      </div>

      {/* Action Buttons */}
      <div className="flex flex-wrap gap-3">
        {/* Fault Reset -- visible only in FAULT state */}
        {isFault && (
          <button
            type="button"
            data-testid="fault-reset-btn"
            className="min-h-[44px] rounded-lg bg-danger px-6 py-3 font-semibold text-white active:bg-danger/80"
            onClick={handleFaultReset}
          >
            Fault Reset
          </button>
        )}

        {/* Maintenance Enter -- admin only, not in maintenance */}
        {isAdmin && !isMaintenance && (
          <button
            type="button"
            data-testid="maintenance-enter-btn"
            className="min-h-[44px] rounded-lg bg-warning px-6 py-3 font-semibold text-black active:bg-warning/80"
            onClick={() => handleMaintenance("enter")}
          >
            Enter Maintenance
          </button>
        )}

        {/* Maintenance Exit -- admin only, in maintenance */}
        {isAdmin && isMaintenance && (
          <button
            type="button"
            data-testid="maintenance-exit-btn"
            className="min-h-[44px] rounded-lg bg-success px-6 py-3 font-semibold text-white active:bg-success/80"
            onClick={() => handleMaintenance("exit")}
          >
            Exit Maintenance
          </button>
        )}
      </div>

      {/* Confirm Dialog */}
      {confirmDialog && (
        <ConfirmDialog
          title={confirmDialog.title}
          message={confirmDialog.message}
          variant={confirmDialog.variant}
          onConfirm={confirmDialog.onConfirm}
          onCancel={() => setConfirmDialog(null)}
        />
      )}

      {/* Numeric Keypad */}
      {showKeypad && (
        <NumericKeypad
          value={keypadValue}
          onChange={setKeypadValue}
          onConfirm={handleSetpointConfirm}
          onCancel={() => {
            setShowKeypad(false);
            setKeypadValue("");
          }}
          title="Enter Power (kW)"
        />
      )}
    </div>
  );
}
