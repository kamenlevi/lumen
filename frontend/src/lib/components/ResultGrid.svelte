<script lang="ts">
  import { goto } from "$app/navigation";
  import { api, type SearchResult } from "$lib/ipc";

  let { results, showScore = true }: { results: SearchResult[]; showScore?: boolean } = $props();

  function open(r: SearchResult) {
    goto(`/photo/${r.id}/`);
  }

  // A small, honest quality badge so results can be verified at a glance.
  function quality(r: SearchResult): { text: string; cls: string } | null {
    if (r.is_blurry === 1) return { text: "blurry", cls: "bg-red-600/85" };
    if (r.subject_out_of_focus === 1) return { text: "soft subject", cls: "bg-orange-600/85" };
    if (r.is_dark === 1) return { text: "dark", cls: "bg-blue-700/85" };
    if (r.is_bright === 1) return { text: "bright", cls: "bg-amber-500/85" };
    if (r.sharpness != null) return { text: "sharp", cls: "bg-emerald-700/85" };
    return null;
  }
</script>

{#if results.length === 0}
  <div class="p-8 text-center text-sm text-neutral-500">No matches.</div>
{:else}
  <div class="grid grid-cols-2 gap-2 p-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
    {#each results as r (r.id)}
      <button
        type="button"
        on:click={() => open(r)}
        class="group relative aspect-square overflow-hidden rounded-md bg-neutral-900 ring-1 ring-neutral-800 hover:ring-neutral-600">
        <img
          src={api.photoThumbUrl(r.id)}
          alt={r.path}
          loading="lazy"
          class="h-full w-full object-cover transition-transform duration-200 group-hover:scale-[1.02]" />
        {#if showScore}
          <span class="absolute left-1.5 top-1.5 rounded bg-black/70 px-1.5 py-0.5 text-[10px] font-mono text-neutral-200">
            {r.score.toFixed(2)}
          </span>
        {/if}
        {#if quality(r)}
          {@const q = quality(r)}
          <span
            class="absolute right-1.5 top-1.5 rounded px-1.5 py-0.5 text-[10px] font-medium text-white {q?.cls}"
            title={r.sharpness != null ? `sharpness ${r.sharpness.toFixed(0)}` : ""}>
            {q?.text}{#if r.sharpness != null}<span class="ml-1 font-mono opacity-80">{r.sharpness.toFixed(0)}</span>{/if}
          </span>
        {/if}
        <span
          class="absolute inset-x-0 bottom-0 flex items-center gap-1.5 truncate bg-gradient-to-t from-black/80 to-transparent px-2 py-1 text-left text-[11px] text-neutral-200">
          {#if r.dominant_hex}
            <span
              class="inline-block h-2.5 w-2.5 flex-shrink-0 rounded-full ring-1 ring-white/30"
              style="background-color: {r.dominant_hex}"
              title="dominant color {r.dominant_hex}"></span>
          {/if}
          <span class="truncate">{r.path.split("/").pop()}</span>
        </span>
      </button>
    {/each}
  </div>
{/if}
