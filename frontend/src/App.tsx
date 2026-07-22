import { useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { ToastHost } from "./components/ToastHost";
import { apiFetch, setCsrfToken } from "./lib/api";
import { LoginPage } from "./pages/LoginPage";

const OverviewPage = lazy(() => import("./pages/OverviewPage").then((module) => ({ default: module.OverviewPage })));
const InstancesPage = lazy(() => import("./pages/InstancesPage").then((module) => ({ default: module.InstancesPage })));
const InstanceDetailPage = lazy(() => import("./pages/InstanceDetailPage").then((module) => ({ default: module.InstanceDetailPage })));
const TokensPage = lazy(() => import("./pages/TokensPage").then((module) => ({ default: module.TokensPage })));
const LogsPage = lazy(() => import("./pages/LogsPage").then((module) => ({ default: module.LogsPage })));
const AlertsPage = lazy(() => import("./pages/AlertsPage").then((module) => ({ default: module.AlertsPage })));
const AuditPage = lazy(() => import("./pages/AuditPage").then((module) => ({ default: module.AuditPage })));
const SettingsPage = lazy(() => import("./pages/SettingsPage").then((module) => ({ default: module.SettingsPage })));

export function App() {
  const queryClient = useQueryClient();
  const auth = useQuery({
    queryKey: ["auth"],
    queryFn: async () => {
      const result = await apiFetch<{ authenticated: boolean; csrf_token: string }>("/auth/me");
      setCsrfToken(result.csrf_token);
      return result;
    },
    retry: false,
    staleTime: 60000
  });

  function loggedIn() {
    auth.refetch();
  }

  function loggedOut() {
    queryClient.clear();
    queryClient.setQueryData(["auth"], null);
    auth.refetch();
  }

  if (auth.isLoading) return <div className="boot-screen"><span className="boot-mark">A2</span><p>正在连接运维中心</p></div>;
  if (!auth.data?.authenticated) return <><LoginPage onLogin={loggedIn} /><ToastHost /></>;

  return (
    <>
      <Suspense fallback={<div className="boot-screen"><span className="boot-mark">A2</span><p>正在加载页面</p></div>}>
        <Routes>
          <Route element={<AppShell onLogout={loggedOut} />}>
            <Route index element={<OverviewPage />} />
            <Route path="instances" element={<InstancesPage />} />
            <Route path="instances/:instanceId" element={<InstanceDetailPage />} />
            <Route path="tokens" element={<TokensPage />} />
            <Route path="logs" element={<LogsPage />} />
            <Route path="alerts" element={<AlertsPage />} />
            <Route path="audit" element={<AuditPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </Suspense>
      <ToastHost />
    </>
  );
}
