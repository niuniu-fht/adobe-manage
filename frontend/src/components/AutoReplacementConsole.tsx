import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, LoaderCircle, Save, SquareTerminal } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { apiFetch, emitToast, formatTime } from "../lib/api";
import type { AutoReplacementOperation, AutoReplacementsResponse } from "../types";

const STATUS_LABELS: Record<AutoReplacementOperation["status"], string> = {
  queued: "排队中",
  running: "执行中",
  done: "已完成",
  partial: "部分完成",
  failed: "失败",
  skipped: "已跳过"
};

export function AutoReplacementConsole() {
  const queryClient = useQueryClient();
  const scrollRef = useRef<HTMLPreElement>(null);
  const [creditThreshold, setCreditThreshold] = useState("0");
  const [refreshMinutes, setRefreshMinutes] = useState("5");
  const [concurrency, setConcurrency] = useState("3");
  const [autoRefillEnabled, setAutoRefillEnabled] = useState(true);
  const [refillMode, setRefillMode] = useState<"new_domain" | "registered_reuse">("new_domain");
  const [settingsDirty, setSettingsDirty] = useState(false);
  const query = useQuery({
    queryKey: ["auto-replacements"],
    queryFn: () => apiFetch<AutoReplacementsResponse>("/auto-replacements"),
    refetchInterval: (state) => state.state.data?.active || state.state.data?.queued ? 2000 : 10000
  });
  const operations = query.data?.operations || [];
  const activeIds = query.data?.active_ids || (query.data?.active_id ? [query.data.active_id] : []);
  const activeOperations = operations.filter((item) => activeIds.includes(item.id));
  const active = activeOperations[0];
  const latest = active || operations[0];
  useEffect(() => {
    if (!settingsDirty && query.data?.settings) {
      setCreditThreshold(String(query.data.settings.credit_threshold));
      setRefreshMinutes(String(query.data.settings.refresh_interval_minutes));
      setAutoRefillEnabled(Boolean(query.data.settings.enabled ?? true));
      setRefillMode(query.data.settings.refill_mode === "registered_reuse" ? "registered_reuse" : "new_domain");
      setConcurrency(String(query.data.settings.concurrency ?? 3));
    }
  }, [query.data?.settings, settingsDirty]);

  const saveSettings = useMutation({
    mutationFn: () => {
      const threshold = Number(creditThreshold);
      const minutes = Number(refreshMinutes);
      const maxConcurrency = Number(concurrency);
      if (!Number.isFinite(threshold) || threshold < 0) {
        throw new Error("补号阈值需要填写非负数字");
      }
      if (!Number.isInteger(minutes) || minutes < 1 || minutes > 1440) {
        throw new Error("额度刷新间隔需要填写 1-1440 分钟的整数");
      }
      if (!Number.isInteger(maxConcurrency) || maxConcurrency < 1 || maxConcurrency > 10) {
        throw new Error("并发数需要填写 1-10 的整数");
      }
      return apiFetch<{
        settings: AutoReplacementsResponse["settings"];
      }>("/auto-replacements/settings", {
        method: "PUT",
        body: JSON.stringify({
          credit_threshold: threshold,
          refresh_interval_minutes: minutes,
          enabled: autoRefillEnabled,
          refill_mode: refillMode,
          concurrency: maxConcurrency
        })
      });
    },
    onSuccess: async (payload) => {
      setCreditThreshold(String(payload.settings.credit_threshold));
      setRefreshMinutes(String(payload.settings.refresh_interval_minutes));
      setAutoRefillEnabled(Boolean(payload.settings.enabled ?? true));
      setRefillMode(payload.settings.refill_mode === "registered_reuse" ? "registered_reuse" : "new_domain");
      setConcurrency(String(payload.settings.concurrency ?? 3));
      setSettingsDirty(false);
      const modeText = payload.settings.refill_mode === "registered_reuse" ? "已注册补号" : "新注册补号";
      emitToast(payload.settings.enabled ? `自动补号设置已保存，模式：${modeText}，额度刷新已启动` : "已保存：异常账号移除后跳过后续补号", "success");
      await queryClient.invalidateQueries({ queryKey: ["auto-replacements"] });
    },
    onError: (error) => emitToast(error.message, "error")
  });
  const logText = useMemo(() => {
    const recent = [...operations].reverse().slice(-8);
    const lines = recent.flatMap((operation) => operation.logs.map((line) => line));
    return lines.length ? lines.join("\n") : "等待自动移除补号任务…";
  }, [operations]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [logText]);

  const statusClass = latest ? ` console-status-${latest.status}` : "";
  const refillModeText = refillMode === "registered_reuse" ? "已注册补号" : "新注册补号";
  const activeCount = query.data?.active_count ?? activeOperations.length;
  const concurrencyLimit = query.data?.settings.concurrency ?? (Number(concurrency) || 3);
  const switchRefillMode = (mode: "new_domain" | "registered_reuse") => {
    setRefillMode(mode);
    setSettingsDirty(true);
  };
  return <section className="auto-replace-console" aria-label="自动移除补号控制台">
    <header>
      <div className="console-title"><SquareTerminal size={17} /><strong>自动移除补号</strong><span className={`console-status${statusClass}`}>{latest ? STATUS_LABELS[latest.status] : "待命"}</span><span className={`refill-mode-badge refill-mode-${refillMode}`}>{refillModeText}</span></div>
      <div className="console-context">
        {active ? <><LoaderCircle size={14} className="spin" /><strong>并发执行 {activeCount}/{concurrencyLimit}</strong><span>{active.instance_name}</span><span>{active.source_email}</span><span>{active.trigger}</span>{active.remove_only && <span>仅移除</span>}</> : latest ? <><StatusIcon operation={latest} /><strong>{latest.instance_name}</strong><span>{latest.source_email}</span>{latest.remove_only && <span>仅移除</span>}</> : <span>并发队列空闲</span>}
        <b>队列 {query.data?.queued ?? 0}</b>
      </div>
    </header>
    <div className="auto-replace-settings">
      <label><span>额度刷新</span><input type="number" min="1" max="1440" step="1" value={refreshMinutes} onChange={(event) => { setRefreshMinutes(event.target.value); setSettingsDirty(true); }} /><b>分钟</b></label>
      <label><span>补号阈值</span><input type="number" min="0" step="1" value={creditThreshold} onChange={(event) => { setCreditThreshold(event.target.value); setSettingsDirty(true); }} /></label>
      <label><span>并发拉号</span><input type="number" min="1" max="10" step="1" value={concurrency} onChange={(event) => { setConcurrency(event.target.value); setSettingsDirty(true); }} /><b>个</b></label>
      <label className="auto-refill-toggle"><input type="checkbox" checked={autoRefillEnabled} onChange={(event) => { setAutoRefillEnabled(event.target.checked); setSettingsDirty(true); }} /><span>{autoRefillEnabled ? "补号开启" : "仅移除"}</span></label>
      <div className="refill-mode-switch" role="radiogroup" aria-label="补号方式">
        <span>补号方式</span>
        <button type="button" className={refillMode === "new_domain" ? "active" : ""} disabled={!autoRefillEnabled} aria-pressed={refillMode === "new_domain"} onClick={() => switchRefillMode("new_domain")}>新注册补号</button>
        <button type="button" className={refillMode === "registered_reuse" ? "active" : ""} disabled={!autoRefillEnabled} aria-pressed={refillMode === "registered_reuse"} onClick={() => switchRefillMode("registered_reuse")}>已注册补号</button>
      </div>
      <button className="icon-btn" title="保存自动补号设置" disabled={saveSettings.isPending || !settingsDirty} onClick={() => saveSettings.mutate()}><Save size={15} /></button>
      <small>{query.data?.credit_refresh.running ? "正在刷新额度" : `下次刷新 ${formatTime(query.data?.credit_refresh.next_refresh_at)}`}</small>
    </div>
    {query.isError
      ? <div className="console-query-error">{query.error.message}</div>
      : <pre ref={scrollRef} className="auto-replace-log" aria-live="polite">{logText}</pre>}
  </section>;
}

function StatusIcon({ operation }: { operation: AutoReplacementOperation }) {
  if (operation.status === "done") return <CircleCheck size={14} className="text-success" />;
  if (operation.status === "failed") return <CircleAlert size={14} className="text-danger" />;
  return <SquareTerminal size={14} />;
}
