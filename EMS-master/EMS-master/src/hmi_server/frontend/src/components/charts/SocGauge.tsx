import { Doughnut } from "react-chartjs-2";

interface SocGaugeProps {
  soc: number; // 0-100
  size?: number; // pixel dimension (default 200)
}

/**
 * Doughnut chart showing battery SOC percentage.
 * Green > 20%, yellow > 10%, red <= 10%.
 * Center text overlay shows the percentage value.
 */
export function SocGauge({ soc, size = 200 }: SocGaugeProps): React.ReactElement {
  const color: string =
    soc > 20 ? "#22c55e" : soc > 10 ? "#eab308" : "#ef4444";

  const data = {
    datasets: [
      {
        data: [soc, 100 - soc],
        backgroundColor: [color, "#334155"],
        borderWidth: 0,
        cutout: "75%",
      },
    ],
  };

  const options = {
    responsive: false as const,
    animation: false as const,
    plugins: {
      tooltip: { enabled: false },
      legend: { display: false },
    },
  };

  return (
    <div className="relative inline-block" data-testid="soc-gauge">
      <Doughnut data={data} options={options} width={size} height={size} />
      <div className="absolute inset-0 flex items-center justify-center">
        <span className="text-3xl font-bold text-text-primary">
          {soc.toFixed(1)}%
        </span>
      </div>
    </div>
  );
}
