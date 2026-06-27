<script lang="ts">
  import { onMount } from "svelte";
  import { goto } from "$app/navigation";
  import { api, type DuplicateGroup } from "$lib/ipc";

  let groups = $state<DuplicateGroup[]>([]);
  let extra = $state(0);
  let loading = $state(true);
  let err = $state<string | null>(null);
  let busy = $state<number | null>(null); // id currently being deleted

  async function load() {
    loading = true;
    err = null;
    try {
      const r = await api.duplicates();
      groups = r.groups;
      extra = r.extra_copies;
    } catch (e) {
      err = (e as Error).message;
    } finally {
      loading = false;
    }
  }

  // The newest copy (first in each group) is the one to keep; the rest are
  // redundant. Trashing a redundant copy is safe — the keeper still exists.
  async function trash(groupIdx: number, id: number) {
    busy = id;
    err = null;
    try {
      await api.deletePhoto(id, true);
      // Drop it locally; if a group falls to a single copy it's no longer a dup.
      const g = groups[groupIdx];
      g.copies = g.copies.filter((c) => c.id !== id);
      g.count = g.copies.length;
      groups = groups.filter((grp) => grp.count > 1);
      extra = groups.reduce((n, grp) => n + grp.count - 1, 0);
    } catch (e) {
      err = (e as Error).message;
    } finally {
      busy = null;
    }
  }

  function fname(p: string): string {
    return p.split("/").pop() ?? p;
  }

  onMount(load);
</script>

<section class="space-y-4 p-4">
  <header class="flex flex-wrap items-center gap-2">
    <h1 class="text-base font-semibold">Exact duplicates</h1>
    <span class="text-xs text-neutral-500">byte-identical files (matched by SHA-256)</span>
    <div class="flex-1"></div>
    <button
      type="button"
      onclick={load}
      class="rounded bg-neutral-800 px-3 py-1.5 text-sm hover:bg-neutral-700">
      Rescan
    </button>
  </header>

  {#if err}
    <div class="rounded border border-red-900/60 bg-red-950/40 p-2 text-sm text-red-300">{err}</div>
  {/if}

  {#if loading}
    <p class="text-sm text-neutral-500">Scanning…</p>
  {:else if groups.length === 0}
    <p class="text-sm text-neutral-500">
      No exact duplicates — every indexed photo has unique file content. (If you
      expected some, make sure the folders are indexed; older photos get a
      content hash on the next re-index.)
    </p>
  {:else}
    <p class="text-sm text-neutral-400">
      {groups.length} duplicate set{groups.length === 1 ? "" : "s"} ·
      <span class="text-amber-300">{extra} redundant cop{extra === 1 ? "y" : "ies"}</span>
      you can clear out. Deleting moves the file to your system Trash — it's recoverable.
    </p>

    <ul class="space-y-3">
      {#each groups as g, gi (g.sha256)}
        <li class="rounded-lg border border-neutral-800 bg-neutral-900 p-3">
          <div class="mb-2 flex items-center gap-2 text-xs text-neutral-500">
            <span class="rounded bg-neutral-800 px-1.5 py-0.5 font-mono">{g.sha256?.slice(0, 16)}…</span>
            <span>{g.count} identical copies</span>
          </div>
          <div class="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
            {#each g.copies as c, ci (c.id)}
              <div class="relative overflow-hidden rounded-md border {ci === 0 ? 'border-amber-400' : 'border-neutral-800'}">
                <button
                  type="button"
                  onclick={() => goto(`/photo/${c.id}/`)}
                  class="block aspect-square w-full bg-neutral-950">
                  <img src={api.photoThumbUrl(c.id)} alt={c.path} loading="lazy" class="h-full w-full object-cover" />
                </button>
                {#if ci === 0}
                  <span class="absolute left-1 top-1 rounded bg-amber-400 px-1.5 py-0.5 text-[10px] font-bold text-black">★ keep</span>
                {:else}
                  <button
                    type="button"
                    disabled={busy === c.id}
                    onclick={() => trash(gi, c.id)}
                    class="absolute right-1 top-1 rounded bg-red-600/90 px-1.5 py-0.5 text-[10px] font-medium text-white hover:bg-red-500 disabled:opacity-50">
                    {busy === c.id ? "…" : "Trash"}
                  </button>
                {/if}
                <span class="block truncate px-1.5 py-1 text-[11px] text-neutral-400" title={c.path}>{fname(c.path)}</span>
              </div>
            {/each}
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</section>
