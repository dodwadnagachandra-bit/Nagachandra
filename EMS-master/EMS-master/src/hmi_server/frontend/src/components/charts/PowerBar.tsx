import { Bar } from "react-chartjs-2";

interface PowerBarProps {
  powerKw: number; // positive = charging, negative = discharging
  maxPowerKw?: number; // scale limit (default 100)
}

/**
 * Horizontal bar chart showing power in kW.
 * Green for charging (positive), orange for discharging (negative).
 */
export function PowerBar({
  powerKw,
  maxPowerKw = 100,
}: PowerBarProps): React.ReactElement {
  const isCharging: boolean = powerKw >= 0;

  const data = {
    labels: ["Power"],
    datasets: [
      {
        data: [powerKw],
        backgroundColor: isCharging ? "#22c55e" : "#f97316",
        borderWidth: 0,
      },
    ],
  };

  const options = {
    indexAxis: "y" as const,
    responsive: false as const,
    animation: false as const,
    scales: {
      x: {
        min: -maxPowerKw,
        max: maxPowerKw,
        grid: { color: "#475569" },
        ticks: { color: "#94a3b8" },
      },
      y: {
        display: false,
      },
    },
    plugins: {
      tooltip: { enabled: false },
      legend: { display: false },
    },
  };

  return (
    <div data-testid="power-bar">
      <Bar data={data} options={options} width={300} height={60} />
      <div className="mt-1 text-center text-sm text-text-secondary">
        <span className="font-semibold text-text-primary">
          {powerKw.toFixed(1)} kW
        </span>{" "}
        — {isCharging ? "Charging" : "Discharging"}
      </div>
    </div>
  );
}
