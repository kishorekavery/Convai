import { useEffect, useRef } from "react";
import { MAX_USER_INPUT } from "../api";

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  onStop: () => void;
  busy: boolean;
  disabled: boolean;
}

export function Composer({ value, onChange, onSend, onStop, busy, disabled }: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);

  // Grow with the content up to a cap, like a chat composer rather than a form
  // field - so a long question stays fully visible while typing.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  const tooLong = value.length > MAX_USER_INPUT;

  return (
    <div className="composer-wrap">
      <div className={`composer${tooLong ? " invalid" : ""}`}>
        <textarea
          ref={ref}
          value={value}
          rows={1}
          disabled={disabled}
          placeholder={disabled ? "Loading configuration…" : "Ask about work orders, calibrations, spares…"}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends, Shift+Enter makes a newline - the convention users
            // already expect from every chat client.
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (!busy && !tooLong) onSend();
            }
          }}
        />
        {busy ? (
          <button className="send stop" onClick={onStop} title="Stop generating">
            <span className="stop-square" />
          </button>
        ) : (
          <button
            className="send"
            onClick={onSend}
            disabled={disabled || !value.trim() || tooLong}
            title="Send  (Enter)"
          >
            ↑
          </button>
        )}
      </div>
      <div className="composer-hint">
        {tooLong ? (
          <span className="over">
            {value.length.toLocaleString()} / {MAX_USER_INPUT.toLocaleString()} characters — too long
          </span>
        ) : (
          <span>Enter to send · Shift+Enter for a new line · try “more” to page through results</span>
        )}
      </div>
    </div>
  );
}
