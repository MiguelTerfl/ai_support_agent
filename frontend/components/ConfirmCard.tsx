"use client";

import { useI18n } from "@/lib/i18n";
import type { Confirmation } from "@/lib/types";

const FIELD_ORDER = ["subject", "description", "email", "order_id"] as const;

export default function ConfirmCard({ confirmation, onDecide }: { confirmation: Confirmation; onDecide: (approve: boolean) => void }) {
  const { t } = useI18n();
  const { state, args, message } = confirmation;
  const finished = state === "done" || state === "cancelled";

  return (
    <section className="confirm" aria-label={t.confirmAria}>
      <h3>{finished ? (state === "done" ? t.confirmDone : t.confirmCancelled) : t.confirmTitle}</h3>
      <dl>
        {FIELD_ORDER.filter((key) => args[key]).map((key) => (
          <div key={key}>
            <dt>{t.fields[key]}</dt>
            <dd>{args[key]}</dd>
          </div>
        ))}
      </dl>
      {message && (
        <p className={state === "error" ? "confirm-note error" : "confirm-note"} role={state === "error" ? "alert" : "status"}>
          {message}
        </p>
      )}
      {!finished && (
        <div className="confirm-actions">
          <button type="button" className="btn primary" disabled={state === "sending"} onClick={() => onDecide(true)}>
            {state === "sending" ? t.creating : t.createTicket}
          </button>
          <button type="button" className="btn" disabled={state === "sending"} onClick={() => onDecide(false)}>
            {t.cancel}
          </button>
        </div>
      )}
    </section>
  );
}
