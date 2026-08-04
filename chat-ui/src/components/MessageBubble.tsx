import type { Message } from "../types";

/**
 * The API's final-response prompt forbids markdown ("plain, conversational text
 * only"), so the text is rendered with whitespace preserved rather than parsed.
 * That keeps the record lists readable and avoids a markdown parser and the
 * sanitiser that would have to come with it.
 */
export function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";
  return (
    <div className={`msg ${isUser ? "user" : "assistant"}`}>
      <div className={`avatar ${isUser ? "user-avatar" : "assistant-avatar"}`}>
        {isUser ? "You" : "M"}
      </div>
      <div className={`bubble${message.error ? " error" : ""}`}>
        {message.error ? (
          <>
            <div className="error-title">{message.error}</div>
            {message.reference && (
              <div className="error-ref">
                Reference <code>{message.reference}</code> — quote this if you report it.
              </div>
            )}
          </>
        ) : (
          message.text
        )}
      </div>
    </div>
  );
}
