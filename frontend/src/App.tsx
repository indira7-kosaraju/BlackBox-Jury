import { Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./app/Shell";
import { SignIn } from "./app/SignIn";
import { useSession } from "./app/session";
import { Evidence } from "./pages/Evidence";
import { Findings } from "./pages/Findings";
import { Lab } from "./pages/Lab";
import { Overview } from "./pages/Overview";
import { Regressions } from "./pages/Regressions";
import { RunDetail } from "./pages/RunDetail";
import { Runs } from "./pages/Runs";
import { SettingsPage } from "./pages/Settings";
import { Targets } from "./pages/Targets";

export function App() {
  const { api, restoring } = useSession();
  if (restoring) return null;
  if (!api) return <SignIn />;
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Overview />} />
        <Route path="targets" element={<Targets />} />
        <Route path="lab" element={<Lab />} />
        <Route path="runs" element={<Runs />} />
        <Route path="runs/:runId" element={<RunDetail />} />
        <Route path="evidence" element={<Evidence />} />
        <Route path="evidence/:executionId" element={<Evidence />} />
        <Route path="findings" element={<Findings />} />
        <Route path="findings/:findingId" element={<Findings />} />
        <Route path="regressions" element={<Regressions />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
