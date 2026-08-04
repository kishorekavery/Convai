import type { AppConfig, Message } from "./types";

/** models/data_models.py: MAX_USER_INPUT_LENGTH */
export const MAX_USER_INPUT = 2000;
/** models/data_models.py: MAX_CHAT_HISTORY_LENGTH */
const MAX_CHAT_HISTORY = 10000;

/**
 * Build the flat chat_history string the API expects.
 *
 * The backend parses this with
 *   /\b(user|ai)\s*:\s*(.*?)(?=\s*\b(?:user|ai)\s*:|\Z)/
 * so turns are "user: ..." / "ai: ..." joined by ", " - matching the shape of
 * the production payload. Get this wrong and follow-up detection and pagination
 * silently stop working, because this string is what the classifier reads.
 *
 * Oldest turns are dropped until the result fits MAX_CHAT_HISTORY, since the
 * request is rejected with 422 above that. Recent turns matter most: the
 * classifier only reads the last three exchanges.
 */
export function buildChatHistory(messages: Message[]): string {
  const turns = messages
    .filter((m) => !m.error && m.text.trim())
    .map((m) => `${m.role === "user" ? "user" : "ai"}: ${m.text.replace(/\s+/g, " ").trim()}`);

  while (turns.length > 0 && turns.join(", ").length > MAX_CHAT_HISTORY) {
    turns.shift();
  }
  return turns.join(", ");
}

export interface AskCallbacks {
  /** Called for each streamed chunk, so the bubble grows as tokens arrive. */
  onChunk: (chunk: string) => void;
  signal?: AbortSignal;
}

export class ApiError extends Error {
  status: number;
  reference?: string;
  constructor(status: number, message: string, reference?: string) {
    super(message);
    this.status = status;
    this.reference = reference;
  }
}

/** Pull the "(reference: abc123)" the API appends to unexpected errors. */
function extractReference(detail: string): string | undefined {
  return /\(reference:\s*([a-f0-9]+)\)/i.exec(detail)?.[1];
}

function friendlyError(status: number, detail: string): string {
  switch (status) {
    case 403:
      return "No AI quota is assigned to this user.";
    case 429:
      return "This user has used their entire AI quota.";
    case 422:
      return detail || "That question could not be answered from the available data.";
    case 504:
      return "That query took too long to run. Try narrowing it - a shorter time range, a specific facility, or a count instead of a full list.";
    default:
      return detail || "Something went wrong.";
  }
}

/**
 * Send a question and stream the answer back.
 *
 * The endpoint returns `text/plain` streamed - not SSE and not JSON - so this
 * reads the body with a ReadableStream reader rather than EventSource.
 */
export async function ask(
  question: string,
  history: Message[],
  config: AppConfig,
  { onChunk, signal }: AskCallbacks,
): Promise<void> {
  const response = await fetch("/convai/AI/chat-completion", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal,
    body: JSON.stringify({
      database_name: config.database_name,
      user_input: question,
      user_id: config.user_id,
      facm_code: config.facm_code,
      chat_history: buildChatHistory(history),
      eval_mode: false,
    }),
  });

  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail = typeof body?.detail === "string" ? body.detail : JSON.stringify(body?.detail ?? "");
    } catch {
      detail = await response.text().catch(() => "");
    }
    throw new ApiError(response.status, friendlyError(response.status, detail), extractReference(detail));
  }

  if (!response.body) throw new ApiError(response.status, "The server returned an empty response.");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    onChunk(decoder.decode(value, { stream: true }));
  }
}

export async function loadConfig(): Promise<AppConfig> {
  const res = await fetch("/config.json", { cache: "no-store" });
  if (!res.ok) throw new Error(`Could not load config.json (HTTP ${res.status})`);
  const cfg = (await res.json()) as AppConfig;
  for (const key of ["database_name", "user_id", "facm_code"] as const) {
    if (!cfg[key] || (Array.isArray(cfg[key]) && cfg[key].length === 0)) {
      throw new Error(`config.json is missing "${key}"`);
    }
  }
  return cfg;
}
