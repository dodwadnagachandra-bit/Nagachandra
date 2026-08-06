import {
  BarChart3,
  Battery,
  Bell,
  LayoutGrid,
  Settings,
  SlidersHorizontal,
  Zap,
} from "lucide-react";
import { NavLink } from "react-router-dom";
import { useTelemetryContext } from "../context/TelemetryContext";
import type { ConnectionStatus } from "../types/telemetry";
import CloudStatusIndicator from "./CloudStatusIndicator";
import ConnectionIndicator from "./ConnectionIndicator";

interface SidebarProps {
  showSettings: boolean;
  unackedCount: number;
  connectionStatus: ConnectionStatus;
}

interface NavItem {
  to: string;
  label: string;
  icon: React.ReactElement;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Dashboard", icon: <LayoutGrid size={22} /> },
  { to: "/bms", label: "Battery", icon: <Battery size={22} /> },
  { to: "/pcs", label: "Inverter", icon: <Zap size={22} /> },
  { to: "/alarms", label: "Alarms", icon: <Bell size={22} /> },
  { to: "/control", label: "Control", icon: <SlidersHorizontal size={22} /> },
  { to: "/energy", label: "Energy", icon: <BarChart3 size={22} /> },
];

export default function Sidebar({
  showSettings,
  unackedCount,
  connectionStatus,
}: SidebarProps): React.ReactElement {
  const { state: telemetry } = useTelemetryContext();

  return (
    <nav
      data-testid="sidebar"
      className="flex h-screen w-[60px] flex-col bg-bg-secondary xl:w-[200px]"
    >
      <div className="flex flex-1 flex-col gap-1 p-1">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            className={({ isActive }) =>
              `flex min-h-[44px] min-w-[44px] items-center gap-3 rounded-lg px-3 transition-none ${
                isActive
                  ? "bg-accent text-text-primary"
                  : "text-text-secondary active:bg-bg-card"
              }`
            }
          >
            <span className="relative flex-shrink-0">
              {item.icon}
              {item.label === "Alarms" && unackedCount > 0 && (
                <span className="absolute -top-1 -right-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[10px] font-bold text-white">
                  {unackedCount}
                </span>
              )}
            </span>
            <span className="hidden xl:inline">{item.label}</span>
          </NavLink>
        ))}

        {showSettings && (
          <NavLink
            to="/settings"
            className={({ isActive }) =>
              `flex min-h-[44px] min-w-[44px] items-center gap-3 rounded-lg px-3 transition-none ${
                isActive
                  ? "bg-accent text-text-primary"
                  : "text-text-secondary active:bg-bg-card"
              }`
            }
          >
            <span className="flex-shrink-0">
              <Settings size={22} />
            </span>
            <span className="hidden xl:inline">Settings</span>
          </NavLink>
        )}
      </div>

      <div className="flex items-center justify-center gap-1 border-t border-bg-card p-2">
        <CloudStatusIndicator cloud={telemetry.cloud} />
        <ConnectionIndicator status={connectionStatus} />
      </div>
    </nav>
  );
}
