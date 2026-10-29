import Chat from "@/components/Chat";
import { I18nProvider } from "@/lib/i18n";

export default function Page() {
  return (
    <I18nProvider>
      <Chat />
    </I18nProvider>
  );
}
