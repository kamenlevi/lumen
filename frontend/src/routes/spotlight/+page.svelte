<script lang="ts">
  import { onDestroy, onMount, tick } from "svelte";
  import { api, type SearchResult } from "$lib/ipc";

  // Tauri invoke + event APIs are imported dynamically so the SvelteKit
  // dev build also works in a plain browser.
  type InvokeFn = <T = unknown>(cmd: string, args?: Record<string, unknown>) => Promise<T>;
  type ListenFn = (event: string, cb: (e: { payload: unknown }) => void) => Promise<() => void>;
  let invoke: InvokeFn | null = null;
  let listen: ListenFn | null = null;

  let query = $state("");
  let results = $state<SearchResult[]>([]);
  let selected = $state(0);
  let loading = $state(false);
  let scopeFolder = $state<string | null>(null);
  let inputEl: HTMLInputElement | null = $state(null);
  let listEl: HTMLUListElement | null = $state(null);
  let unlistenShow: (() => void) | null = null;
  let unlistenKeydown: (() => void) | null = null;
  let debounce: ReturnType<typeof setTimeout> | null = null;

  // Keep the keyboard-selected row visible as you arrow past the fold.
  $effect(() => {
    const el = listEl?.children[selected] as HTMLElement | undefined;
    el?.scrollIntoView({ block: "nearest" });
  });

  const ROW_PX = 64;
  const HEADER_PX = 80;
  const MAX_ROWS = 8;

  async function resize() {
    await tick();
    if (!invoke) return;
    const visible = Math.min(results.length, MAX_ROWS);
    const h = visible === 0 ? HEADER_PX : HEADER_PX + visible * ROW_PX + 12;
    try {
      await invoke("resize_spotlight", { height: h });
    } catch { /* ignore */ }
  }

  async function close() {
    if (!invoke) return;
    try { await invoke("hide_spotlight"); } catch { /* ignore */ }
  }

  async function refreshScope() {
    if (!invoke) {
      scopeFolder = null;
      return;
    }
    try {
      const f = await invoke<string | null>("frontmost_folder");
      scopeFolder = f && f.length > 0 ? f : null;
    } catch {
      scopeFolder = null;
    }
  }

  async function runSearch() {
    const q = query.trim();
    if (!q) {
      results = [];
      selected = 0;
      await resize();
      return;
    }
    loading = true;
    try {
      const r = await api.search({
        query: q,
        top_k: 30,
        folder: scopeFolder,
      });
      results = r.results;
      selected = 0;
    } catch (e) {
      results = [];
    } finally {
      loading = false;
      await resize();
    }
  }

  function onInput() {
    if (debounce) clearTimeout(debounce);
    debounce = setTimeout(runSearch, 120);
  }

  async function activate(r: SearchResult) {
    // Open the selected photo in the main ("bigger") window, then dismiss
    // the spotlight — Enter takes you into the full UI, not the OS viewer.
    if (invoke) {
      try {
        await invoke("open_in_main", { route: `/photo/${r.id}/` });
        await close();
        return;
      } catch { /* fall through to browser */ }
    }
    window.location.href = `/photo/${r.id}/`;
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (results.length) selected = (selected + 1) % results.length;
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (results.length) selected = (selected - 1 + results.length) % results.length;
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (results[selected]) activate(results[selected]);
    }
  }

  onMount(async () => {
    try {
      const core = await import("@tauri-apps/api/core");
      invoke = core.invoke as unknown as InvokeFn;
      const ev = await import("@tauri-apps/api/event");
      listen = ev.listen as unknown as ListenFn;
    } catch {
      // Running in plain dev browser — tauri APIs unavailable.
    }

    if (listen) {
      unlistenShow = await listen("spotlight://show", async () => {
        query = "";
        results = [];
        selected = 0;
        await refreshScope();
        await resize();
        await tick();
        inputEl?.focus();
        inputEl?.select();
      });
    }

    await refreshScope();
    await tick();
    inputEl?.focus();
  });

  onDestroy(() => {
    if (unlistenShow) unlistenShow();
    if (debounce) clearTimeout(debounce);
  });
</script>

<svelte:window on:keydown={onKeydown} />

<div class="spotlight">
  <div class="search-row">
    <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="11" cy="11" r="7" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
    <input
      bind:this={inputEl}
      bind:value={query}
      on:input={onInput}
      placeholder="Search photos…"
      spellcheck="false"
      autocomplete="off" />
    {#if scopeFolder}
      <span class="scope" title={scopeFolder}>
        in {scopeFolder.split("/").pop() || scopeFolder}
      </span>
    {/if}
    {#if loading}<span class="dot" aria-label="searching" />{/if}
  </div>

  {#if results.length > 0}
    <div class="divider"></div>
    <ul class="results" role="listbox" bind:this={listEl}>
      {#each results as r, i (r.id)}
        <li
          role="option"
          aria-selected={i === selected}
          class:selected={i === selected}
          on:mouseenter={() => (selected = i)}
          on:click={() => activate(r)}>
          <img src={api.photoThumbUrl(r.id)} alt="" />
          <div class="meta">
            <div class="name">{r.path.split("/").pop()}</div>
            <div class="path">{r.path}</div>
          </div>
          <div class="score">{r.score.toFixed(2)}</div>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .spotlight {
    height: 100%;
    width: 100%;
    background: #15151a;
    box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.08) inset;
    color: #f3f3f3;
    overflow: hidden;
    -webkit-font-smoothing: antialiased;
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif;
  }
  .search-row {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 20px 22px;
    height: 80px;
    box-sizing: border-box;
  }
  .icon {
    width: 22px;
    height: 22px;
    color: rgba(255, 255, 255, 0.55);
    flex-shrink: 0;
  }
  input {
    flex: 1;
    background: transparent;
    border: none;
    color: inherit;
    outline: none;
    font-size: 22px;
    font-weight: 300;
    letter-spacing: -0.01em;
    min-width: 0;
  }
  input::placeholder { color: rgba(255, 255, 255, 0.32); }
  .scope {
    font-size: 11px;
    color: rgba(255, 255, 255, 0.55);
    background: rgba(255, 255, 255, 0.06);
    padding: 4px 9px;
    border-radius: 999px;
    white-space: nowrap;
    max-width: 220px;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #6366f1;
    animation: pulse 1s ease-in-out infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 0.4; transform: scale(0.9); }
    50% { opacity: 1; transform: scale(1.1); }
  }
  .divider {
    height: 1px;
    background: rgba(255, 255, 255, 0.07);
    margin: 0 14px;
  }
  .results {
    margin: 0;
    padding: 6px;
    list-style: none;
    max-height: calc(8 * 64px);
    overflow-y: auto;
    overflow-x: hidden;
    scrollbar-width: thin;
    scrollbar-color: rgba(255, 255, 255, 0.2) transparent;
  }
  .results::-webkit-scrollbar { width: 8px; }
  .results::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, 0.18);
    border-radius: 4px;
  }
  .results li {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 12px;
    height: 64px;
    box-sizing: border-box;
    border-radius: 10px;
    cursor: pointer;
    transition: background 80ms ease;
  }
  .results li.selected {
    background: rgba(99, 102, 241, 0.32);
  }
  .results img {
    width: 44px;
    height: 44px;
    object-fit: cover;
    border-radius: 6px;
    background: #111;
    flex-shrink: 0;
  }
  .meta { min-width: 0; flex: 1; }
  .name {
    font-size: 13px;
    font-weight: 500;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .path {
    font-size: 11px;
    color: rgba(255, 255, 255, 0.45);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .score {
    font-family: ui-monospace, "SF Mono", Menlo, monospace;
    font-size: 11px;
    color: rgba(255, 255, 255, 0.4);
  }
</style>
