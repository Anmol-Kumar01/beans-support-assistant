import { useEffect, useRef } from "react";
import { Maximize2, RotateCcw, TriangleAlert } from "lucide-react";
import icon from "../assets/beans-icon.png";
import { renderMarkdown } from "../lib/markdown.js";
import { clockTime } from "../lib/history.js";

export const SOURCE_LABELS = {
  zendesk: "Article",
  youtube: "Video",
  release_note: "Release note",
  trainn: "Tutorial",
  api_reference: "API",
  beans_content: "Beans.ai",
};

function SourceCard({ source, messageId, onOpen }) {
  return (
    <li id={`src-${messageId}-${source.n}`} className="source">
      <button type="button" className="source-link" onClick={() => onOpen(source)} title="Open this source">
        <span className={`source-n tone-${(source.n - 1) % 4}`}>{source.n}</span>
        <span className="source-body">
          <span className="source-head">
            <span className="source-title">{source.title}</span>
            <span className="source-tag">{SOURCE_LABELS[source.source_type] || "Source"}</span>
          </span>
          {source.excerpt && <span className="source-excerpt">{source.excerpt}</span>}
        </span>
        <Maximize2 size={15} className="source-open" />
      </button>
    </li>
  );
}

function BotMessage({ message, onRetry, onOpenSource }) {
  const sources = message.sources || [];
  const byN = new Map(sources.map((s) => [s.n, s]));

  const onClick = (e) => {
    const chip = e.target.closest("button.cite");
    if (!chip) return;
    const card = document.getElementById(`src-${message.id}-${chip.dataset.n}`);
    if (!card) return;
    card.scrollIntoView({ block: "nearest", behavior: "smooth" });
    card.classList.remove("is-flash");
    void card.offsetWidth;
    card.classList.add("is-flash");
  };

  let body;
  if (message.status === "loading") {
    body = <span className="typing" aria-label="Thinking"><i /><i /><i /></span>;
  } else if (message.status === "error") {
    body = (
      <div className="bot-error">
        <TriangleAlert size={20} />
        <div>
          <p>{message.error}</p>
          <button type="button" className="retry" onClick={() => onRetry(message)}>
            <RotateCcw size={15} /> Try again
          </button>
        </div>
      </div>
    );
  } else {
    body = (
      <>
        <div
          className="answer"
          onClick={onClick}
          dangerouslySetInnerHTML={{ __html: renderMarkdown(message.text, message.status === "done" ? byN : null) }}
        />
        {message.evidenceStatus === "not_found" && (
          <p className="not-found">No Beans source covers this yet. Try rephrasing, or contact Beans support.</p>
        )}
        {sources.length > 0 && (
          <ol className="sources">
            {sources.map((s) => <SourceCard key={s.n} source={s} messageId={message.id} onOpen={onOpenSource} />)}
          </ol>
        )}
      </>
    );
  }

  return (
    <div className="msg msg-bot">
      <img className="bot-avatar" src={icon} alt="Beans Assistant" />
      <div className="bot-col">
        <div className="bot-card">{body}</div>
        {message.status === "done" && (
          <p className="msg-meta">Beans Assistant • {clockTime(message.time)}{message.stopped && <span className="msg-stopped"> • Stopped</span>}</p>
        )}
      </div>
    </div>
  );
}

export default function Chat({ messages, onRetry, onOpenSource }) {
  const endRef = useRef(null);
  const last = messages.at(-1);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [messages.length, last?.status, last?.text]);

  return (
    <div className="thread">
      {messages.map((m) =>
        m.role === "user" ? (
          <div key={m.id} className="msg msg-user">
            <div className="user-bubble">
              <p>{m.text}</p>
              <span className="user-time">{clockTime(m.time)}</span>
            </div>
          </div>
        ) : (
          <BotMessage key={m.id} message={m} onRetry={onRetry} onOpenSource={onOpenSource} />
        ),
      )}
      <div ref={endRef} />
    </div>
  );
}
