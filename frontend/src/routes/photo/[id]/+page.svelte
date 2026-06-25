<script lang="ts">
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

  // zoom / pan state
  let zoom = $state(1);
  let panX = $state(0);
  let panY = $state(0);
  let dragging = false;
  let dragStart = { x: 0, y: 0, px: 0, py: 0 };

  function resetView() {
    zoom = 1; panX = 0; panY = 0;
  }

  $effect(() => {
    const id = Number($page.params.id);
    if (!id) return;
    photo = null;
    similar = [];
    resetView();
    api.photo(id).then((p) => (photo = p)).catch((e) => (err = e.message));
    api.photoSimilar(id, 24).then((r) => (similar = r.results)).catch((e) => (err = e.message));
    api.photoNeighbors(id).then((n) => (nav = n)).catch(() => {});
  });

  function go(id: number | null) {
    if (id != null) goto(`/photo/${id}/`);
  }

  function onWheel(e: WheelEvent) {
    e.preventDefault();
    const next = Math.min(6, Math.max(1, zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
    if (next === 1) resetView();
    zoom = next;
  }
  function onDblClick() {
    if (zoom > 1) resetView();
    else zoom = 2.5;
  }
  function onPointerDown(e: PointerEvent) {
    if (zoom <= 1) return;
    dragging = true;
    dragStart = { x: e.clientX, y: e.clientY, px: panX, py: panY };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  }
  function onPointerMove(e: PointerEvent) {
    if (!dragging) return;
    panX = dragStart.px + (e.clientX - dragStart.x);
    panY = dragStart.py + (e.clientY - dragStart.y);
  }
  function onPointerUp() { dragging = false; }

  function onKey(e: KeyboardEvent) {
    if (e.key === "ArrowRight") { e.preventDefault(); go(nav.next); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); go(nav.prev); }
    else if (e.key === "+" || e.key === "=") { zoom = Math.min(6, zoom * 1.25); }
    else if (e.key === "-") { const z = zoom / 1.25; if (z <= 1) resetView(); else zoom = z; }
    else if (e.key === "0" || e.key === "Escape") { resetView(); }
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
        class="relative flex h-[70vh] items-center justify-center overflow-hidden rounded-md bg-black ring-1 ring-neutral-800"
        role="presentation"
        onwheel={onWheel}
        ondblclick={onDblClick}
        onpointerdown={onPointerDown}
        onpointermove={onPointerMove}
        onpointerup={onPointerUp}>
        <img
          src={api.photoFileUrl(photo.id)}
          alt={photo.path}
          draggable="false"
          class="max-h-full max-w-full select-none object-contain {zoom > 1 ? (dragging ? 'cursor-grabbing' : 'cursor-grab') : 'cursor-zoom-in'}"
          style="transform: translate({panX}px, {panY}px) scale({zoom}); transition: {dragging ? 'none' : 'transform 80ms ease-out'};" />

        {#if nav.prev != null}
          <button onclick={() => go(nav.prev)} aria-label="Previous"
            class="absolute left-2 top-1/2 -translate-y-1/2 rounded-full bg-black/55 px-3 py-2 text-lg text-white hover:bg-black/80">‹</button>
        {/if}
        {#if nav.next != null}
          <button onclick={() => go(nav.next)} aria-label="Next"
            class="absolute right-2 top-1/2 -translate-y-1/2 rounded-full bg-black/55 px-3 py-2 text-lg text-white hover:bg-black/80">›</button>
        {/if}

        <div class="pointer-events-none absolute bottom-2 left-1/2 -translate-x-1/2 rounded bg-black/60 px-2 py-0.5 text-[11px] text-neutral-300">
          {#if nav.index}{nav.index} / {nav.total} · {/if}
          {Math.round(zoom * 100)}%
          {#if zoom > 1}<button class="pointer-events-auto ml-1 underline" onclick={resetView}>reset</button>{/if}
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
          <div>
            <dt class="text-xs uppercase tracking-wide text-neutral-500">pHash</dt>
            <dd class="font-mono text-xs">{photo.phash ?? "—"}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase tracking-wide text-neutral-500">Indexed</dt>
            <dd>{new Date(photo.indexed_at * 1000).toLocaleString()}</dd>
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
