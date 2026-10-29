import type { AgentEvent } from "./types";

/** Reads an SSE stream (text/event-stream) from a fetch response and yields each parsed event. */
export async function* readSSE(response: Response): AsyncGenerator<AgentEvent> {
  if (!response.body) return;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const data = block
          .split("\n")
          .filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).trimStart())
          .join("\n");
        if (!data) continue; // comments (": ping") and empty blocks
        try {
          yield JSON.parse(data) as AgentEvent;
        } catch {
          /* incomplete or corrupt block: ignored */
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
