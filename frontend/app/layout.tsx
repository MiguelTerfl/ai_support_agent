import type { Metadata, Viewport } from "next";
import "@fontsource-variable/instrument-sans/wdth.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Support · Aurora Electro",
  description: "Demo of an AI support agent: RAG, tools and guardrails on a local model.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ECEFF0" },
    { media: "(prefers-color-scheme: dark)", color: "#0E181D" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
