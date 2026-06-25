<script lang="ts">
  import { onMount } from "svelte";
  import { goto } from "$app/navigation";
  import { applyMode, hideWindow, spotlightQuery, mode } from "$lib/shell";

  let query = $state("");
  let inputEl: HTMLInputElement | null = $state(null);

  function focus() {
    inputEl?.focus();
    inputEl?.select();
  }

  async function submit() {
    const q = query.trim();
    if (!q) return;
    spotlightQuery.set(q);     // the search page picks this up
    await applyMode("expanded");
    goto("/search/");
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === "Enter") { e.preventDefault(); submit(); }
    else if (e.key === "Escape") { e.preventDefault(); hideWindow(); }
  }

  onMount(focus);
  // Refocus whenever the bar is (re)shown.
  $effect(() => {
    if ($mode === "compact") setTimeout(focus, 30);
  });
</script>

<div class="bar">
  <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="11" cy="11" r="7" />
    <line x1="21" y1="21" x2="16.65" y2="16.65" />
  </svg>
  <input
    bind:this={inputEl}
    bind:value={query}
    onkeydown={onKey}
    placeholder="Search your photos…  ·  ask “blurry photos”, “pink”, “sunset”"
    spellcheck="false"
    autocomplete="off" />
</div>

<style>
  .bar {
    display: flex;
    align-items: center;
    gap: 14px;
    height: 100%;
    width: 100%;
    box-sizing: border-box;
    padding: 0 24px;
    background: #15151a;
    box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.08) inset;
    color: #f3f3f3;
    -webkit-font-smoothing: antialiased;
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif;
  }
  .icon { width: 24px; height: 24px; color: rgba(255, 255, 255, 0.5); flex-shrink: 0; }
  input {
    flex: 1;
    background: transparent;
    border: none;
    color: inherit;
    outline: none;
    font-size: 23px;
    font-weight: 300;
    min-width: 0;
  }
  input::placeholder { color: rgba(255, 255, 255, 0.3); font-size: 18px; }
</style>
