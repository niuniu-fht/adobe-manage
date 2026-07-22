import { useEffect, useState } from "react";

interface ToastState {
  id: number;
  message: string;
  tone: "success" | "error" | "info";
}

export function ToastHost() {
  const [toast, setToast] = useState<ToastState | null>(null);

  useEffect(() => {
    const listener = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      setToast({ id: Date.now(), message: detail.message, tone: detail.tone || "info" });
    };
    window.addEventListener("manager-toast", listener);
    return () => window.removeEventListener("manager-toast", listener);
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 3600);
    return () => window.clearTimeout(timer);
  }, [toast]);

  if (!toast) return null;
  return <div className={`toast toast-${toast.tone}`}>{toast.message}</div>;
}
