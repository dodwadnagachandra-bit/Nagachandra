import React, { Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { TelemetryProvider } from "./context/TelemetryContext";
import Layout from "./components/Layout";

const LoginScreen = React.lazy(() => import("./screens/LoginScreen"));
const Dashboard = React.lazy(() => import("./screens/Dashboard"));
const BmsScreen = React.lazy(() => import("./screens/BmsScreen"));
const PcsScreen = React.lazy(() => import("./screens/PcsScreen"));
const AlarmScreen = React.lazy(() => import("./screens/AlarmScreen"));
const ControlScreen = React.lazy(() => import("./screens/ControlScreen"));
const EnergyScreen = React.lazy(() => import("./screens/EnergyScreen"));
const SettingsScreen = React.lazy(() => import("./screens/SettingsScreen"));

function LoadingFallback(): React.ReactElement {
  return (
    <div className="flex h-full items-center justify-center text-text-secondary">
      Loading...
    </div>
  );
}

/** Gate that shows LoginScreen when not authenticated. */
function AuthGate(): React.ReactElement {
  const { state } = useAuth();

  if (!state.token) {
    return <LoginScreen />;
  }

  return (
    <TelemetryProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="/bms" element={<BmsScreen />} />
          <Route path="/pcs" element={<PcsScreen />} />
          <Route path="/alarms" element={<AlarmScreen />} />
          <Route path="/control" element={<ControlScreen />} />
          <Route path="/energy" element={<EnergyScreen />} />
          <Route path="/settings" element={<SettingsScreen />} />
        </Route>
      </Routes>
    </TelemetryProvider>
  );
}

export default function App(): React.ReactElement {
  return (
    <AuthProvider>
      <Suspense fallback={<LoadingFallback />}>
        <AuthGate />
      </Suspense>
    </AuthProvider>
  );
}
