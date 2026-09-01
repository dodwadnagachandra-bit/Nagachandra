import { useEffect, useRef, useState } from "react";
import { SocGauge } from "../components/charts/SocGauge";
import { TrendLine } from "../components/charts/TrendLine";
import { useTelemetryContext } from "../context/TelemetryContext";
import { RollingBuffer } from "../lib/rolling-buffer";
import type { BmsRackTelemetry } from "../types/telemetry";

const numFmt = new Intl.NumberFormat("en", { maximumFractionDigits: 1 });
const cellFmt = new Intl.NumberFormat("en", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export default function BmsScreen(): React.ReactElement {
  const { state } = useTelemetryContext();
  const rackKeys: string[] = Object.keys(state.bmsRacks).sort();

  const [selectedRack, setSelectedRack] = useState<string | null>(null);

  // Auto-select first rack when data arrives
  useEffect(() => {
    if (selectedRack === null && rackKeys.length > 0) {
      setSelectedRack(rackKeys[0]);
    }
  }, [rackKeys, selectedRack]);

  // SOC trend buffer for selected rack
  const socBufferRef = useRef(new RollingBuffer<{ ts: number; value: number }>(300));
  const [socTrend, setSocTrend] = useState<{ ts: number; value: number }[]>([]);
  const prevRackRef = useRef<string | null>(null);

  const rack: BmsRackTelemetry | undefined =
    selectedRack !== null ? state.bmsRacks[selectedRack] : undefined;

  // Clear buffer when rack changes, push new data on updates
  useEffect(() => {
    if (selectedRack !== prevRackRef.current) {
      socBufferRef.current.clear();
      prevRackRef.current = selectedRack;
    }
    if (rack) {
      socBufferRef.current.push({ ts: Date.now(), value: rack.pack_soc });
      setSocTrend(socBufferRef.current.getAll());
    }
  }, [rack, selectedRack]);

  if (rackKeys.length === 0) {
    return (
      <div data-testid="screen-bms" className="flex h-full items-center justify-center">
        <p className="text-lg text-text-secondary">No racks available</p>
      </div>
    );
  }

  return (
    <div data-testid="screen-bms" className="p-4">
      <h2 className="mb-4 text-2xl font-bold">Battery Management</h2>

      {/* Rack selector */}
      <div className="mb-4 flex flex-wrap gap-2">
        {rackKeys.map((key) => (
          <button
            key={key}
            className={`min-h-[44px] min-w-[44px] rounded-lg px-4 py-2 font-medium transition-colors ${
              selectedRack === key
                ? "bg-accent text-white"
                : "bg-bg-card text-text-primary active:bg-accent/50"
            }`}
            onClick={() => setSelectedRack(key)}
          >
            Rack {key}
          </button>
        ))}
      </div>

      {/* Selected rack detail */}
      {rack && (
        <div className="grid grid-cols-2 gap-4 xl:grid-cols-3">
          {/* SOC gauge */}
          <div className="col-span-2 flex justify-center rounded-lg bg-bg-card p-4 xl:col-span-1">
            <SocGauge soc={rack.pack_soc} size={180} />
          </div>

          {/* Pack data */}
          <div className="rounded-lg bg-bg-card p-4">
            <h3 className="mb-3 text-sm font-medium text-text-secondary">Pack Data</h3>
            <div className="space-y-2">
              <DataRow label="Voltage" value={`${numFmt.format(rack.pack_v)} V`} />
              <DataRow label="Current" value={`${numFmt.format(rack.pack_i)} A`} />
              <DataRow label="SOC" value={`${numFmt.format(rack.pack_soc)} %`} />
              <DataRow label="SOH" value={`${numFmt.format(rack.pack_soh)} %`} />
            </div>
          </div>

          {/* Cell voltage range */}
          <div className="rounded-lg bg-bg-card p-4">
            <h3 className="mb-3 text-sm font-medium text-text-secondary">Cell Voltage</h3>
            <div className="space-y-2">
              <DataRow label="Min" value={`${cellFmt.format(rack.min_cell_v)} V`} />
              <DataRow label="Avg" value={`${cellFmt.format(rack.avg_cell_v)} V`} />
              <DataRow label="Max" value={`${cellFmt.format(rack.max_cell_v)} V`} />
            </div>
          </div>

          {/* Cell temperature range */}
          <div className="rounded-lg bg-bg-card p-4">
            <h3 className="mb-3 text-sm font-medium text-text-secondary">Cell Temperature</h3>
            <div className="space-y-2">
              <DataRow label="Min" value={`${numFmt.format(rack.min_cell_t)} C`} />
              <DataRow label="Avg" value={`${numFmt.format(rack.avg_cell_t)} C`} />
              <DataRow label="Max" value={`${numFmt.format(rack.max_cell_t)} C`} />
            </div>
          </div>

          {/* Status */}
          <div className="rounded-lg bg-bg-card p-4">
            <h3 className="mb-3 text-sm font-medium text-text-secondary">Status</h3>
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-text-secondary">Online</span>
                <span
                  className={`rounded px-2 py-0.5 text-sm font-bold ${
                    rack.online ? "bg-success/20 text-success" : "bg-danger/20 text-danger"
                  }`}
                >
                  {rack.online ? "Online" : "Offline"}
                </span>
              </div>
              <DataRow
                label="Fault"
                value={rack.fault_code ? `0x${rack.fault_code.toString(16).padStart(4, "0").toUpperCase()}` : "None"}
              />
            </div>
          </div>

          {/* SOC Trend */}
          {socTrend.length > 1 && (
            <div className="col-span-2 rounded-lg bg-bg-card p-4 xl:col-span-3">
              <h3 className="mb-2 text-sm font-medium text-text-secondary">SOC Trend (5 min)</h3>
              <TrendLine data={socTrend} label="SOC %" yMin={0} yMax={100} width={400} height={160} />
            </div>
          )}
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
