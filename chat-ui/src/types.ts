export type Role = "user" | "assistant";

export interface Message {
  id: string;
  role: Role;
  text: string;
  /** Set when the request failed, so the bubble can render as an error. */
  error?: string;
  /** Correlation id from the API, shown so a user can quote it in a report. */
  reference?: string;
}

/**
 * Runtime configuration, served from /config.json rather than baked into the
 * bundle - so the tenant, user and facility list can be changed by editing a
 * file and reloading, with no rebuild.
 */
export interface AppConfig {
  database_name: string;
  user_id: string;
  facm_code: string[];
  /** Optional label shown in the header. */
  tenant_label?: string;
}
