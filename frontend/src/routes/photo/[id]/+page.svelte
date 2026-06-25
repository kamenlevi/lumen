<script lang="ts">
  import { tick } from "svelte";
  import { goto } from "$app/navigation";
  import { page } from "$app/stores";
  import { api, type PhotoDetail, type SearchResult } from "$lib/ipc";
  import ResultGrid from "$lib/components/ResultGrid.svelte";

  let photo = $state<PhotoDetail | null>(null);
  let similar = $state<SearchResult[]>([]);
  let err = $state<string | null>(null);
  let nav = $state<{ prev: number | null; next: number | null; index: number | null; total: number }>(
    { prev: null, next: null, index: null, total: 0 }
  );

  // ojo-style zoom: just two states — fit-to-window and 100% (actual pixels).
  // No laggy continuous scaling. Pan by dragging when at 100%.
  let mode = $state<"fit" | "actual">("fit");
  let container: HTMLDivElement | null = $state(null);
  let loaded = $state(false);
  let dragging = false;
  let drag = { x: 0, y: 0, sl: 0, st: 0 };

  function preload(id: number | null) {
    if (id != null) {
      const im = new Image();
      im.src = api.photoFileUrl(id);
    }
  }

  $effect(() => {
    const id = Number($page.params.id);
    if (!id) return;
    photo = null;
    similar = [];
    mode = "fit";
    loaded = false;
    api.photo(id).then((p) => (photo = p)).catch((e) => (err = e.message));
    api.photoSimilar(id, 24).then((r) => (similar = r.results)).catch(() => {});
    api.photoNeighbors(id).then((n) => {
      nav = n;
      preload(n.prev); // neighbours preloaded → arrow nav is instant
      preload(n.next);
    }).catch(() => {});
  });

  function go(id: number | null) {
    if (id != null) goto(`/photo/${id}/`);
  }

  async function toggleZoom(e: MouseEvent) {
    if (dragging) return;
    if (mode === "fit") {
      mode = "actual";
      await tick();
      if (container) {
        // center the 100% view on where the user clicked
        const img = container.querySelector("img.full") as HTMLImageElement | null;
        if (img) {
          const r = container.getBoundingClientRect();
          const fx = (e.clientX - r.left) / r.width;
          const fy = (e.clientY - r.top) / r.height;
          container.scrollLeft = fx * (img.scrollWidth - r.width);
          container.scrollTop = fy * (img.scrollHeight - r.height);
        }
      }
    } else {
      mode = "fit";
    }
  }

  function onPointerDown(e: PointerEvent) {
    if (mode !== "actual" || !container) return;
    dragging = true;
    drag = { x: e.clientX, y: e.clientY, sl: container.scrollLeft, st: container.scrollTop };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  }
  function onPointerMove(e: PointerEvent) {
    if (!dragging || !container) return;
    container.scrollLeft = drag.sl - (e.clientX - drag.x);
    container.scrollTop = drag.st - (e.clientY - drag.y);
  }
  function onPointerUp() {
    // small delay so the click that ends a drag doesn't also toggle zoom
    setTimeout(() => (dragging = false), 0);
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === "ArrowRight") { e.preventDefault(); go(nav.next); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); go(nav.prev); }
    else if (e.key === " ") { e.preventDefault(); mode = mode === "fit" ? "actual" : "fit"; }
    else if (e.key === "Escape") { mode = "fit"; }
  }

  function fmtCoord(lat: number | null, lon: number | null) {
    if (lat == null || lon == null) return "—";
    return `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  }
</script>

<svelte:window on:keydown={onKey} />

<section class="space-y-4 p-4">
  {#if err}
    <div class="rounded border border-red-900/60 bg-red-950/40 p-2 text-sm text-red-300">{err}</div>
  {/if}

  {#if photo}
    <div class="grid gap-4 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
      <div
        bind:this={container}
        class="relative h-[72vh] rounded-md bg-black ring-1 ring-neutral-800 {mode === 'actual' ? 'overflow-auto' : 'overflow-hidden flex items-center justify-center'}"
        role="presentation"
        onclick={toggleZoom}
        onpointerdown={onPointerDown}
        onpointermove={onPointerMove}
        onpointerup={onPointerUp}>
        <!-- instant thumbnail placeholder under the full image -->
        <img
          src={api.photoThumbUrl(photo.id)}
          alt=""
          class="pointer-events-none absolute inset-0 m-auto max-h-full max-w-full object-contain blur-[1px] transition-opacity {loaded ? 'opacity-0' : 'opacity-100'}" />
        <img
          src={api.photoFileUrl(photo.id)}
          alt={photo.path}
          draggable="false"
          onload={() => (loaded = true)}
          class="full select-none {mode === 'fit'
            ? 'max-h-full max-w-full object-contain cursor-zoom-in'
            : (dragging ? 'cursor-grabbing' : 'cursor-grab') + ' max-w-none'} {loaded ? 'opacity-100' : 'opacity-0'}" />

        {#if nav.prev != null}
          <button onclick={(e) => { e.stopPropagation(); go(nav.prev); }} aria-label="Previous"
            class="absolute left-2 top-1/2 z-10 -translate-y-1/2 rounded-full bg-black/55 px-3 py-2 text-lg text-white hover:bg-black/80">‹</button>
        {/if}
        {#if nav.next != null}
          <button onclick={(e) => { e.stopPropagation(); go(nav.next); }} aria-label="Next"
            class="absolute right-2 top-1/2 z-10 -translate-y-1/2 rounded-full bg-black/55 px-3 py-2 text-lg text-white hover:bg-black/80">›</button>
        {/if}
        <div class="pointer-events-none absolute bottom-2 left-1/2 z-10 -translate-x-1/2 rounded bg-black/60 px-2 py-0.5 text-[11px] text-neutral-300">
          {#if nav.index}{nav.index} / {nav.total} · {/if}{mode === "fit" ? "fit — click to zoom" : "100% — click to fit"}
        </div>
      </div>

      <dl class="space-y-2 text-sm">
        <div>
          <dt class="text-xs uppercase tracking-wide text-neutral-500">Path</dt>
          <dd class="break-all font-mono text-xs">{photo.path}</dd>
        </div>
        <div class="grid grid-cols-2 gap-2">
          <div>
            <dt class="text-xs uppercase tracking-wide text-neutral-500">Dimensions</dt>
            <dd>{photo.w ?? "?"} × {photo.h ?? "?"}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase tracking-wide text-neutral-500">Taken</dt>
            <dd>{photo.taken_at ?? "—"}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase tracking-wide text-neutral-500">Camera</dt>
            <dd>{photo.camera ?? "—"}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase tracking-wide text-neutral-500">GPS</dt>
            <dd class="font-mono text-xs">{fmtCoord(photo.lat, photo.lon)}</dd>
          </div>
        </div>
      </dl>
    </div>

    <section>
      <h2 class="px-3 pt-2 text-xs uppercase tracking-wide text-neutral-500">Similar photos</h2>
      <ResultGrid results={similar} showScore={false} />
    </section>
  {:else}
    <div class="p-8 text-center text-sm text-neutral-500">Loading…</div>
  {/if}
</section>
