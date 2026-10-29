"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ConfirmCard from "./ConfirmCard";
import LangToggle from "./LangToggle";
import RichText from "./RichText";
import Trace from "./Trace";
import { useI18n, type Dict } from "@/lib/i18n";
import { readSSE } from "@/lib/sse";
import type { AgentEvent, ChatMessage, Health } from "@/lib/types";

const MAX_INPUT = 800;
const HISTORY_LIMIT = 10;

const DEMO_ORDERS: [string, string, keyof Dict["orderStates"]][] = [
  ["ORD-10001", "ana@example.com", "transit"],
  ["ORD-10002", "luis@example.com", "delivered"],
  ["ORD-10003", "maria@example.com", "processing"],
  ["ORD-10004", "jorge@example.com", "deliveredOld"],
  ["ORD-10005", "sofia@example.com", "cancelled"],
];

const uid = () => Math.random().toString(36).slice(2, 10);

function chatHttpError(t: Dict, status: number, retryAfter: string | null): string {
  if (status === 429) return t.errors.rateLimited(retryAfter);
  if (status === 502) return t.errors.unreachable;
  return t.errors.http(status);
}

function confirmHttpError(t: Dict, status: number, retryAfter: string | null): string {
  if (status === 400) return t.errors.invalidConfirmation;
  if (status === 409) return t.errors.alreadyConfirmed;
  if (status === 429) return t.errors.rateLimited(retryAfter);
  if (status === 502) return t.errors.unreachable;
  if (status === 500) return t.errors.ticketFailed;
  return t.errors.action;
}

function DemoData() {
  const { t } = useI18n();
  return (
    <div className="demo-data">
      <h2>{t.demoTitle}</h2>
      <p>{t.demoIntro}</p>
      <table>
        <tbody>
          {DEMO_ORDERS.map(([id, email, state]) => (
            <tr key={id}>
              <th scope="row">{id}</th>
              <td>{email}</td>
              <td>{t.orderStates[state]}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Chat() {
  const { lang, t } = useI18n();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  // ---- model status (the demo depends on the PC running Ollama being on) ----
  const checkHealth = useCallback(async () => {
    try {
      const r = await fetch("/api/health", { cache: "no-store" });
      setHealth((await r.json()) as Health);
    } catch {
      setHealth({ ok: false, llm: false, db: false, model: "" });
    }
  }, []);

  useEffect(() => {
    void checkHealth();
    const timer = setInterval(checkHealth, 20_000);
    return () => clearInterval(timer);
  }, [checkHealth]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  const offline = health !== null && !health.ok;

  const patch = useCallback((id: string, fn: (m: ChatMessage) => ChatMessage) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? fn(m) : m)));
  }, []);

  // ---- send a message and consume the agent's event stream ----
  const send = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text || busy) return;

      const userMsg: ChatMessage = { id: uid(), role: "user", content: text, trace: [], sources: [] };
      const botId = uid();
      const bot: ChatMessage = { id: botId, role: "assistant", content: "", trace: [], sources: [], running: true };
      const history = [...messages, userMsg]
        .filter((m) => m.content && !m.excluded)
        .slice(-HISTORY_LIMIT)
        .map((m) => ({ role: m.role, content: m.content }));

      setMessages((prev) => [...prev, userMsg, bot]);
      setInput("");
      setBusy(true);
      const controller = new AbortController();
      abortRef.current = controller;

      const knownErrors: Record<string, string> = {
        llm_unavailable: t.errors.llm_unavailable,
        busy: t.errors.busy,
        internal: t.errors.internal,
      };

      const apply = (ev: AgentEvent) => {
        switch (ev.type) {
          case "tool_call":
            patch(botId, (m) => ({ ...m, trace: [...m.trace, { kind: "tool", name: ev.name, args: ev.args, status: "running" }] }));
            break;
          case "tool_result":
            patch(botId, (m) => {
              const trace = [...m.trace];
              for (let i = trace.length - 1; i >= 0; i--) {
                const item = trace[i];
                if (item.kind === "tool" && item.name === ev.name && item.status === "running") {
                  trace[i] = { ...item, status: ev.ok ? "ok" : "error", summary: ev.summary };
                  break;
                }
              }
              return { ...m, trace };
            });
            break;
          case "guardrail":
            patch(botId, (m) => ({ ...m, trace: [...m.trace, { kind: "guardrail", stage: ev.stage, action: ev.action, detail: ev.detail }] }));
            break;
          case "confirmation":
            patch(botId, (m) => ({ ...m, confirmation: { token: ev.token, tool: ev.tool, args: ev.args, summary: ev.summary, state: "pending" } }));
            break;
          case "answer":
            patch(botId, (m) => ({ ...m, content: ev.content, sources: ev.sources, blocked: ev.blocked, excluded: ev.blocked }));
            // a blocked turn is not resent as history (the guardrail would block it again)
            if (ev.blocked) patch(userMsg.id, (m) => ({ ...m, excluded: true }));
            break;
          case "error":
            patch(botId, (m) => ({ ...m, error: knownErrors[ev.code] ?? ev.message }));
            break;
          case "done":
            patch(botId, (m) => ({ ...m, metrics: ev.metrics }));
            break;
          default:
            break;
        }
      };

      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ messages: history, lang }),
          signal: controller.signal,
        });
        if (!res.ok) throw new Error(chatHttpError(t, res.status, res.headers.get("retry-after")));
        for await (const ev of readSSE(res)) apply(ev);
      } catch (err) {
        const aborted = (err as Error).name === "AbortError";
        const message = aborted ? t.errors.stopped : err instanceof TypeError ? t.errors.unreachable : (err as Error).message || t.errors.generic;
        patch(botId, (m) => ({ ...m, error: aborted && m.content ? undefined : message, excluded: true }));
        patch(userMsg.id, (m) => ({ ...m, excluded: true }));
        if (!aborted) void checkHealth();
      } finally {
        patch(botId, (m) => ({ ...m, running: false }));
        setBusy(false);
        abortRef.current = null;
        inputRef.current?.focus();
      }
    },
    [busy, messages, patch, checkHealth, lang, t],
  );

  // ---- confirm / cancel an action with side effects (create ticket) ----
  const decide = useCallback(
    async (msgId: string, approve: boolean) => {
      const current = messages.find((m) => m.id === msgId)?.confirmation;
      if (!current) return;
      patch(msgId, (m) => ({ ...m, confirmation: m.confirmation && { ...m.confirmation, state: "sending", message: undefined } }));
      try {
        const res = await fetch("/api/confirm", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ token: current.token, approve }),
        });
        if (!res.ok) throw new Error(confirmHttpError(t, res.status, res.headers.get("retry-after")));
        const data = (await res.json().catch(() => ({}))) as { message?: string };
        patch(msgId, (m) => ({ ...m, confirmation: m.confirmation && { ...m.confirmation, state: approve ? "done" : "cancelled" } }));
        setMessages((prev) => [...prev, { id: uid(), role: "assistant", content: data.message ?? "", trace: [], sources: [] }]);
      } catch (err) {
        const message = err instanceof TypeError ? t.errors.unreachable : (err as Error).message || t.errors.action;
        patch(msgId, (m) => ({ ...m, confirmation: m.confirmation && { ...m.confirmation, state: "error", message } }));
      }
    },
    [messages, patch, t],
  );

  const reset = () => {
    abortRef.current?.abort();
    setMessages([]);
    setInput("");
    inputRef.current?.focus();
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send(input);
    }
  };

  const lampClass = health === null ? "idle" : health.ok ? "ok" : "bad";
  const lampText = health === null ? t.statusChecking : health.ok ? t.statusOnline(health.model) : t.statusOffline;

  return (
    <div className="shell">
      <aside className="rail" aria-label={t.railLabel}>
        <div className="brand">
          <span className="brand-name">Aurora Electro</span>
          <span className="brand-sub">{t.brandSub}</span>
        </div>
        <p className="status">
          <span className={`lamp ${lampClass}`} aria-hidden="true" />
          <span>{lampText}</span>
        </p>
        <DemoData />
        <div className="how">
          <h2>{t.howTitle}</h2>
          <ul>
            {t.how.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="brand compact">
            <span className="brand-name">Aurora Electro</span>
            <span className="status small">
              <span className={`lamp ${lampClass}`} aria-hidden="true" />
              <span>{health === null ? t.statusShortChecking : health.ok ? t.statusShortOnline : t.statusShortOffline}</span>
            </span>
          </div>
          <div className="topbar-actions">
            {messages.length > 0 && (
              <button type="button" className="btn ghost" onClick={reset}>
                {t.newChat}
              </button>
            )}
            <LangToggle />
          </div>
        </header>

        <div className="scroll">
          <div className="thread" role="log" aria-live="polite" aria-label={t.conversation}>
            {messages.length === 0 && (
              <section className="empty">
                <h1>{t.emptyTitle}</h1>
                <p>{t.emptySub}</p>
                <h2>{t.tryTitle}</h2>
                <ul className="samples">
                  {t.samples.map((s) => (
                    <li key={s.text}>
                      <button type="button" onClick={() => void send(s.text)} disabled={busy || offline}>
                        <span className="sample-text">{s.text}</span>
                        <span className="sample-note">{s.note}</span>
                      </button>
                    </li>
                  ))}
                </ul>
                <div className="only-mobile">
                  <DemoData />
                </div>
              </section>
            )}

            {messages.map((m) =>
              m.role === "user" ? (
                <div key={m.id} className="msg user">
                  <p>{m.content}</p>
                </div>
              ) : (
                <div key={m.id} className={`msg bot${m.blocked ? " blocked" : ""}`}>
                  <Trace items={m.trace} running={!!m.running} metrics={m.metrics} />
                  {m.content && (
                    <div className="bot-text">
                      <RichText text={m.content} />
                    </div>
                  )}
                  {m.confirmation && <ConfirmCard confirmation={m.confirmation} onDecide={(approve) => void decide(m.id, approve)} />}
                  {m.sources.length > 0 && (
                    <p className="sources">
                      {t.sources}{" "}
                      {m.sources.map((s, i) => (
                        <span key={`${s.source}-${s.heading}`}>
                          {i > 0 && "; "}
                          {s.source.replace(/\.md$/, "")} › {s.heading}
                        </span>
                      ))}
                    </p>
                  )}
                  {m.error && (
                    <p className="error" role="alert">
                      {m.error}
                    </p>
                  )}
                </div>
              ),
            )}
            <div ref={endRef} />
          </div>
        </div>

        <div className="composer-wrap">
          {offline && (
            <div className="offline" role="status">
              <p>{t.offline}</p>
              <button type="button" className="btn" onClick={() => void checkHealth()}>
                {t.recheck}
              </button>
            </div>
          )}
          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              void send(input);
            }}
          >
            <label htmlFor="msg" className="sr-only">
              {t.messageLabel}
            </label>
            <textarea
              id="msg"
              ref={inputRef}
              value={input}
              rows={1}
              maxLength={MAX_INPUT}
              placeholder={t.placeholder}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              disabled={offline}
            />
            {busy ? (
              <button type="button" className="btn" onClick={() => abortRef.current?.abort()}>
                {t.stop}
              </button>
            ) : (
              <button type="submit" className="btn primary" disabled={!input.trim() || offline}>
                {t.send}
              </button>
            )}
          </form>
          <p className="hint">
            {input.length > MAX_INPUT - 120 ? t.charCount(input.length, MAX_INPUT) : ""}
            {t.hint}
          </p>
        </div>
      </main>
    </div>
  );
}
