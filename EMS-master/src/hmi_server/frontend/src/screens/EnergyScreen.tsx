import { useCallback, useEffect, useState } from "react";
import { useApi } from "../hooks/useApi";
import { EnergyBar } from "../components/charts/EnergyBar";
import type { ZmqResponse } from "../types/api";

type TimeRange = "today" | "7d" | "30d";

interface EnergyTotals {
  charge_kwh: number;
  discharge_kwh: number;
  import_kwh: number;
  export_kwh: number;
}

const RANGE_MS: Record<TimeRange, number> = {
  today: 24 * 60 * 60 * 1000,
  "7d": 7 * 24 * 60 * 60 * 1000,
  "30d": 30 * 24 * 60 * 60 * 1000,
};

const RANGE_LABELS: Record<TimeRange, string> = {
  today: "Today",
  "7d": "7 Days",
  "30d": "30 Days",
};

const fmt = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 1,
  minimumFractionDigits: 0,
});

function parseEnergyResult(
  result: Record<string, unknown>,
): EnergyTotals | null {
  const columns = result.columns as string[] | undefined;
  const rows = result.rows as number[][] | undefined;

  if (!columns || !rows || rows.length === 0) {
    return null;
  }

  const row: number[] = rows[0];
  const colMap: Record<string, number> = {};
  columns.forEach((col: string, idx: number) => {
    colMap[col] = row[idx] ?? 0;
  });

  return {
    discharge_kwh: (colMap["discharge_wh"] ?? 0) / 1000,
    charge_kwh: (colMap["charge_wh"] ?? 0) / 1000,
    import_kwh: (colMap["grid_import_wh"] ?? 0) / 1000,
    export_kwh: (colMap["grid_export_wh"] ?? 0) / 1000,
  };
}

export default function EnergyScreen(): React.ReactElement {
  const { apiFetch } = useApi();
  const [range, setRange] = useState<TimeRange>("today");
  const [totals, setTotals] = useState<EnergyTotals | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [empty, setEmpty] = useState<boolean>(false);

  const fetchEnergy = useCallback(
    async (selectedRange: TimeRange): Promise<void> => {
      setLoading(true);
      setError(null);
      setEmpty(false);
      try {
        const endTs: number = Date.now();
        const startTs: number = endTs - RANGE_MS[selectedRange];
        const resp = await apiFetch<ZmqResponse>("/api/query/energy_totals", {
          method: "POST",
          body: JSON.stringify({ start_ts: startTs, end_ts: endTs }),
        });
        if (resp.status === "ok" && resp.result) {
          const parsed: EnergyTotals | null = parseEnergyResult(resp.result);
          if (parsed) {
            setTotals(parsed);
          } else {
            setEmpty(true);
            setTotals(null);
          }
        } else {
          setError(resp.error_msg ?? "Query returned error");
        }
      } catch {
        setError("Failed to load energy data");
      } finally {
        setLoading(false);
      }
    },
    [apiFetch],
  );

  useEffect(() => {
    void fetchEnergy(range);
  }, [range, fetchEnergy]);

  const handleRangeChange = (newRange: TimeRange): void => {
    setRange(newRange);
  };

  if (loading) {
    return (
      <div
        data-testid="screen-energy"
        className="flex h-full items-center justify-center text-text-secondary"
      >
        Fetching energy data...
      </div>
    );
  }

  if (error) {
    return (
      <div
        data-testid="screen-energy"
        className="flex h-full flex-col items-center justify-center gap-4"
      >
        <p className="text-danger">{error}</p>
        <button
          type="button"
          className="min-h-[44px] min-w-[44px] rounded-lg bg-accent px-6 py-2 font-semibold text-text-primary active:bg-accent/80"
          onClick={() => void fetchEnergy(range)}
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div data-testid="screen-energy" className="flex flex-col gap-6 p-4">
      <h2 className="text-2xl font-bold text-text-primary">Energy</h2>

      {/* Time range selector */}
      <div className="flex gap-2">
        {(["today", "7d", "30d"] as TimeRange[]).map((r) => (
          <button
            key={r}
            type="button"
            data-testid={`range-${r}`}
            className={`min-h-[44px] min-w-[44px] rounded-lg px-4 py-2 font-semibold ${
              range === r
                ? "bg-accent text-text-primary"
                : "bg-bg-card text-text-secondary active:bg-accent"
            }`}
            onClick={() => handleRangeChange(r)}
          >
            {RANGE_LABELS[r]}
          </button>
        ))}
      </div>

      {empty ? (
        <div className="flex h-64 items-center justify-center text-text-secondary">
          No energy data for this period
        </div>
      ) : (
        totals && (
          <>
            {/* Energy totals cards -- 2x2 grid */}
            <div className="grid grid-cols-2 gap-4">
              <div className="rounded-xl bg-bg-card p-4">
                <p className="text-sm text-text-secondary">Charge</p>
                <p className="text-2xl font-bold text-success">
                  {fmt.format(totals.charge_kwh)} kWh
                </p>
              </div>
              <div className="rounded-xl bg-bg-card p-4">
                <p className="text-sm text-text-secondary">Discharge</p>
                <p className="text-2xl font-bold text-orange">
                  {fmt.format(totals.discharge_kwh)} kWh
                </p>
              </div>
              <div className="rounded-xl bg-bg-card p-4">
                <p className="text-sm text-text-secondary">Grid Import</p>
                <p className="text-2xl font-bold text-accent">
                  {fmt.format(totals.import_kwh)} kWh
                </p>
              </div>
              <div className="rounded-xl bg-bg-card p-4">
                <p className="text-sm text-text-secondary">Grid Export</p>
                <p className="text-2xl font-bold text-purple-400">
                  {fmt.format(totals.export_kwh)} kWh
                </p>
              </div>
            </div>

            {/* Bar chart */}
            <div className="rounded-xl bg-bg-card p-4">
              <EnergyBar
                charge_kwh={totals.charge_kwh}
                discharge_kwh={totals.discharge_kwh}
                import_kwh={totals.import_kwh}
                export_kwh={totals.export_kwh}
              />
            </div>
          </>
        )
      )}
    </div>
  );
}
