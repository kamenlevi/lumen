<script lang="ts">
  import { tick, onMount } from "svelte";
  import { api, type Chat, type ChatMessage, type SearchResult } from "$lib/ipc";
  import ResultGrid from "$lib/components/ResultGrid.svelte";
  import { focusTick } from "$lib/shell";

  let inputEl: HTMLTextAreaElement | null = $state(null);
  function focusInput() {
    inputEl?.focus();
  }
  onMount(focusInput);
  // Refocus when a tab switch / Ctrl+Space asks for it.
  $effect(() => {
    $focusTick;
    setTimeout(focusInput, 30);
  });

  let chats = $state<Chat[]>([]);
  let activeId = $state<number | null>(null);
  let title = $state("New chat");
  let messages = $state<ChatMessage[]>([]);
  // Which assistant message currently drives the results gallery. null = follow
  // the latest assistant message that has results.
  let pinnedMsgId = $state<number | null>(null);
  let input = $state("");
  let sending = $state(false);
  let drawerOpen = $state(false);
  let err = $state<string | null>(null);
  let scroller: HTMLDivElement | null = $state(null);

  // The result set shown on the right: the pinned message's, else the most
  // recent assistant message that returned photos.
  let galleryResults = $derived.by((): SearchResult[] => {
    if (pinnedMsgId !== null) {
      const m = messages.find((x) => x.id === pinnedMsgId);
      if (m) return m.results;
    }
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "assistant" && messages[i].results.length) {
        return messages[i].results;
      }
    }
    return [];
  });

  async function loadChats() {
    try {
      chats = await api.listChats();
    } catch (e) {
      err = (e as Error).message;
    }
  }

  async function openChat(id: number) {
    try {
      const { chat, messages: msgs } = await api.getChat(id);
      activeId = chat.id;
      title = chat.title;
      messages = msgs;
      pinnedMsgId = null;
      drawerOpen = false;
      err = null;
      await scrollToBottom();
    } catch (e) {
      err = (e as Error).message;
    }
  }

  function newChat() {
    // Created lazily on first send, so we don't litter empty chats.
    activeId = null;
    title = "New chat";
    messages = [];
    pinnedMsgId = null;
    drawerOpen = false;
    input = "";
  }

  async function send() {
    const text = input.trim();
    if (!text || sending) return;
    sending = true;
    err = null;
    try {
      if (activeId === null) {
        const c = await api.createChat();
        activeId = c.id;
      }
      // optimistic user bubble
      const optimistic: ChatMessage = {
        id: -Date.now(), role: "user", content: text, created_at: 0, results: [],
      };
      messages = [...messages, optimistic];
      input = "";
      await scrollToBottom();

      const res = await api.sendMessage(activeId, text);
      // swap optimistic for the real pair, follow the newest results
      messages = [...messages.filter((m) => m.id !== optimistic.id), res.user, res.assistant];
      title = res.title;
      pinnedMsgId = null;
      await loadChats();
      await scrollToBottom();
    } catch (e) {
      err = (e as Error).message;
    } finally {
      sending = false;
    }
  }

  async function removeChat(id: number) {
    try {
      await api.deleteChat(id);
      if (id === activeId) newChat();
      await loadChats();
    } catch (e) {
      err = (e as Error).message;
    }
  }

  async function renameActive() {
    if (activeId === null) return;
    const next = prompt("Rename chat", title);
    if (next && next.trim()) {
      try {
        const c = await api.renameChat(activeId, next.trim());
        title = c.title;
        await loadChats();
      } catch (e) {
        err = (e as Error).message;
      }
    }
  }

  async function scrollToBottom() {
    await tick();
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  // Is this the assistant message currently driving the gallery?
  function _isShown(m: ChatMessage): boolean {
    if (pinnedMsgId !== null) return m.id === pinnedMsgId;
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "assistant" && messages[i].results.length) {
        return messages[i].id === m.id;
      }
    }
    return false;
  }

  $effect(() => {
    loadChats();
  });
</script>

<div class="relative flex h-full">
  <!-- LEFT: conversation -->
  <section class="flex h-full w-[42%] min-w-[22rem] flex-col border-r border-neutral-800">
    <header class="flex items-center gap-2 border-b border-neutral-800 bg-neutral-900 px-3 py-2">
      <button
        type="button"
        title="History"
        on:click={() => (drawerOpen = !drawerOpen)}
        class="rounded px-2 py-1 text-neutral-300 hover:bg-neutral-800">☰</button>
      <button
        type="button"
        on:click={renameActive}
        class="flex-1 truncate text-left text-sm font-medium text-neutral-200 hover:text-white"
        title="Click to rename">{title}</button>
      <button
        type="button"
        on:click={newChat}
        class="rounded bg-neutral-800 px-2 py-1 text-xs text-neutral-200 hover:bg-neutral-700">+ New</button>
    </header>

    {#if err}
      <div class="border-b border-red-900/60 bg-red-950/40 px-4 py-2 text-sm text-red-300">{err}</div>
    {/if}

    <div bind:this={scroller} class="min-h-0 flex-1 space-y-3 overflow-auto p-4">
      {#if messages.length === 0}
        <div class="mt-10 text-center text-sm text-neutral-500">
          <p class="mb-1 text-neutral-400">Ask about your photos.</p>
          <p>“sunset over water” · “are all my portraits in focus?” · “screenshots of code”</p>
        </div>
      {/if}

      {#each messages as m (m.id)}
        {#if m.role === "user"}
          <div class="flex justify-end">
            <div class="max-w-[85%] rounded-2xl rounded-br-sm bg-indigo-600 px-3 py-2 text-sm text-white">
              {m.content}
            </div>
          </div>
        {:else}
          <div class="flex justify-start">
            <div class="max-w-[90%] space-y-1">
              <div class="rounded-2xl rounded-bl-sm bg-neutral-800 px-3 py-2 text-sm text-neutral-100">
                {m.content}
              </div>
              {#if m.results.length}
                <button
                  type="button"
                  on:click={() => (pinnedMsgId = m.id)}
                  class="ml-1 text-xs {(_isShown(m)) ? 'text-indigo-300' : 'text-neutral-500 hover:text-neutral-300'}">
                  {m.results.length} photos {(_isShown(m)) ? "· shown →" : "· show →"}
                </button>
              {/if}
            </div>
          </div>
        {/if}
      {/each}
    </div>

    <form
      class="flex items-end gap-2 border-t border-neutral-800 bg-neutral-900 p-3"
      on:submit|preventDefault={send}>
      <textarea
        bind:this={inputEl}
        bind:value={input}
        on:keydown={onKey}
        rows="1"
        placeholder="Ask about your photos…"
        class="max-h-32 min-h-[2.5rem] flex-1 resize-none rounded-lg border border-neutral-700 bg-neutral-950 px-3 py-2 text-sm placeholder:text-neutral-600 focus:border-neutral-500 focus:outline-none"
      ></textarea>
      <button
        type="submit"
        disabled={sending || !input.trim()}
        class="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50">
        {sending ? "…" : "Send"}
      </button>
    </form>
  </section>

  <!-- RIGHT: results gallery -->
  <section class="flex h-full flex-1 flex-col bg-neutral-950">
    <header class="flex items-center justify-between border-b border-neutral-800 bg-neutral-900 px-4 py-2 text-sm">
      <span class="font-medium text-neutral-200">Results</span>
      <span class="text-neutral-500">{galleryResults.length} photos</span>
    </header>
    <div class="min-h-0 flex-1 overflow-auto">
      {#if galleryResults.length}
        <ResultGrid results={galleryResults} />
      {:else}
        <div class="flex h-full items-center justify-center p-8 text-center text-sm text-neutral-600">
          Photos Lumen finds will appear here.
        </div>
      {/if}
    </div>
  </section>

  <!-- HISTORY DRAWER -->
  {#if drawerOpen}
    <button
      type="button"
      aria-label="Close history"
      class="absolute inset-0 z-10 cursor-default bg-black/40"
      on:click={() => (drawerOpen = false)}></button>
    <aside class="absolute inset-y-0 left-0 z-20 flex w-72 flex-col border-r border-neutral-800 bg-neutral-900 shadow-xl">
      <div class="flex items-center justify-between border-b border-neutral-800 px-3 py-2">
        <span class="text-sm font-medium text-neutral-200">History</span>
        <button
          type="button"
          on:click={() => { newChat(); }}
          class="rounded bg-neutral-800 px-2 py-1 text-xs text-neutral-200 hover:bg-neutral-700">+ New</button>
      </div>
      <div class="min-h-0 flex-1 overflow-auto p-2">
        {#if chats.length === 0}
          <p class="p-3 text-xs text-neutral-500">No conversations yet.</p>
        {/if}
        {#each chats as c (c.id)}
          <div
            class="group flex items-center gap-1 rounded px-2 py-2 {c.id === activeId ? 'bg-neutral-800' : 'hover:bg-neutral-800/60'}">
            <button
              type="button"
              on:click={() => openChat(c.id)}
              class="min-w-0 flex-1 text-left">
              <div class="truncate text-sm text-neutral-200">{c.title}</div>
              <div class="text-[11px] text-neutral-500">{c.message_count ?? 0} messages</div>
            </button>
            <button
              type="button"
              title="Delete"
              on:click={() => removeChat(c.id)}
              class="rounded px-1.5 py-1 text-xs text-neutral-500 opacity-0 hover:text-red-400 group-hover:opacity-100">✕</button>
          </div>
        {/each}
      </div>
    </aside>
  {/if}
</div>
