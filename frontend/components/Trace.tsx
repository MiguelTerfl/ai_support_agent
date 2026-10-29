"use client";

import { useId, useState } from "react";
import { useI18n, type Dict } from "@/lib/i18n";
import type { ChatMessage, TraceItem } from "@/lib/types";

function describeDetail(detail: string, labels: Dict["detailLabels"]): string {
  return detail
    .split(",")
    .map((d) => d.trim().replace(/^pii:/, ""))
    .filter(Boolean)
    .map((d) => labels[d] ?? d)
    .join(", ");
}

function formatArgs(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([k, v]) => {
      const s = String(v);
      return `${k}: ${s.length > 48 ? s.slice(0, 47) + "…" : s}`;
    })
    .join(" · ");
}

type Lamp = "run" | "ok" | "warn" | "bad";

function itemLamp(item: TraceItem): Lamp {
  if (item.kind === "tool") return item.status === "running" ? "run" : item.status === "ok" ? "ok" : "warn";
  return item.action === "blocked" ? "bad" : "warn";
}

function overallLamp(items: TraceItem[], running: boolean): Lamp {
  if (running) return "run";
  const lamps = items.map(itemLamp);
  if (lamps.includes("bad")) return "bad";
  if (lamps.includes("warn")) return "warn";
  return "ok";
}

export default function Trace({ items, running, metrics }: { items: TraceItem[]; running: boolean; metrics?: ChatMessage["metrics"] }) {
  const { t } = useI18n();
  const id = useId();
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const open = userOpen ?? running;
  if (items.length === 0 && !running) return null;

  const seconds = metrics ? `${(metrics.elapsed_ms / 1000).toFixed(1)} s` : null;
  const label = running ? (items.length === 0 ? t.thinking : t.working) : t.steps(items.length, seconds);

  return (
    <div className="trace">
      <button
        type="button"
        className="trace-toggle"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setUserOpen(!open)}
        disabled={items.length === 0}
      >
        <span className={`lamp ${overallLamp(items, running)}`} aria-hidden="true" />
        <span>{label}</span>
        {items.length > 0 && <span className="chev" aria-hidden="true" />}
      </button>
      {open && items.length > 0 && (
        <ol className="trace-list" id={id}>
          {items.map((item, i) => (
            <li key={i} className="trace-item">
              <span className={`lamp ${itemLamp(item)}`} aria-hidden="true" />
              {item.kind === "tool" ? (
                <div>
                  <div className="trace-title">{t.toolLabels[item.name] ?? item.name}</div>
                  <div className="trace-meta">{formatArgs(item.args) || t.noArgs}</div>
                  {item.summary && <div className="trace-meta">{item.summary}</div>}
                </div>
              ) : (
                <div>
                  <div className="trace-title">{t.guardrailTitle(item.stage, t.actionLabels[item.action] ?? item.action)}</div>
                  {item.detail && <div className="trace-meta">{describeDetail(item.detail, t.detailLabels)}</div>}
                </div>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
