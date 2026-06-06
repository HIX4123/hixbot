export type ChatRole = "system" | "user" | "assistant";

export interface ChatTurn {
  role: ChatRole;
  content: string;
  name?: string;
}

export interface ChatResult {
  provider: string;
  content: string;
}

export interface ChatProvider {
  name: string;
  complete(messages: ChatTurn[], options?: { temperature?: number; maxTokens?: number }): Promise<ChatResult>;
  health(): Promise<{ ok: boolean; detail: string }>;
}

export interface EmbeddingProvider {
  name: string;
  embed(text: string): Promise<number[]>;
  health(): Promise<{ ok: boolean; detail: string }>;
}
