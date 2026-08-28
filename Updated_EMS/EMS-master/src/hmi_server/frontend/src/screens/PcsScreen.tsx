import { useEffect, useRef, useState } from "react";
import { TrendLine } from "../components/charts/TrendLine";
import { useTelemetry } from "../hooks/useTelemetry";
import { PCS_STATE_NAMES } from "../types/enums";
import { RollingBuffer } from "../lib/rolling-buffer";

const numFmt = new Intl.NumberFormat("en", { maximumFractionDigits: 1 });
const freqFmt = new Intl.NumberFormat("en", { maximumFractionDigits: 2 });

/** Color class for PCS state. */
function pcsStateColor(state: number): string {
  if (state === 3) return "text-danger";
  if (state === 2) return "text-success";
  if (state === 1) return "text-warning";
  return "text-text-secondary";
}

export default function PcsScreen(): React.ReactElement {
  const pcs = useTelemetry("pcs");

  // Power trend buffer -- 300 points (5 min at 1Hz)
  const powerBufferRef = useRef(new RollingBuffer<{ ts: number; value: number }>(300));
  const [powerTrend, setPowerTrend] = useState<{ ts: number; value: number }[]>([]);

  useEffect(() => {
    if (pcs) {
      powerBufferRef.current.push({ ts: Date.now(), value: pcs.active_power });
      setPowerTrend(powerBufferRef.current.getAll());
    }
  }, [pcs]);

  if (!pcs) {
    return (
      <div data-testid="screen-pcs" className="flex h-full items-center justify-center">
        <p className="text-lg text-text-secondary">Waiting for PCS data...</p>
      </div>
    );
  }

  const stateName: string = PCS_STATE_NAMES[pcs.state] ?? "UNKNOWN";
  const faultDisplay: string = pcs.fault_code
    ? `0x${pcs.fault_code.toString(16).padStart(4, "0").toUpperCase()}`
    : "None";

  return (
    <div data-testid="screen-pcs" className="p-4">
      <h2 className="mb-4 text-2xl font-bold">Inverter (PCS)</h2>

      {/* AC / DC grid -- 2 cols on 15", stacked on 10" */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {/* AC Section */}
        <div className="rounded-lg bg-bg-card p-4">
          <h3 className="mb-3 text-sm font-medium text-text-secondary">AC Side</h3>
          <div className="space-y-2">
            <DataRow label="Voltage" value={`${numFmt.format(pcs.ac_voltage)} V`} />
            <DataRow label="Current" value={`${numFmt.format(pcs.ac_current)} A`} />
            <DataRow label="Active Power" value={`${numFmt.format(pcs.active_power)} kW`} />
            <DataRow label="Reactive Power" value={`${numFmt.format(pcs.reactive_power)} kVAR`} />
            <DataRow label="Frequency" value={`${freqFmt.format(pcs.frequency)} Hz`} />
          </div>
        </div>

        {/* DC Section */}
        <div className="rounded-lg bg-bg-card p-4">
          <h3 className="mb-3 text-sm font-medium text-text-secondary">DC Side</h3>
          <div className="space-y-2">
            <DataRow label="Voltage" value={`${numFmt.format(pcs.dc_voltage)} V`} />
            <DataRow label="Current" value={`${numFmt.format(pcs.dc_current)} A`} />
          </div>
        </div>
      </div>

      {/* Status section */}
      <div className="mt-4 grid grid-cols-2 gap-4 xl:grid-cols-3">
        <div className="rounded-lg bg-bg-card p-4">
          <h3 className="mb-2 text-sm font-medium text-text-secondary">PCS State</h3>
          <p className={`text-xl font-bold ${pcsStateColor(pcs.state)}`}>{stateName}</p>
        </div>

        <div className="rounded-lg bg-bg-card p-4">
          <h3 className="mb-2 text-sm font-medium text-text-secondary">Temperature</h3>
          <p className={`text-xl font-bold ${pcs.temperature > 50 ? "text-danger" : "text-text-primary"}`}>
            {numFmt.format(pcs.temperature)} <span className="text-sm text-text-secondary">C</span>
          </p>
        </div>

        <div className="rounded-lg bg-bg-card p-4">
          <h3 className="mb-2 text-sm font-medium text-text-secondary">Fault Code</h3>
          <p className={`text-xl font-bold ${pcs.fault_code ? "text-danger" : "text-text-primary"}`}>
            {faultDisplay}
          </p>
        </div>
      </div>

      {/* Power Trend */}
      {powerTrend.length > 1 && (
        <div className="mt-4 rounded-lg bg-bg-card p-4">
          <h3 className="mb-2 text-sm font-medium text-text-secondary">Power Trend (5 min)</h3>
          <TrendLine data={powerTrend} label="Active Power (kW)" width={400} height={160} />
        </div>
      )}
    </div>
  );
}

/** Simple label + value row. */
function DataRow({ label, value }: { label: string; value: string }): React.ReactElement {
  return (
    <div className="flex items-center justify-between">
      <span className="text-text-secondary">{label}</span>
      <span className="font-semibold">{value}</span>
    </div>
  );
}
