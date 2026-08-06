import { useCallback, useEffect, useRef, useState } from "react";
import { Outlet } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useTelemetryContext } from "../context/TelemetryContext";
import { useApi } from "../hooks/useApi";
import type { ActiveAlarm, ZmqResponse } from "../types/api";
import Sidebar from "./Sidebar";

export default function Layout(): React.ReactElement {
  const { state: authState } = useAuth();
  const { connectionStatus } = useTelemetryContext();
  const { apiFetch } = useApi();
  const [unackedCount, setUnackedCount] = useState<number>(0);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Settings tab visible only for admin users
  const showSettings: boolean = authState.level === "admin";

  /** Fetch active alarm count for sidebar badge */
  const fetchAlarmCount = useCallback(async (): Promise<void> => {
    try {
      const resp = await apiFetch<ZmqResponse>("/api/alarm/active");
      const result = resp.result as { alarms?: ActiveAlarm[] } | null;
      const alarms: ActiveAlarm[] = result?.alarms ?? [];
      const count: number = alarms.filter(
        (a) => a.state === "ACTIVE_UNACKED",
      ).length;
      setUnackedCount(count);
    } catch {
      // Silently fail -- don't break layout if alarm fetch fails
    }
  }, [apiFetch]);

  // Poll alarm count every 10 seconds
  useEffect(() => {
    fetchAlarmCount();
    intervalRef.current = setInterval(fetchAlarmCount, 10000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [fetchAlarmCount]);

  return (
    <div className="flex h-screen bg-bg-primary">
      <Sidebar
        showSettings={showSettings}
        unackedCount={unackedCount}
        connectionStatus={connectionStatus}
      />
      <main
        data-testid="content-area"
        className="flex-1 overflow-y-auto bg-bg-primary p-4"
      >
        <Outlet context={{ unackedCount }} />
      </main>
    </div>
  );
}
