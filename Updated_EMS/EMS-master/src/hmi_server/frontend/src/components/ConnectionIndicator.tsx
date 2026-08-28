import type { ConnectionStatus } from "../types/telemetry";

const STATUS_CLASSES: Record<ConnectionStatus, string> = {
  connected: "bg-success",
  disconnected: "bg-danger",
  reconnecting: "bg-warning",
};

interface ConnectionIndicatorProps {
  status: ConnectionStatus;
}

export default function ConnectionIndicator({
  status,
}: ConnectionIndicatorProps): React.ReactElement {
  return (
    <div className="flex min-h-[44px] min-w-[44px] items-center justify-center">
      <div
        data-testid="connection-dot"
        className={`h-3 w-3 rounded-full ${STATUS_CLASSES[status]}`}
      />
      <span className="sr-only">{status}</span>
    </div>
  );
}
