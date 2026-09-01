import type { CloudTelemetry } from "../types/telemetry";

interface CloudStatusIndicatorProps {
  cloud: CloudTelemetry | null;
}

const DOT_CLASSES: Record<string, string> = {
  connected: "bg-green-500",
  disconnected: "bg-red-500",
  unknown: "bg-gray-500",
};

const TITLE_LABELS: Record<string, string> = {
  connected: "Cloud: connected",
  disconnected: "Cloud: disconnected",
  unknown: "Cloud: unknown",
};

export default function CloudStatusIndicator({
  cloud,
}: CloudStatusIndicatorProps): React.ReactElement {
  const key: string = cloud === null ? "unknown" : cloud.state;
  const dotClass: string = DOT_CLASSES[key] ?? "bg-gray-500";
  const title: string = TITLE_LABELS[key] ?? "Cloud: unknown";

  return (
    <div className="flex min-h-[44px] min-w-[44px] items-center justify-center gap-1">
      <div
        data-testid="cloud-status-dot"
        className={`h-3 w-3 rounded-full ${dotClass}`}
        title={title}
      />
      <span className="hidden xl:inline text-xs text-text-secondary">Cloud</span>
    </div>
  );
}
