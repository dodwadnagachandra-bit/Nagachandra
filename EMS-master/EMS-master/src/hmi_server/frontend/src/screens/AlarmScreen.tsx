import { useCallback, useEffect, useRef, useState } from "react";
import { useApi } from "../hooks/useApi";
import type { ActiveAlarm, ZmqResponse } from "../types/api";

/** Severity display config: colors and sort priority */
const SEVERITY_CONFIG: Record<
  string,
  { bg: string; border: string; text: string; priority: number }
> = {
  protection: {
    bg: "bg-red-900/30",
    border: "border-red-500",
    text: "text-red-400",
    priority: 0,
  },
  action: {
    bg: "bg-orange-900/30",
    border: "border-orange-500",
    text: "text-orange-400",
    priority: 1,
  },
  warning: {
    bg: "bg-yellow-900/30",
    border: "border-yellow-500",
    text: "text-yellow-400",
    priority: 2,
  },
};

/** Format timestamp as HH:MM:SS */
const timeFmt = new Intl.DateTimeFormat("en", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

type SortField = "severity" | "time";
type SortDir = "asc" | "desc";
type TabName = "active" | "history";

/** Event log row from POST /api/query/event_log */
interface EventLogResponse {
  columns: string[];
  rows: unknown[][];
  count: number;
}

export default function AlarmScreen(): React.ReactElement {
  const { apiFetch } = useApi();
  const [alarms, setAlarms] = useState<ActiveAlarm[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [sortField, setSortField] = useState<SortField>("time");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [activeTab, setActiveTab] = useState<TabName>("active");

  // History state
  const [historyColumns, setHistoryColumns] = useState<string[]>([]);
  const [historyRows, setHistoryRows] = useState<unknown[][]>([]);
  const [historyLoading, setHistoryLoading] = useState<boolean>(false);
  const [severityFilter, setSeverityFilter] = useState<string>("all");

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /** Fetch active alarms from REST API */
  const fetchAlarms = useCallback(async (): Promise<void> => {
    try {
      const resp = await apiFetch<ZmqResponse>("/api/alarm/active");
      const result = resp.result as { alarms?: ActiveAlarm[] } | null;
      setAlarms(result?.alarms ?? []);
    } catch {
      // Silently fail -- alarm fetch should not break UI
    } finally {
      setLoading(false);
    }
  }, [apiFetch]);

  /** Fetch alarm history from event_log query */
  const fetchHistory = useCallback(async (): Promise<void> => {
    setHistoryLoading(true);
    try {
      const now: number = Date.now();
      const dayAgo: number = now - 24 * 60 * 60 * 1000;
      const resp = await apiFetch<ZmqResponse>("/api/query/event_log", {
        method: "POST",
        body: JSON.stringify({ start_ts: dayAgo, end_ts: now }),
      });
      const result = resp.result as EventLogResponse | null;
      setHistoryColumns(result?.columns ?? []);
      setHistoryRows(result?.rows ?? []);
    } catch {
      setHistoryColumns([]);
      setHistoryRows([]);
    } finally {
      setHistoryLoading(false);
    }
  }, [apiFetch]);

  // Auto-refresh active alarms every 5 seconds
  useEffect(() => {
    fetchAlarms();
    intervalRef.current = setInterval(fetchAlarms, 5000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [fetchAlarms]);

  // Fetch history when history tab opens
  useEffect(() => {
    if (activeTab === "history") {
      fetchHistory();
    }
  }, [activeTab, fetchHistory]);

  /** Acknowledge an alarm */
  const handleAck = async (alarmId: string): Promise<void> => {
    try {
      await apiFetch<ZmqResponse>("/api/alarm/acknowledge", {
        method: "POST",
        body: JSON.stringify({ alarm_id: alarmId }),
      });
      await fetchAlarms();
    } catch {
      // ACK failed -- alarm will refresh on next interval
    }
  };

  /** Toggle sort direction or change sort field */
  const handleSort = (field: SortField): void => {
    if (sortField === field) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir("desc");
    }
  };

  /** Sort alarms by current sort field/direction */
  const sortedAlarms = [...alarms].sort((a, b) => {
    let cmp = 0;
    if (sortField === "severity") {
      const pa = SEVERITY_CONFIG[a.severity]?.priority ?? 3;
      const pb = SEVERITY_CONFIG[b.severity]?.priority ?? 3;
      cmp = pa - pb;
    } else {
      cmp = a.activated_at - b.activated_at;
    }
    return sortDir === "asc" ? cmp : -cmp;
  });

  /** State badge display */
  const stateBadge = (state: string): React.ReactElement => {
    if (state === "ACTIVE_ACKED") {
      return (
        <span className="rounded bg-bg-card px-2 py-0.5 text-xs text-text-secondary">
          Acked
        </span>
      );
    }
    if (state === "CLEARED_UNACKED") {
      return (
        <span className="rounded bg-bg-card px-2 py-0.5 text-xs text-text-secondary">
          Cleared
        </span>
      );
    }
    return (
      <span className="rounded bg-danger/20 px-2 py-0.5 text-xs text-danger">
        Active
      </span>
    );
  };

  /** Determine sort arrow display */
  const sortArrow = (field: SortField): string => {
    if (sortField !== field) return "";
    return sortDir === "asc" ? " \u25B2" : " \u25BC";
  };

  return (
    <div data-testid="screen-alarms" className="p-4">
      <h2 className="mb-4 text-2xl font-bold">Alarms</h2>

      {/* Tab selector */}
      <div className="mb-4 flex gap-2">
        <button
          type="button"
          data-testid="tab-active"
          className={`min-h-[44px] rounded-lg px-4 ${
            activeTab === "active"
              ? "bg-accent text-white"
              : "bg-bg-card text-text-secondary active:bg-bg-card/80"
          }`}
          onClick={() => setActiveTab("active")}
        >
          Active
        </button>
        <button
          type="button"
          data-testid="tab-history"
          className={`min-h-[44px] rounded-lg px-4 ${
            activeTab === "history"
              ? "bg-accent text-white"
              : "bg-bg-card text-text-secondary active:bg-bg-card/80"
          }`}
          onClick={() => setActiveTab("history")}
        >
          History
        </button>
      </div>

      {/* Active alarms tab */}
      {activeTab === "active" && (
        <div className="max-h-[480px] overflow-y-auto rounded-lg bg-bg-secondary xl:max-h-[720px]">
          {loading ? (
            <p className="p-4 text-center text-text-secondary">Loading...</p>
          ) : sortedAlarms.length === 0 ? (
            <p className="p-8 text-center text-text-secondary">No active alarms</p>
          ) : (
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-bg-secondary">
                <tr className="border-b border-bg-card text-left text-text-secondary">
                  <th
                    data-testid="sort-severity"
                    className="cursor-pointer px-3 py-2 select-none"
                    onClick={() => handleSort("severity")}
                  >
                    Severity{sortArrow("severity")}
                  </th>
                  <th className="px-3 py-2">Signal</th>
                  <th className="px-3 py-2">State</th>
                  <th
                    data-testid="sort-time"
                    className="cursor-pointer px-3 py-2 select-none"
                    onClick={() => handleSort("time")}
                  >
                    Time{sortArrow("time")}
                  </th>
                  <th className="px-3 py-2">Action</th>
                </tr>
              </thead>
              <tbody>
                {sortedAlarms.map((alarm) => {
                  const cfg = SEVERITY_CONFIG[alarm.severity] ?? SEVERITY_CONFIG.warning;
                  const needsAck =
                    alarm.state === "ACTIVE_UNACKED" ||
                    alarm.state === "CLEARED_UNACKED";

                  return (
                    <tr
                      key={alarm.alarm_id}
                      data-testid={`alarm-row-${alarm.alarm_id}`}
                      className={`border-l-4 ${cfg.bg} ${cfg.border} border-b border-b-bg-card`}
                    >
                      <td className={`px-3 py-2 font-semibold capitalize ${cfg.text}`}>
                        {alarm.severity}
                      </td>
                      <td className="px-3 py-2 text-text-primary">{alarm.signal}</td>
                      <td className="px-3 py-2">{stateBadge(alarm.state)}</td>
                      <td className="px-3 py-2 font-mono text-text-secondary">
                        {alarm.activated_at
                          ? timeFmt.format(new Date(alarm.activated_at))
                          : "--"}
                      </td>
                      <td className="px-3 py-2">
                        {needsAck && (
                          <button
                            type="button"
                            data-testid="ack-btn"
                            className="min-h-[44px] min-w-[44px] rounded-lg bg-accent px-3 text-white active:bg-accent/80"
                            onClick={() => handleAck(alarm.alarm_id)}
                          >
                            ACK
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* History tab */}
      {activeTab === "history" && (
        <div>
          {/* Severity filter buttons */}
          <div className="mb-3 flex gap-2">
            {["all", "warning", "action", "protection"].map((level) => (
              <button
                key={level}
                type="button"
                className={`min-h-[44px] rounded-lg px-3 capitalize ${
                  severityFilter === level
                    ? "bg-accent text-white"
                    : "bg-bg-card text-text-secondary active:bg-bg-card/80"
                }`}
                onClick={() => setSeverityFilter(level)}
              >
                {level}
              </button>
            ))}
          </div>

          <div className="max-h-[480px] overflow-y-auto rounded-lg bg-bg-secondary xl:max-h-[720px]">
            {historyLoading ? (
              <p className="p-4 text-center text-text-secondary">Loading history...</p>
            ) : historyRows.length === 0 ? (
              <p className="p-8 text-center text-text-secondary">No events found</p>
            ) : (
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-bg-secondary">
                  <tr className="border-b border-bg-card text-left text-text-secondary">
                    {historyColumns.map((col) => (
                      <th key={col} className="px-3 py-2">
                        {col}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {historyRows
                    .filter((row) => {
                      if (severityFilter === "all") return true;
                      // Find severity column index
                      const sevIdx = historyColumns.indexOf("severity");
                      if (sevIdx < 0) return true;
                      return row[sevIdx] === severityFilter;
                    })
                    .map((row, idx) => {
                      // Find severity for row coloring
                      const sevIdx = historyColumns.indexOf("severity");
                      const severity = sevIdx >= 0 ? String(row[sevIdx]) : "";
                      const cfg = SEVERITY_CONFIG[severity];

                      return (
                        <tr
                          key={idx}
                          className={`border-b border-b-bg-card ${
                            cfg
                              ? `border-l-4 ${cfg.bg} ${cfg.border}`
                              : ""
                          }`}
                        >
                          {row.map((cell, ci) => (
                            <td key={ci} className="px-3 py-2 text-text-primary">
                              {String(cell ?? "")}
                            </td>
                          ))}
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
