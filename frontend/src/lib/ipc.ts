// HTTP client for the Python sidecar.
//
// The Tauri shell spawns the sidecar, reads LUMEN_PORT from stdout, and
// stores it in window.__LUMEN_PORT before the SvelteKit app hydrates.
// In `pnpm dev` (browser), fall back to an env var or 8765.

import { browser } from "$app/environment";

declare global {
  interface Window {
    __LUMEN_PORT?: number;
  }
}

let cachedBase: string | null = null;

export function sidecarBase(): string {
  if (cachedBase) return cachedBase;
  let port: number | undefined;
  if (browser && window.__LUMEN_PORT) port = window.__LUMEN_PORT;
  if (!port) {
    const envPort = (import.meta as any).env?.VITE_SIDECAR_PORT;
    if (envPort) port = Number(envPort);
  }
  if (!port) port = 8765;
  cachedBase = `http://127.0.0.1:${port}`;
  return cachedBase;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(sidecarBase() + path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const j = await r.json();
      detail = j.detail ?? detail;
    } catch { /* ignore */ }
    throw new Error(`${r.status} ${detail}`);
  }
  return r.json() as Promise<T>;
}

// ---------- types ----------

export interface Folder {
  id: number;
  path: string;
  added_at: number;
  watch: number;
  image_count: number;
}

export interface SearchResult {
  id: number;
  score: number;
  path: string;
  thumb_path: string | null;
  w: number | null;
  h: number | null;
  taken_at: string | null;
  camera: string | null;
  lat: number | null;
  lon: number | null;
  // Tier-2 quality (null until the quality pass has run on the image)
  sharpness?: number | null;
  is_blurry?: number | null;
  is_dark?: number | null;
  is_bright?: number | null;
  subject_out_of_focus?: number | null;
  dominant_hex?: string | null;
}

export interface PhotoDetail {
  id: number;
  path: string;
  w: number | null;
  h: number | null;
  taken_at: string | null;
  camera: string | null;
  lat: number | null;
  lon: number | null;
  phash: string | null;
  thumb_path: string | null;
  indexed_at: number;
  mtime: number;
}

export interface IndexProgress {
  total: number;
  seen: number;
  indexed: number;
  moved: number;
  skipped: number;
  failed: number;
  pruned: number;
  current_path: string | null;
  done: boolean;
  started_at: number;
  error: string | null;
}

export interface Chat {
  id: number;
  title: string;
  created_at: number;
  updated_at: number;
  message_count?: number;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  created_at: number;
  results: SearchResult[];
}

export interface ModelInfo {
  name: string;
  size_gb?: number;
  vision?: boolean;
  installed?: boolean;
  min_ram_gb?: number;
  fits_ram?: boolean;
  blurb?: string;
}

export interface CloudModel {
  id: string;
  name: string;
  in: number;
  out: number;
}

export interface ModelsOverview {
  hardware: {
    cores: number;
    ram_total_gb: number | null;
    ram_available_gb: number | null;
    gpu: string | null;
  };
  ollama_up: boolean;
  installed: ModelInfo[];
  recommended: ModelInfo[];
  recommendation: { provider: string; model: string; why: string };
  selected: { provider: string; model: string };
}

export interface PullStatus {
  name: string | null;
  status: string;
  percent: number;
  error: string | null;
}

export interface Settings {
  model_name: string;
  pretrained: string;
  device: string;
  data_dir: string;
}

export interface SearchOptions {
  query: string;
  top_k?: number;
  offset?: number;
  folder?: string | null;
  camera?: string | null;
  date_from?: string | null;
  date_to?: string | null;
  has_gps?: boolean | null;
}

// ---------- api ----------

export const api = {
  health: () => req<{ ok: boolean; data_dir: string; db: string }>("/healthz"),

  listFolders: () => req<Folder[]>("/library/folders"),
  addFolder: (path: string) =>
    req<{ ok: boolean; path: string }>("/library/folders", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  removeFolder: (path: string) =>
    req<{ ok: boolean }>(`/library/folders?path=${encodeURIComponent(path)}`, {
      method: "DELETE",
    }),

  indexStart: (path: string) =>
    req<{ ok: boolean; folder: string }>("/index/start", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  indexStatus: (folder?: string) =>
    req<IndexProgress | Record<string, IndexProgress>>(
      folder ? `/index/status?folder=${encodeURIComponent(folder)}` : "/index/status"
    ),
  indexPrune: (folder?: string) =>
    req<{ ok: boolean; pruned: number }>(
      folder ? `/index/prune?folder=${encodeURIComponent(folder)}` : "/index/prune",
      { method: "POST" }
    ),
  setWatch: (path: string, watch: boolean) =>
    req<{ ok: boolean; watch: boolean }>("/library/folders/watch", {
      method: "POST",
      body: JSON.stringify({ path, watch }),
    }),

  search: (opts: SearchOptions) =>
    req<{ results: SearchResult[] }>("/search", {
      method: "POST",
      body: JSON.stringify(opts),
    }),

  photo: (id: number) => req<PhotoDetail>(`/photo/${id}`),
  photoSimilar: (id: number, k = 20) =>
    req<{ results: SearchResult[] }>(`/photo/${id}/similar?k=${k}`),
  photoThumbUrl: (id: number) => `${sidecarBase()}/photo/${id}/thumb`,
  photoFileUrl: (id: number) => `${sidecarBase()}/photo/${id}/file`,

  getSettings: () => req<Settings>("/settings"),
  setSettings: (s: Partial<Settings>) =>
    req<Settings>("/settings", { method: "POST", body: JSON.stringify(s) }),

  // ---------- models ----------
  modelsOverview: () => req<ModelsOverview>("/models"),
  modelsCloud: () => req<CloudModel[]>("/models/cloud"),
  modelPull: (name: string) =>
    req<{ ok: boolean }>("/models/pull", { method: "POST", body: JSON.stringify({ name }) }),
  modelPullStatus: () => req<PullStatus>("/models/pull/status"),
  modelSelect: (provider: string, model: string) =>
    req<{ provider: string; model: string }>("/models/select", {
      method: "POST",
      body: JSON.stringify({ provider, model }),
    }),

  // ---------- chat ----------
  listChats: () => req<Chat[]>("/chats"),
  createChat: () => req<Chat>("/chats", { method: "POST" }),
  getChat: (id: number) =>
    req<{ chat: Chat; messages: ChatMessage[] }>(`/chats/${id}`),
  renameChat: (id: number, title: string) =>
    req<Chat>(`/chats/${id}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  deleteChat: (id: number) =>
    req<{ ok: boolean }>(`/chats/${id}`, { method: "DELETE" }),
  sendMessage: (id: number, text: string) =>
    req<{ user: ChatMessage; assistant: ChatMessage; title: string }>(
      `/chats/${id}/messages`,
      { method: "POST", body: JSON.stringify({ text }) }
    ),
};
