import { useEffect, useRef, useState } from "react";
import type { ConnectionStatus, TelemetryAction, WsMessage } from "../types/telemetry";

/**
 * Manages a single WebSocket connection with auto-reconnect and exponential backoff.
 *
 * @param url - WebSocket URL (e.g. "/ws/telemetry")
 * @param dispatch - Reducer dispatch for telemetry actions
 * @returns Current connection status
 */
export function useWebSocket(
  url: string,
  dispatch: React.Dispatch<TelemetryAction>,
): ConnectionStatus {
  const [status, setStatus] = useState<ConnectionStatus>("disconnected");
  const retriesRef = useRef<number>(0);
  const wsRef = useRef<WebSocket | null>(null);
  const timeoutRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    function connect(): void {
      setStatus("reconnecting");
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = (): void => {
        setStatus("connected");
        retriesRef.current = 0;
      };

      ws.onmessage = (event: MessageEvent): void => {
        const msg: WsMessage = JSON.parse(event.data as string);
        dispatch({
          type: "UPDATE_TOPIC",
          topic: msg.topic,
          data: msg.data,
          ts: msg.ts,
        });
      };

      ws.onclose = (): void => {
        setStatus("disconnected");
        const delay = Math.min(1000 * Math.pow(2, retriesRef.current), 30000);
        retriesRef.current++;
        timeoutRef.current = window.setTimeout(connect, delay);
      };

      // Let onclose handle reconnect -- WebSocket fires close after error
      ws.onerror = (): void => {};
    }

    connect();

    return (): void => {
      wsRef.current?.close();
      if (timeoutRef.current !== undefined) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, [url, dispatch]);

  return status;
}
