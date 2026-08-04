import { useEffect, useState } from "react";

/**
 * Shown between sending and the first streamed token.
 *
 * That gap is real: a new question runs classification, embedding, retrieval,
 * SQL generation and execution before the answer starts - about 5-7 seconds.
 * The staged labels tell the user the system is working rather than stuck.
 * They are time-based, not a report of actual pipeline state, so they are
 * worded as expectations rather than claims.
 */
const STAGES = [
  { after: 0, label: "Thinking" },
  { after: 1500, label: "Understanding the question" },
  { after: 3500, label: "Looking up the data" },
  { after: 6500, label: "Writing the answer" },
  { after: 12000, label: "Still working - larger result sets take longer" },
];

export function ThinkingIndicator() {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const started = Date.now();
    const id = setInterval(() => setElapsed(Date.now() - started), 250);
    return () => clearInterval(id);
  }, []);

  const stage = [...STAGES].reverse().find((s) => elapsed >= s.after) ?? STAGES[0];

  return (
    <div className="msg assistant">
      <div className="avatar assistant-avatar">M</div>
      <div className="bubble thinking" aria-live="polite">
        <span className="thinking-label">{stage.label}</span>
        <span className="dots" aria-hidden="true">
          <i /><i /><i />
        </span>
      </div>
    </div>
  );
}
