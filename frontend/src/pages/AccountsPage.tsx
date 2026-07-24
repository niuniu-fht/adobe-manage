import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, RefreshCw, Search, Upload, WalletCards } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { AccountBatchBar } from "../components/AccountBatchBar";
import { AccountTable } from "../components/AccountTable";
import { CookieImportModal } from "../components/CookieImportModal";
import { apiDownload, apiFetch, emitToast } from "../lib/api";
import type { AccountsResponse, FleetCreditsRefreshResponse, FleetInstance } from "../types";

export function AccountsPage({ fixedInstanceId }: { fixedInstanceId?: string }) {
  const queryClient = useQueryClient();
  const [instanceFilter, setInstanceFilter] = useState(fixedInstanceId || "");
  const [healthFilter, setHealthFilter] = useState("");
  const [search, setSearch] = useState("");
  const [lowOnly, setLowOnly] = useState(false);
  const [failingOnly, setFailingOnly] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const instances = useQuery({
    queryKey: ["instances"],
    queryFn: () => apiFetch<{ instances: FleetInstance[] }>("/instances")
  });
  const accounts = useQuery({
    queryKey: ["accounts", fixedInstanceId],
    queryFn: () => apiFetch<AccountsResponse>(`/accounts${fixedInstanceId ? `?instance_ids=${fixedInstanceId}` : ""}`),
    staleTime: 15000
  });
  useEffect(() => setSelected(new Set()), [instanceFilter, healthFilter, lowOnly, failingOnly, search]);

  const rows = useMemo(() => (accounts.data?.accounts || []).filter((item) => {
    if (instanceFilter && item.instance_id !== instanceFilter) return false;
    if (healthFilter && item.health !== healthFilter) return false;
    if (lowOnly && !item.low_credit) return false;
    if (failingOnly && item.health !== "refresh_failed") return false;
    if (search) {
      const haystack = `${item.instance_name} ${item.name} ${item.display_name} ${item.email} ${item.user_id}`.toLowerCase();
      if (!haystack.includes(search.toLowerCase())) return false;
    }
    return true;
  }), [accounts.data, instanceFilter, healthFilter, lowOnly, failingOnly, search]);

  const selectedInstance = instances.data?.instances.find((item) => item.id === (fixedInstanceId || instanceFilter));
  const refreshBalances = useMutation({
    mutationFn: () => {
      const targetId = fixedInstanceId || instanceFilter;
      return apiFetch<FleetCreditsRefreshResponse | Record<string, unknown>>(
        targetId ? `/instances/${targetId}/tokens/credits-batch` : "/fleet/tokens/credits-batch",
        {
          method: "POST",
          body: JSON.stringify({})
        }
      );
    },
    onSuccess: async (payload) => {
      const fleet = "summary" in payload ? payload as FleetCreditsRefreshResponse : null;
      emitToast(
        fleet
          ? `全部实例额度已刷新：成功 ${fleet.summary.refreshed_count}，失败 ${fleet.summary.failed_count}`
          : "账号余额已刷新",
        fleet?.status === "partial" ? "info" : "success"
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["accounts"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] })
      ]);
    },
    onError: (error) => emitToast(error.message, "error")
  });

  function exportCookies() {
    const targetId = fixedInstanceId || instanceFilter;
    if (!targetId || !selectedInstance) {
      emitToast("请先选择一个实例", "error");
      return;
    }
    void apiDownload(`/instances/${targetId}/refresh-profiles/export`, {}, `${selectedInstance.name}-cookies.json`);
  }

  return <div className="page-stack">
    <section className={fixedInstanceId ? "account-actions" : "page-toolbar"}>
      {!fixedInstanceId && <div><strong>Cookie 账号</strong><span>{rows.length} 条当前结果 · 低积分阈值 {accounts.data?.low_credit_threshold ?? 100}</span></div>}
      <div className="inline-actions">
        <button className="primary-btn" onClick={() => setImportOpen(true)}><Upload size={16} />导入 Cookie</button>
        <button className="secondary-btn" disabled={refreshBalances.isPending} onClick={() => refreshBalances.mutate()}><WalletCards size={16} />{fixedInstanceId || instanceFilter ? "刷新实例额度" : "刷新全部实例额度"}</button>
        <button className="secondary-btn" disabled={!fixedInstanceId && !instanceFilter} onClick={exportCookies}><Download size={16} />导出 Cookie</button>
        <button className="icon-btn" title="刷新账号列表" onClick={() => accounts.refetch()}><RefreshCw size={17} className={accounts.isFetching ? "spin" : ""} /></button>
      </div>
    </section>
    <section className="filter-bar account-filters">
      {!fixedInstanceId && <select value={instanceFilter} onChange={(event) => setInstanceFilter(event.target.value)}><option value="">全部实例</option>{instances.data?.instances.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>}
      <select value={healthFilter} onChange={(event) => setHealthFilter(event.target.value)}><option value="">全部健康状态</option><option value="healthy">正常</option><option value="low_credit">低积分</option><option value="balance_unknown">余额未知</option><option value="refresh_failed">刷新失败</option><option value="credential_error">凭证异常</option><option value="disabled">已暂停</option></select>
      <label className="filter-check"><input type="checkbox" checked={lowOnly} onChange={(event) => setLowOnly(event.target.checked)} />低积分</label>
      <label className="filter-check"><input type="checkbox" checked={failingOnly} onChange={(event) => setFailingOnly(event.target.checked)} />刷新失败</label>
      <label className="search-box"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="账号、邮箱或 Adobe ID" /></label>
    </section>
    {accounts.data?.status === "partial" && <div className="warning-banner">部分实例读取失败：{accounts.data.errors?.map((item) => item.instance_name).join("、")}</div>}
    {accounts.isError && <div className="error-banner">{accounts.error.message}</div>}
    <AccountBatchBar accounts={accounts.data?.accounts || []} selected={selected} onSelectionChange={setSelected} />
    <section className="table-section"><div className="table-scroll"><AccountTable accounts={rows} loading={accounts.isLoading} selected={selected} onSelectionChange={setSelected} /></div></section>
    <CookieImportModal
      open={importOpen}
      onClose={() => setImportOpen(false)}
      fixedInstanceId={fixedInstanceId || instanceFilter || undefined}
      fixedInstanceName={selectedInstance?.name}
    />
  </div>;
}
