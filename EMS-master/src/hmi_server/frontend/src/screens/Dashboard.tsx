import { useEffect, useRef, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { SocGauge } from "../components/charts/SocGauge";
import { PowerBar } from "../components/charts/PowerBar";
import { TrendLine } from "../components/charts/TrendLine";
import { useTelemetry } from "../hooks/useTelemetry";
import { CONTROL_STATE_NAMES, PCS_STATE_NAMES } from "../types/enums";
import { RollingBuffer } from "../lib/rolling-buffer";

const numFmt = new Intl.NumberFormat("en", { maximumFractionDigits: 1 });

/** Format seconds as HH:MM:SS. */
function formatUptime(totalSeconds: number): string {
  const h: number = Math.floor(totalSeconds / 3600);
  const m: number = Math.floor((totalSeconds % 3600) / 60);
  const s: number = Math.floor(totalSeconds % 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/** Color class for control state badge. */
function controlStateColor(state: number): string {
  if (state === 5 || state === 6) return "text-danger";
  if (state === 7) return "text-warning";
  if (state === 3 || state === 4) return "text-success";
  return "text-text-primary";
}

/** Color class for PCS state badge. */
function pcsStateBadgeColor(state: number): string {
  if (state === 3) return "text-danger";
  if (state === 2) return "text-success";
  if (state === 1) return "text-warning";
  return "text-text-secondary";
}

export default function Dashboard(): React.ReactElement {
  const system = useTelemetry("system");
  const { unackedCount } = useOutletContext<{ unackedCount: number }>();

  // SOC trend buffer -- 300 points (5 min at 1Hz)
  const socBufferRef = useRef(new RollingBuffer<{ ts: number; value: number }>(300));
  const [socTrend, setSocTrend] = useState<{ ts: number; value: number }[]>([]);

  useEffect(() => {
    if (system) {
      socBufferRef.current.push({ ts: Date.now(), value: system.total_soc });
      setSocTrend(socBufferRef.current.getAll());
    }
  }, [system]);

  if (!system) {
    return (
      <div data-testid="screen-dashboard" className="flex h-full items-center justify-center">
        <p className="text-lg text-text-secondary">Waiting for data...</p>
      </div>
    );
  }

  const controlName: string = CONTROL_STATE_NAMES[system.control_state] ?? "UNKNOWN";
  const pcsName: string = PCS_STATE_NAMES[system.pcs_command] ?? "UNKNOWN";

  return (
    <div data-testid="screen-dashboard" className="p-4">
      <h2 className="mb-4 text-2xl font-bold">Dashboard</h2>

      {/* Hero SOC gauge -- centered, prominent */}
      <div className="mb-6 flex flex-col items-center">
        <SocGauge soc={system.total_soc} size={240} />
        <p className="mt-2 text-sm text-text-secondary">Battery State of Charge</p>
      </div>

      {/* Status cards grid -- 2 cols on 10", 3 cols on 15" */}
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-3">
        {/* Power card */}
        <div className="rounded-lg bg-bg-card p-4">
          <h3 className="mb-2 text-sm font-medium text-text-secondary">Power</h3>
          <PowerBar powerKw={system.total_power_kw} />
        </div>

        {/* Control State card */}
        <div className="rounded-lg bg-bg-card p-4">
          <h3 className="mb-2 text-sm font-medium text-text-secondary">Control State</h3>
          <p className={`text-xl font-bold ${controlStateColor(system.control_state)}`}>
            {controlName}
          </p>
        </div>

        {/* PCS State card */}
        <div className="rounded-lg bg-bg-card p-4">
          <h3 className="mb-2 text-sm font-medium text-text-secondary">PCS State</h3>
          <p className={`text-xl font-bold ${pcsStateBadgeColor(system.pcs_command)}`}>
            {pcsName}
          </p>
        </div>

        {/* Setpoint card */}
        <div className="rounded-lg bg-bg-card p-4">
          <h3 className="mb-2 text-sm font-medium text-text-secondary">Setpoint</h3>
          <p className="text-xl font-bold">
            {numFmt.format(system.active_setpoint_kw)} <span className="text-sm text-text-secondary">kW</span>
          </p>
        </div>

        {/* Uptime card */}
        <div className="rounded-lg bg-bg-card p-4">
          <h3 className="mb-2 text-sm font-medium text-text-secondary">Uptime</h3>
          <p className="text-xl font-bold font-mono">{formatUptime(system.ems_uptime_s)}</p>
        </div>

        {/* Alarm Count card */}
        <div className="rounded-lg bg-bg-card p-4">
          <h3 className="mb-2 text-sm font-medium text-text-secondary">Active Alarms</h3>
          <p className={`text-xl font-bold ${unackedCount > 0 ? "text-danger" : "text-success"}`}>
            {unackedCount}
          </p>
        </div>
      </div>

      {/* SOC Trend */}
      {socTrend.length > 1 && (
        <div className="mt-6 rounded-lg bg-bg-card p-4">
          <h3 className="mb-2 text-sm font-medium text-text-secondary">SOC Trend (5 min)</h3>
          <TrendLine data={socTrend} label="SOC %" yMin={0} yMax={100} width={400} height={160} />
        </div>
      )}
    </div>
  );
}
