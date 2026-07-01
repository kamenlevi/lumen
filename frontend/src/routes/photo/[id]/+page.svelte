<script lang="ts">
  import { onDestroy } from "svelte";
  import { goto } from "$app/navigation";
  import { page } from "$app/stores";
  import { api, type PhotoDetail, type SearchResult } from "$lib/ipc";
  import ResultGrid from "$lib/components/ResultGrid.svelte";

  let photo = $state<PhotoDetail | null>(null);
  let similar = $state<SearchResult[]>([]);
  let err = $state<string | null>(null);
  let nav = $state<{ prev: number | null; next: number | null; prev2: number | null; next2: number | null; index: number | null; total: number }>(
    { prev: null, next: null, prev2: null, next2: null, index: null, total: 0 }
  );

  // ojo-style zoom: fit-to-window ⇄ 100% (of the preview). Pan by dragging.
  let mode = $state<"fit" | "actual">("fit");
  let container: HTMLDivElement | null = $state(null);
  let loaded = $state(false);
  let dragging = $state(false); // read in the template (grab cursor)
  let moved = false;
  let down = { x: 0, y: 0, sl: 0, st: 0 };
  let similarTimer: ReturnType<typeof setTimeout> | null = null;

  // Click-to-copy for the fingerprint hashes.
  let hashCopied = $state(false);
  let copyTimer: ReturnType<typeof setTimeout> | null = null;
  async function copyHash(value: string | null | undefined) {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      hashCopied = true;
      if (copyTimer) clearTimeout(copyTimer);
      copyTimer = setTimeout(() => (hashCopied = false), 1500);
    } catch { /* clipboard blocked — ignore */ }
  }

  // Tier-3 VLM "describe this photo" — opt-in, cached, async (slow on CPU).
  let vlmAvail = $state<boolean | null>(null);
  let card = $state<{ status: string; description?: string; error?: string }>({ status: "none" });
  let cardPoll: ReturnType<typeof setInterval> | null = null;

  function clearPoll() {
    if (cardPoll) { clearInterval(cardPoll); cardPoll = null; }
  }
  function startPoll(id: number) {
    clearPoll();
    cardPoll = setInterval(async () => {
      const c = await api.photoCard(id);
      card = c;
      if (c.status === "done" || c.status === "error") clearPoll();
    }, 3000);
  }
  async function loadCard(id: number) {
    clearPoll();
    if (vlmAvail === null) {
      try { vlmAvail = (await api.vlmStatus()).available; } catch { vlmAvail = false; }
    }
    try {
      card = await api.photoCard(id); // shows a cached description instantly
      if (card.status === "generating") startPoll(id);
    } catch { card = { status: "none" }; }
  }
  async function describe(force = false) {
    if (!photo) return;
    try {
      card = await api.photoDescribe(photo.id, force);
      if (card.status === "generating") startPoll(photo.id);
    } catch (e) {
      card = { status: "error", error: (e as Error).message };
    }
  }

  function preload(id: number | null) {
    if (id != null) {
      const im = new Image();
      im.src = api.photoPreviewUrl(id); // small + cached → instant arrow nav
    }
  }

  $effect(() => {
    const id = Number($page.params.id);
    if (!id) return;
    photo = null;
    mode = "fit";
    loaded = false;
    card = { status: "none" };
    loadCard(id);
    api.photo(id).then((p) => (photo = p)).catch((e) => (err = e.message));
    api.photoNeighbors(id).then((n) => {
      nav = n;
      preload(n.prev); preload(n.next); preload(n.prev2); preload(n.next2);
    }).catch(() => {});
    // The "similar" search is a CLIP query — defer it so rapid arrowing
    // doesn't fire one per image. Only runs if you linger ~0.4s.
    if (similarTimer) clearTimeout(similarTimer);
    similar = [];
    similarTimer = setTimeout(() => {
      api.photoSimilar(id, 24).then((r) => (similar = r.results)).catch(() => {});
    }, 400);
  });

  function go(id: number | null) {
    if (id != null) goto(`/photo/${id}/`);
  }

  function toggleZoom(e: { clientX: number; clientY: number }) {
    if (mode === "fit") {
      mode = "actual";
      queueMicrotask(() => {
        if (!container) return;
        const r = container.getBoundingClientRect();
        const inner = container.scrollWidth, innerH = container.scrollHeight;
        const fx = (e.clientX - r.left) / r.width;
        const fy = (e.clientY - r.top) / r.height;
        container.scrollLeft = fx * (inner - r.width);
        container.scrollTop = fy * (innerH - r.height);
      });
    } else {
      mode = "fit";
    }
  }

  function onPointerDown(e: PointerEvent) {
    moved = false;
    down = { x: e.clientX, y: e.clientY, sl: container?.scrollLeft ?? 0, st: container?.scrollTop ?? 0 };
    if (mode === "actual") {
      dragging = true;
      (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    }
  }
  function onPointerMove(e: PointerEvent) {
    if (Math.abs(e.clientX - down.x) > 4 || Math.abs(e.clientY - down.y) > 4) moved = true;
    if (dragging && container) {
      container.scrollLeft = down.sl - (e.clientX - down.x);
      container.scrollTop = down.st - (e.clientY - down.y);
    }
  }
  function onPointerUp(e: PointerEvent) {
    dragging = false;
    if (!moved) toggleZoom(e); // a click (not a drag) toggles zoom
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

  onDestroy(clearPoll);
</script>

<svelte:window onkeydown={onKey} />

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
        onpointerdown={onPointerDown}
        onpointermove={onPointerMove}
        onpointerup={onPointerUp}>
        <img
          src={api.photoThumbUrl(photo.id)}
          alt=""
          class="pointer-events-none absolute inset-0 m-auto max-h-full max-w-full object-contain blur-[1px] {loaded ? 'opacity-0' : 'opacity-100'}" />
        <img
          src={api.photoPreviewUrl(photo.id)}
          alt={photo.path}
          draggable="false"
          onload={() => (loaded = true)}
          class="full select-none {mode === 'fit'
            ? 'max-h-full max-w-full object-contain cursor-zoom-in'
            : (dragging ? 'cursor-grabbing' : 'cursor-grab') + ' max-w-none'} {loaded ? 'opacity-100' : 'opacity-0'}" />

        {#if nav.prev != null}
          <button onpointerdown={(e) => e.stopPropagation()} onclick={() => go(nav.prev)} aria-label="Previous"
            class="absolute left-2 top-1/2 z-10 -translate-y-1/2 rounded-full bg-black/55 px-3 py-2 text-lg text-white hover:bg-black/80">‹</button>
        {/if}
        {#if nav.next != null}
          <button onpointerdown={(e) => e.stopPropagation()} onclick={() => go(nav.next)} aria-label="Next"
            class="absolute right-2 top-1/2 z-10 -translate-y-1/2 rounded-full bg-black/55 px-3 py-2 text-lg text-white hover:bg-black/80">›</button>
        {/if}
        <div class="pointer-events-none absolute bottom-2 left-1/2 z-10 -translate-x-1/2 rounded bg-black/60 px-2 py-0.5 text-[11px] text-neutral-300">
          {#if nav.index}{nav.index} / {nav.total} · {/if}{mode === "fit" ? "fit — click to zoom · ← →" : "100% — click to fit"}
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

        <!-- Fingerprint: hashes Lumen uses to track this file -->
        <div class="mt-1 rounded-lg border border-neutral-800 bg-neutral-900 p-3">
          <div class="mb-1 text-xs font-semibold uppercase tracking-wide text-neutral-400">Fingerprint</div>
          <div class="space-y-1.5">
            <div>
              <dt class="text-[11px] uppercase tracking-wide text-neutral-500">SHA-256 · exact content</dt>
              <dd class="break-all font-mono text-[11px]">
                <button
                  type="button"
                  class="break-all text-left font-mono text-neutral-300 hover:text-indigo-300"
                  title="Click to copy"
                  onclick={() => copyHash(photo?.sha256)}>
                  {photo.sha256 ?? "— (re-index to compute)"}
                </button>
              </dd>
            </div>
            <div>
              <dt class="text-[11px] uppercase tracking-wide text-neutral-500">pHash · perceptual / near-duplicate</dt>
              <dd class="break-all font-mono text-[11px]">
                <button
                  type="button"
                  class="break-all text-left font-mono text-neutral-300 hover:text-indigo-300"
                  title="Click to copy"
                  onclick={() => copyHash(photo?.phash)}>
                  {photo.phash ?? "—"}
                </button>
              </dd>
            </div>
          </div>
          {#if hashCopied}<div class="mt-1 text-[11px] text-emerald-400">Copied.</div>{/if}
        </div>

        <!-- AI description (Tier-3 VLM) — opt-in, cached -->
        <div class="mt-2 rounded-lg border border-neutral-800 bg-neutral-900 p-3">
          <div class="mb-1 flex items-center justify-between">
            <span class="text-xs font-semibold uppercase tracking-wide text-indigo-300">AI description</span>
            {#if card.status === "done"}
              <button class="text-[11px] text-neutral-500 hover:text-neutral-300" onclick={() => describe(true)}>re-analyze</button>
            {/if}
          </div>
          {#if vlmAvail === false}
            <p class="text-xs text-neutral-500">No vision model selected. Pick one in the <a href="/models/" class="text-indigo-400 underline">Models</a> tab to enable this.</p>
          {:else if card.status === "done"}
            <p class="text-sm text-neutral-200">{card.description}</p>
          {:else if card.status === "generating"}
            <p class="flex items-center gap-2 text-sm text-neutral-400">
              <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-indigo-500"></span>
              Analyzing… (local model can take a minute or two on CPU)
            </p>
          {:else if card.status === "error"}
            <p class="text-xs text-red-400">{card.error}</p>
            <button class="mt-1 text-xs text-indigo-400 underline" onclick={() => describe()}>try again</button>
          {:else}
            <button
              class="rounded bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500"
              onclick={() => describe()}>
              ✨ Describe this photo
            </button>
          {/if}
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
