import { useQuery } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, LoaderCircle, SquareTerminal } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { apiFetch } from "../lib/api";
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
  const scrollRef = useRef<HTMLPreElement>(null);
  const query = useQuery({
    queryKey: ["auto-replacements"],
    queryFn: () => apiFetch<AutoReplacementsResponse>("/auto-replacements"),
    refetchInterval: (state) => state.state.data?.active || state.state.data?.queued ? 2000 : 10000
  });
  const operations = query.data?.operations || [];
  const active = operations.find((item) => item.id === query.data?.active_id);
  const latest = active || operations[0];
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
