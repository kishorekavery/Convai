import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, ask, loadConfig } from "./api";
import { Composer } from "./components/Composer";
import { MessageBubble } from "./components/MessageBubble";
import { ThinkingIndicator } from "./components/ThinkingIndicator";
import type { AppConfig, Message } from "./types";

const SUGGESTIONS = [
  "List the work orders created in the last 30 days",
  "How many open calibration orders are there?",
  "Which machines had breakdowns yesterday?",
  "Show spare parts with no stock",
];

let seq = 0;
const nextId = () => `m${++seq}`;

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  // Distinct from `busy`: the indicator is shown only until the first token,
  // after which the streaming text itself is the progress signal.
  const [waiting, setWaiting] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedToBottom = useRef(true);

  useEffect(() => {
    loadConfig().then(setConfig).catch((e) => setConfigError(String(e.message ?? e)));
  }, []);

  // Only auto-scroll while the user is already at the bottom, so scrolling up
  // to re-read an earlier answer is not yanked back on every streamed chunk.
  useEffect(() => {
    if (pinnedToBottom.current) {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
    }
  }, [messages, waiting]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    pinnedToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  const send = useCallback(
    async (text: string) => {
      if (!config || !text.trim() || busy) return;

      const question = text.trim();
      const history = messages;
      const userMsg: Message = { id: nextId(), role: "user", text: question };
      const replyId = nextId();

      setMessages([...history, userMsg, { id: replyId, role: "assistant", text: "" }]);
      setDraft("");
      setBusy(true);
      setWaiting(true);
      pinnedToBottom.current = true;

      const controller = new AbortController();
      abortRef.current = controller;

      const appendChunk = (chunk: string) => {
        setWaiting(false);
        setMessages((prev) =>
          prev.map((m) => (m.id === replyId ? { ...m, text: m.text + chunk } : m)),
        );
      };

      try {
        // History excludes the message just sent: the API takes the current
        // question separately in user_input.
        await ask(question, history, config, { onChunk: appendChunk, signal: controller.signal });
      } catch (e) {
        if (controller.signal.aborted) {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === replyId ? { ...m, text: m.text || "(stopped)" } : m,
            ),
          );
        } else {
          const err = e as ApiError;
          setMessages((prev) =>
            prev.map((m) =>
              m.id === replyId
                ? { ...m, error: err.message ?? "Request failed", reference: err.reference }
                : m,
            ),
          );
        }
      } finally {
        setBusy(false);
        setWaiting(false);
        abortRef.current = null;
      }
    },
    [config, messages, busy],
  );

  const stop = () => abortRef.current?.abort();
  const reset = () => {
    abortRef.current?.abort();
    setMessages([]);
  };

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <span className="logo">M</span>
          <div>
            <div className="title">MaintWiz Assistant</div>
            <div className="subtitle">
              {config ? config.tenant_label ?? config.database_name : "…"}
            </div>
          </div>
        </div>
        {messages.length > 0 && (
          <button className="ghost" onClick={reset}>New chat</button>
        )}
      </header>

      <div className="scroll" ref={scrollRef} onScroll={onScroll}>
        <div className="thread">
          {configError && (
            <div className="config-error">
              <strong>Configuration problem.</strong> {configError}
              <div className="hint">
                Edit <code>config.json</code> beside the app and reload — it needs{" "}
                <code>database_name</code>, <code>user_id</code> and <code>facm_code</code>.
              </div>
            </div>
          )}

          {!configError && messages.length === 0 && (
            <div className="empty">
              <h1>What would you like to know?</h1>
              <p>Ask about work orders, breakdowns, calibrations, spares or compliance.</p>
              <div className="suggestions">
                {SUGGESTIONS.map((s) => (
                  <button key={s} disabled={!config} onClick={() => send(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m) =>
            m.role === "assistant" && !m.text && !m.error ? null : (
              <MessageBubble key={m.id} message={m} />
            ),
          )}
          {waiting && <ThinkingIndicator />}
        </div>
      </div>

      <div className="footer">
        <Composer
          value={draft}
          onChange={setDraft}
          onSend={() => send(draft)}
          onStop={stop}
          busy={busy}
          disabled={!config}
        />
      </div>
    </div>
  );
}
