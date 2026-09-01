import { Line } from "react-chartjs-2";

interface TrendPoint {
  ts: number;
  value: number;
}

interface TrendLineProps {
  data: TrendPoint[];
  label: string;
  color?: string;
  yMin?: number;
  yMax?: number;
  width?: number;
  height?: number;
}

/** Format a millisecond timestamp as HH:MM:SS. */
const timeFmt = new Intl.DateTimeFormat("en", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

/**
 * Rolling line chart for real-time trend data.
 * No point markers, no animation, fixed canvas dimensions.
 */
export function TrendLine({
  data: points,
  label,
  color = "#3b82f6",
  yMin,
  yMax,
  width = 400,
  height = 200,
}: TrendLineProps): React.ReactElement {
  const labels: string[] = points.map((p) => timeFmt.format(p.ts));
  const values: number[] = points.map((p) => p.value);

  const data = {
    labels,
    datasets: [
      {
        label,
        data: values,
        borderColor: color,
        backgroundColor: `${color}33`,
        fill: true,
        borderWidth: 1.5,
        tension: 0.2,
      },
    ],
  };

  const options = {
    responsive: false as const,
    animation: false as const,
    elements: {
      point: { radius: 0 },
    },
    scales: {
      x: {
        grid: { color: "#475569" },
        ticks: {
          color: "#94a3b8",
          maxTicksLimit: 5,
          maxRotation: 0,
        },
      },
      y: {
        min: yMin,
        max: yMax,
        grid: { color: "#475569" },
        ticks: { color: "#94a3b8" },
      },
    },
    plugins: {
      tooltip: { enabled: false },
      legend: { display: false },
    },
  };

  return (
    <div data-testid="trend-line">
      <Line data={data} options={options} width={width} height={height} />
    </div>
  );
}
