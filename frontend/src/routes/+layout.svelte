<script lang="ts">
  import "../app.css";
  import { onMount } from "svelte";
  import { goto } from "$app/navigation";
  import { page } from "$app/stores";
  import { mode, applyMode, hideWindow, minimizeWindow } from "$lib/shell";
  import CompactBar from "$lib/components/CompactBar.svelte";

  let { children } = $props();

  const tabs = [
    { href: "/chat/", label: "Chat" },
    { href: "/search/", label: "Search" },
    { href: "/library/", label: "Library" },
    { href: "/models/", label: "Models" },
    { href: "/settings/", label: "Settings" },
  ];

  onMount(async () => {
    await applyMode("compact"); // boot as the compact bar
    try {
      const { listen } = await import("@tauri-apps/api/event");
      await listen("ui://spotlight", () => applyMode("compact"));
      await listen("ui://expand", () => applyMode("expanded"));
      await listen("navigate", (e) => { applyMode("expanded"); goto(String(e.payload)); });
    } catch { /* not in Tauri */ }
  });
</script>

{#if $mode === "compact"}
  <CompactBar />
{:else}
  <div class="flex h-full flex-col">
    <header
      data-tauri-drag-region
      class="flex select-none items-center gap-4 border-b border-neutral-800 bg-neutral-900 px-3 py-2">
      <button
        type="button"
        title="Search (compact)"
        onclick={() => applyMode("compact")}
        class="rounded px-2 py-1 text-neutral-300 hover:bg-neutral-800">⌕</button>
      <div class="text-sm font-semibold tracking-wide text-neutral-200">Lumen</div>
      <nav class="flex gap-1 text-sm">
        {#each tabs as tab}
          {@const active = $page.url.pathname.startsWith(tab.href)}
          <a
            href={tab.href}
            class="rounded px-3 py-1 transition-colors {active
              ? 'bg-neutral-800 text-white'
              : 'text-neutral-400 hover:bg-neutral-800 hover:text-neutral-100'}">
            {tab.label}
          </a>
        {/each}
      </nav>
      <div class="ml-auto flex items-center gap-1">
        <button type="button" title="Minimize" onclick={minimizeWindow}
          class="rounded px-2 py-1 text-neutral-400 hover:bg-neutral-800">–</button>
        <button type="button" title="Close" onclick={hideWindow}
          class="rounded px-2 py-1 text-neutral-400 hover:bg-red-900/60 hover:text-red-200">✕</button>
      </div>
    </header>
    <main class="min-h-0 flex-1 overflow-auto">
      {@render children()}
    </main>
  </div>
{/if}
