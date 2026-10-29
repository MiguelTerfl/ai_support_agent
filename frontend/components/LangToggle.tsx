"use client";

import { useI18n, type Lang } from "@/lib/i18n";

const OPTIONS: { value: Lang; label: string }[] = [
  { value: "es", label: "ES" },
  { value: "en", label: "EN" },
];

export default function LangToggle() {
  const { lang, setLang, t } = useI18n();
  return (
    <div className="lang-toggle" role="group" aria-label={t.interfaceLanguage}>
      {OPTIONS.map((o) => (
        <button key={o.value} type="button" aria-pressed={lang === o.value} onClick={() => setLang(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
