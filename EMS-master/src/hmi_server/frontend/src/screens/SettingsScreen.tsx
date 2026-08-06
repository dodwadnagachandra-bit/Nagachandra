import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useTelemetryContext } from "../context/TelemetryContext";
import type { OtaTelemetry } from "../types/telemetry";
import type { ScheduleConfig, TimeWindow } from "../types/api";

type ScheduleMode = ScheduleConfig["mode"];

const DEFAULT_WINDOW: TimeWindow = {
  start: "06:00",
  end: "18:00",
  action: "charge",
  power_kw: 50,
};

/** Returns Tailwind badge color classes based on OTA state string. */
function otaStateBadgeClass(state: string): string {
  if (state === "idle") return "bg-gray-500 text-white";
  if (state === "downloading" || state === "verifying" || state === "applying") {
    return "bg-blue-500 text-white animate-pulse";
  }
  if (state === "rebooting") return "bg-amber-500 text-white";
  if (state === "failed") return "bg-red-500 text-white";
  return "bg-gray-500 text-white";
}

interface OtaStatusSectionProps {
  ota: OtaTelemetry | null;
}

function OtaStatusSection({ ota }: OtaStatusSectionProps): React.ReactElement {
  return (
    <div
      data-testid="ota-status-section"
      className="rounded-xl bg-bg-card p-4"
    >
      <h3 className="mb-2 text-lg font-semibold text-text-secondary">
        OTA Status
      </h3>
      {ota === null ? (
        <p className="text-sm text-text-secondary">
          OTA status: waiting for data...
        </p>
      ) : (
        <div className="flex flex-col gap-1">
          <span
            data-testid="ota-state-badge"
            className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium w-fit ${otaStateBadgeClass(ota.state)}`}
          >
            {ota.state}
          </span>
          {ota.version_current !== null && (
            <p className="text-sm text-text-secondary">
              Current version:{" "}
              <span className="text-text-primary">{ota.version_current}</span>
            </p>
          )}
          {ota.version_previous !== null &&
            ota.version_previous !== ota.version_current && (
              <p className="text-sm text-text-secondary">
                Previous:{" "}
                <span className="text-text-primary">{ota.version_previous}</span>
              </p>
            )}
        </div>
      )}
    </div>
  );
}

export default function SettingsScreen(): React.ReactElement {
  const { state: authState, logout } = useAuth();
  const navigate = useNavigate();
  const { state: telemetry } = useTelemetryContext();

  const [mode, setMode] = useState<ScheduleMode>("manual");
  const [timeWindows, setTimeWindows] = useState<TimeWindow[]>([]);
  const [dayStart, setDayStart] = useState<string>("06:00");
  const [nightStart, setNightStart] = useState<string>("18:00");
  const [saveMessage, setSaveMessage] = useState<string | null>(null);

  // Redirect non-admin users
  useEffect(() => {
    if (authState.level !== "admin") {
      navigate("/");
    }
  }, [authState.level, navigate]);

  const addWindow = useCallback((): void => {
    setTimeWindows((prev) => [...prev, { ...DEFAULT_WINDOW }]);
  }, []);

  const removeWindow = useCallback((index: number): void => {
    setTimeWindows((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const updateWindow = useCallback(
    (index: number, field: keyof TimeWindow, value: string | number): void => {
      setTimeWindows((prev) =>
        prev.map((w, i) =>
          i === index ? { ...w, [field]: value } : w,
        ),
      );
    },
    [],
  );

  const handleSave = (): void => {
    const scheduleBody = {
      _schema_version: "1.0",
      mode,
      time_windows: timeWindows,
      day_night: { day_start: dayStart, night_start: nightStart },
    };

    void (async (): Promise<void> => {
      try {
        const response = await fetch("/api/config/schedule", {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${authState.token}`,
          },
          body: JSON.stringify(scheduleBody),
        });

        if (response.ok) {
          setSaveMessage("Schedule saved successfully");
        } else if (response.status === 422) {
          const body = await response.json() as { detail?: string };
          setSaveMessage(`Validation error: ${body.detail ?? "unknown"}`);
        } else {
          setSaveMessage("Failed to save schedule");
        }
      } catch {
        setSaveMessage("Failed to save schedule");
      }

      setTimeout(() => setSaveMessage(null), 3000);
    })();
  };

  const handleLogout = (): void => {
    void logout();
  };

  if (authState.level !== "admin") {
    return <div data-testid="screen-settings" />;
  }

  return (
    <div data-testid="screen-settings" className="flex flex-col gap-6 p-4">
      <h2 className="text-2xl font-bold text-text-primary">Settings</h2>

      {/* OTA Status Section */}
      <OtaStatusSection ota={telemetry.ota} />

      {/* Schedule Mode Selector */}
      <div>
        <h3 className="mb-2 text-lg font-semibold text-text-secondary">
          Schedule Mode
        </h3>
        <div className="flex gap-2">
          {(["manual", "time_of_day", "curve"] as ScheduleMode[]).map((m) => (
            <button
              key={m}
              type="button"
              data-testid={`mode-${m}`}
              className={`min-h-[44px] min-w-[44px] rounded-lg px-4 py-2 font-semibold ${
                mode === m
                  ? "bg-accent text-text-primary"
                  : "bg-bg-card text-text-secondary active:bg-accent"
              }`}
              onClick={() => setMode(m)}
            >
              {m === "manual"
                ? "Manual"
                : m === "time_of_day"
                  ? "Time of Day"
                  : "Curve"}
            </button>
          ))}
        </div>
      </div>

      {/* Time Windows Editor -- visible when mode === "time_of_day" */}
      {mode === "time_of_day" && (
        <div data-testid="time-windows-section">
          <h3 className="mb-2 text-lg font-semibold text-text-secondary">
            Time Windows
          </h3>

          {timeWindows.map((win, idx) => (
            <div
              key={idx}
              data-testid={`window-row-${idx}`}
              className="mb-2 flex flex-wrap items-center gap-2 rounded-xl bg-bg-card p-3"
            >
              <div className="flex flex-col">
                <label className="text-xs text-text-secondary">Start</label>
                <input
                  type="text"
                  value={win.start}
                  onChange={(e) =>
                    updateWindow(idx, "start", e.target.value)
                  }
                  className="min-h-[44px] w-[80px] rounded-lg bg-bg-secondary px-2 text-center text-text-primary"
                  placeholder="HH:MM"
                />
              </div>
              <div className="flex flex-col">
                <label className="text-xs text-text-secondary">End</label>
                <input
                  type="text"
                  value={win.end}
                  onChange={(e) =>
                    updateWindow(idx, "end", e.target.value)
                  }
                  className="min-h-[44px] w-[80px] rounded-lg bg-bg-secondary px-2 text-center text-text-primary"
                  placeholder="HH:MM"
                />
              </div>
              <div className="flex flex-col">
                <label className="text-xs text-text-secondary">Action</label>
                <div className="flex gap-1">
                  {(["charge", "discharge", "idle"] as const).map((act) => (
                    <button
                      key={act}
                      type="button"
                      className={`min-h-[44px] rounded-lg px-3 py-1 text-sm font-semibold ${
                        win.action === act
                          ? "bg-accent text-text-primary"
                          : "bg-bg-secondary text-text-secondary active:bg-accent"
                      }`}
                      onClick={() => updateWindow(idx, "action", act)}
                    >
                      {act}
                    </button>
                  ))}
                </div>
              </div>
              <div className="flex flex-col">
                <label className="text-xs text-text-secondary">
                  Power (kW)
                </label>
                <input
                  type="number"
                  value={win.power_kw}
                  onChange={(e) =>
                    updateWindow(
                      idx,
                      "power_kw",
                      parseFloat(e.target.value) || 0,
                    )
                  }
                  className="min-h-[44px] w-[80px] rounded-lg bg-bg-secondary px-2 text-center text-text-primary"
                />
              </div>
              <button
                type="button"
                data-testid={`remove-window-${idx}`}
                className="min-h-[44px] min-w-[44px] rounded-lg bg-danger px-3 py-1 font-bold text-text-primary active:bg-danger/80"
                onClick={() => removeWindow(idx)}
              >
                X
              </button>
            </div>
          ))}

          <button
            type="button"
            data-testid="add-window-btn"
            className="min-h-[44px] min-w-[44px] rounded-lg bg-bg-card px-4 py-2 font-semibold text-text-secondary active:bg-accent"
            onClick={addWindow}
          >
            + Add Window
          </button>

          {/* Day/Night settings */}
          <div className="mt-4">
            <h3 className="mb-2 text-lg font-semibold text-text-secondary">
              Day/Night
            </h3>
            <div className="flex gap-4">
              <div className="flex flex-col">
                <label className="text-xs text-text-secondary">Day Start</label>
                <input
                  type="text"
                  value={dayStart}
                  onChange={(e) => setDayStart(e.target.value)}
                  className="min-h-[44px] w-[80px] rounded-lg bg-bg-card px-2 text-center text-text-primary"
                  placeholder="HH:MM"
                />
              </div>
              <div className="flex flex-col">
                <label className="text-xs text-text-secondary">
                  Night Start
                </label>
                <input
                  type="text"
                  value={nightStart}
                  onChange={(e) => setNightStart(e.target.value)}
                  className="min-h-[44px] w-[80px] rounded-lg bg-bg-card px-2 text-center text-text-primary"
                  placeholder="HH:MM"
                />
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Power Curve -- visible when mode === "curve" */}
      {mode === "curve" && (
        <div className="rounded-xl bg-bg-card p-4">
          <h3 className="mb-2 text-lg font-semibold text-text-secondary">
            Power Curve
          </h3>
          <p className="text-text-secondary">
            96-point power curve editing is deferred to a future release.
          </p>
        </div>
      )}

      {/* System Info */}
      <div className="rounded-xl bg-bg-card p-4">
        <h3 className="mb-2 text-lg font-semibold text-text-secondary">
          System Info
        </h3>
        <p className="text-sm text-text-secondary">
          Build version: <span className="text-text-primary">0.1.0</span>
        </p>
      </div>

      {/* Save button */}
      <button
        type="button"
        data-testid="save-btn"
        className="min-h-[44px] min-w-[44px] rounded-lg bg-accent px-6 py-3 font-semibold text-text-primary active:bg-accent/80"
        onClick={handleSave}
      >
        Save Schedule
      </button>

      {saveMessage && (
        <div className="rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
          {saveMessage}
        </div>
      )}

      {/* Logout button */}
      <button
        type="button"
        data-testid="logout-btn"
        className="min-h-[44px] min-w-[44px] rounded-lg bg-danger px-6 py-3 font-semibold text-text-primary active:bg-danger/80"
        onClick={handleLogout}
      >
        Logout
      </button>
    </div>
  );
}
