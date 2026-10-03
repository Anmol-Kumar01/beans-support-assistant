// API client for the FastAPI server. Chat uses the SSE contract in evals/targets/chat_server.py.

export async function getHealth() {
  const res = await fetch("/v1/health");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getSources(type) {
  const res = await fetch(`/v1/sources${type ? `?type=${type}` : ""}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function* sseEvents(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    buffer = buffer.replace(/\r\n?/g, "\n");
    let cut;
    while ((cut = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let event = "message";
      const data = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      if (data.length) yield { event, data: JSON.parse(data.join("\n")) };
    }
    if (done) return;
  }
}

// Streams one answer. Calls onToken(textSoFar) while streaming; resolves with the final event.
// Aborting `signal` stops the stream and rejects with an AbortError.
export async function askQuestion(message, conversationId, onToken, signal) {
  let res;
  try {
    res = await fetch("/v1/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ message, conversation_id: conversationId }),
      signal,
    });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new Error("Can't reach the Beans Assistant server.");
  }
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(typeof detail.detail === "string" ? detail.detail : `Request failed (HTTP ${res.status}).`);
  }
  let text = "";
  for await (const { event, data } of sseEvents(res)) {
    if (event === "token") {
      text += data.text || "";
      onToken?.(text);
    } else if (event === "final") {
      return data;
    } else if (event === "error") {
      throw new Error(data.message || "Something went wrong.");
    }
  }
  throw new Error("The answer was cut off. Please try again.");
}

// Explore-Beans content (app/hub.py). Each part is fetched once per page load.
const hubCache = new Map();
export function getHub(part) {
  if (!hubCache.has(part)) {
    hubCache.set(part, fetch(`/v1/hub/${part}`).then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    }).catch((err) => {
      hubCache.delete(part); // allow a retry on the next visit
      throw err;
    }));
  }
  return hubCache.get(part);
}

export async function getDocument(documentId) {
  const res = await fetch(`/v1/documents/${encodeURIComponent(documentId)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
