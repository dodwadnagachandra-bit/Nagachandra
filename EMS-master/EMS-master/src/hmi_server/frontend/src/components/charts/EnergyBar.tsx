import { Bar } from "react-chartjs-2";

interface EnergyBarProps {
  charge_kwh: number;
  discharge_kwh: number;
  import_kwh: number;
  export_kwh: number;
  width?: number;
  height?: number;
}

/**
 * Grouped bar chart for energy totals: Charge, Discharge, Import, Export.
 * Uses fixed canvas dimensions (no responsive). Animation disabled for ARM.
 */
export function EnergyBar({
  charge_kwh,
  discharge_kwh,
  import_kwh,
  export_kwh,
  width = 400,
  height = 250,
}: EnergyBarProps): React.ReactElement {
  const data = {
    labels: ["Charge", "Discharge", "Import", "Export"],
    datasets: [
      {
        data: [charge_kwh, discharge_kwh, import_kwh, export_kwh],
        backgroundColor: ["#22c55e", "#f97316", "#3b82f6", "#a855f7"],
        borderWidth: 0,
      },
    ],
  };

  const options = {
    indexAxis: "x" as const,
    responsive: false as const,
    animation: false as const,
    scales: {
      x: {
        grid: { color: "#475569" },
        ticks: { color: "#94a3b8" },
      },
      y: {
        grid: { color: "#475569" },
        ticks: { color: "#94a3b8" },
        beginAtZero: true,
      },
    },
    plugins: {
      legend: {
        display: true,
        position: "bottom" as const,
        labels: { color: "#94a3b8" },
      },
      tooltip: { enabled: true },
    },
  };

  return (
    <div data-testid="energy-bar">
      <Bar data={data} options={options} width={width} height={height} />
    </div>
  );
}
