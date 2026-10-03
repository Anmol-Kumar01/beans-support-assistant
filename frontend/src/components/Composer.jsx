import { useEffect, useRef, useState } from "react";
import { Mic, SendHorizontal, Square } from "lucide-react";

// Browser speech-to-text (Chrome, Edge, Safari). The mic button is hidden where unsupported.
// Chrome sends the audio to Google's speech service to transcribe it.
const Recognition = typeof window !== "undefined" ? window.SpeechRecognition || window.webkitSpeechRecognition : null;

export default function Composer({ inputRef, value, onChange, onSubmit, onStop, busy }) {
  const [listening, setListening] = useState(false);
  const [micError, setMicError] = useState(null);
  const recRef = useRef(null);
  const baseRef = useRef("");

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value, inputRef]);

  useEffect(() => () => recRef.current?.abort(), []);

  const canSend = !busy && value.trim().length > 0;
  const submit = (e) => {
    e.preventDefault();
    if (!canSend) return;
    recRef.current?.stop();
    onSubmit();
  };

  const toggleMic = () => {
    if (listening) {
      recRef.current?.stop();
      return;
    }
    const rec = new Recognition();
    rec.lang = navigator.language || "en-US";
    rec.interimResults = true;
    rec.continuous = false;
    baseRef.current = value.trim() ? `${value.trimEnd()} ` : "";
    rec.onresult = (e) => {
      let text = "";
      for (const result of e.results) text += result[0].transcript;
      onChange(baseRef.current + text);
    };
    rec.onerror = (e) => {
      setMicError(e.error === "not-allowed" ? "Microphone access was blocked. Allow it in the browser to use voice input." : null);
    };
    rec.onend = () => {
      setListening(false);
      inputRef.current?.focus();
    };
    recRef.current = rec;
    setMicError(null);
    setListening(true);
    rec.start();
  };

  return (
    <div className="composer-wrap">
      <form className="composer" onSubmit={submit}>
        <textarea
          ref={inputRef}
          rows={1}
          maxLength={2000}
          value={value}
          placeholder={listening ? "Listening…" : "Ask a question about Beans Route..."}
          aria-label="Ask a question about Beans Route"
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) submit(e);
          }}
        />
        <div className="composer-actions">
          {Recognition && (
            <button
              type="button"
              className={`composer-mic${listening ? " is-listening" : ""}`}
              onClick={toggleMic}
              aria-pressed={listening}
              aria-label={listening ? "Stop voice input" : "Speak your question"}
              title={listening ? "Stop listening" : "Speak your question"}
            >
              <Mic size={20} strokeWidth={2} />
            </button>
          )}
          {busy ? (
            <button type="button" className="composer-send" onClick={onStop} aria-label="Stop answering" title="Stop answering">
              <Square size={16} fill="currentColor" />
            </button>
          ) : (
            <button type="submit" className="composer-send" disabled={!canSend} aria-label="Send">
              <SendHorizontal size={22} strokeWidth={1.8} fill="currentColor" />
            </button>
          )}
        </div>
      </form>
      {micError && <p className="composer-hint">{micError}</p>}
    </div>
  );
}
