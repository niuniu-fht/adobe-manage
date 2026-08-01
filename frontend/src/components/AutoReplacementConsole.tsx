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
  const [settingsDirty, setSettingsDirty] = useState(false);
  const query = useQuery({
    queryKey: ["auto-replacements"],
    queryFn: () => apiFetch<AutoReplacementsResponse>("/auto-replacements"),
    refetchInterval: (state) => state.state.data?.active || state.state.data?.queued ? 2000 : 10000
  });
  const operations = query.data?.operations || [];
  const active = operations.find((item) => item.id === query.data?.active_id);
  const latest = active || operations[0];
  useEffect(() => {
    if (!settingsDirty && query.data?.settings) {
      setCreditThreshold(String(query.data.settings.credit_threshold));
      setRefreshMinutes(String(query.data.settings.refresh_interval_minutes));
    }
  }, [query.data?.settings, settingsDirty]);

  const saveSettings = useMutation({
    mutationFn: () => {
      const threshold = Number(creditThreshold);
      const minutes = Number(refreshMinutes);
      if (!Number.isFinite(threshold) || threshold < 0) {
        throw new Error("补号阈值需要填写非负数字");
      }
      if (!Number.isInteger(minutes) || minutes < 1 || minutes > 1440) {
        throw new Error("额度刷新间隔需要填写 1-1440 分钟的整数");
      }
      return apiFetch<{
        settings: AutoReplacementsResponse["settings"];
      }>("/auto-replacements/settings", {
        method: "PUT",
        body: JSON.stringify({
          credit_threshold: threshold,
          refresh_interval_minutes: minutes
        })
      });
    },
    onSuccess: async (payload) => {
      setCreditThreshold(String(payload.settings.credit_threshold));
      setRefreshMinutes(String(payload.settings.refresh_interval_minutes));
      setSettingsDirty(false);
      emitToast("自动补号设置已保存，额度刷新已启动", "success");
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
  return <section className="auto-replace-console" aria-label="自动移除补号控制台">
    <header>
      <div className="console-title"><SquareTerminal size={17} /><strong>自动移除补号</strong><span className={`console-status${statusClass}`}>{latest ? STATUS_LABELS[latest.status] : "待命"}</span></div>
      <div className="console-context">
        {active ? <><LoaderCircle size={14} className="spin" /><strong>{active.instance_name}</strong><span>{active.source_email}</span><span>{active.trigger}</span></> : latest ? <><StatusIcon operation={latest} /><strong>{latest.instance_name}</strong><span>{latest.source_email}</span></> : <span>串行队列空闲</span>}
        <b>队列 {query.data?.queued ?? 0}</b>
      </div>
    </header>
    <div className="auto-replace-settings">
      <label><span>额度刷新</span><input type="number" min="1" max="1440" step="1" value={refreshMinutes} onChange={(event) => { setRefreshMinutes(event.target.value); setSettingsDirty(true); }} /><b>分钟</b></label>
      <label><span>补号阈值</span><input type="number" min="0" step="1" value={creditThreshold} onChange={(event) => { setCreditThreshold(event.target.value); setSettingsDirty(true); }} /></label>
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
