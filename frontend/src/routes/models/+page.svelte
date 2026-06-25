<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { api, type ModelsOverview, type CloudModel, type PullStatus, type BulkStatus } from "$lib/ipc";

  let ov = $state<ModelsOverview | null>(null);
  let cloud = $state<CloudModel[]>([]);
  let cloudLoaded = $state(false);
  let cloudLoading = $state(false);
  let query = $state("");
  let pull = $state<PullStatus | null>(null);
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let err = $state<string | null>(null);
  let apiKey = $state("");
  let hasKey = $state(false);
  let keySaved = $state(false);

  let bulk = $state<BulkStatus | null>(null);
  let bulkTimer: ReturnType<typeof setInterval> | null = null;

  function pollBulk() {
    if (bulkTimer) return;
    bulkTimer = setInterval(async () => {
      bulk = await api.vlmDescribeAllStatus();
      if (!bulk.running && bulkTimer) { clearInterval(bulkTimer); bulkTimer = null; }
    }, 1500);
  }
  async function describeAll() {
    try { bulk = await api.vlmDescribeAll(); pollBulk(); }
    catch (e) { err = (e as Error).message; }
  }
  async function stopDescribeAll() {
    try { bulk = await api.vlmDescribeAllStop(); } catch { /* ignore */ }
  }

  async function saveKey() {
    try {
      const r = await api.vlmSetKey(apiKey.trim());
      hasKey = r.has_key;
      keySaved = true;
      apiKey = "";
      setTimeout(() => (keySaved = false), 2000);
    } catch (e) {
      err = (e as Error).message;
    }
  }

  async function load() {
    try {
      ov = await api.modelsOverview();
    } catch (e) {
      err = (e as Error).message;
    }
  }

  function isSelected(provider: string, model: string): boolean {
    return ov?.selected.provider === provider && ov?.selected.model === model;
  }

  async function select(provider: string, model: string) {
    try {
      const sel = await api.modelSelect(provider, model);
      if (ov) ov.selected = sel;
    } catch (e) {
      err = (e as Error).message;
    }
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
      pull = await api.modelPullStatus();
      if (pull.status === "done" || pull.status === "error") {
        if (pollTimer) clearInterval(pollTimer);
        pollTimer = null;
        if (pull.status === "done") {
          const name = pull.name;
          await load();
          if (name) await select("ollama", name); // auto-select the new model
        }
      }
    }, 1000);
  }

  async function download(name: string) {
    err = null;
    try {
      await api.modelPull(name);
      pull = { name, status: "pulling", percent: 0, error: null };
      startPolling();
    } catch (e) {
      err = (e as Error).message;
    }
  }

  async function loadCloud() {
    cloudLoading = true;
    try {
      cloud = await api.modelsCloud();
      cloudLoaded = true;
    } catch (e) {
      err = (e as Error).message;
    } finally {
      cloudLoading = false;
    }
  }

  let filteredCloud = $derived(
    query.trim()
      ? cloud.filter((m) => (m.name + m.id).toLowerCase().includes(query.toLowerCase()))
      : cloud
  );

  // rough cost: a vision query ~ 1.5k tokens in (image+prompt) + 0.3k out
  function estCost(m: CloudModel): string {
    const c = (1.5 * m.in + 0.3 * m.out) / 1000;
    if (c === 0) return "free";
    if (c < 0.001) return "<$0.001";
    return "$" + c.toFixed(c < 0.01 ? 4 : 3);
  }

  onMount(async () => {
    await load();
    try { hasKey = (await api.vlmStatus()).has_key; } catch { /* ignore */ }
    try { bulk = await api.vlmDescribeAllStatus(); if (bulk.running) pollBulk(); } catch { /* ignore */ }
    // A download started on a previous visit keeps running in the sidecar —
    // pick its progress back up instead of looking like it was lost.
    const ps = await api.modelPullStatus();
    if (ps.status === "pulling") {
      pull = ps;
      startPolling();
    }
  });
  onDestroy(() => {
    if (pollTimer) clearInterval(pollTimer);
    if (bulkTimer) clearInterval(bulkTimer);
  });
</script>

<section class="mx-auto max-w-4xl space-y-6 p-5">
  <div>
    <h1 class="text-lg font-semibold text-neutral-100">Vision model</h1>
    <p class="text-sm text-neutral-400">
      Choose the model Lumen uses to look at photos for deep questions. On-device
      (Ollama) is private and free; cloud is faster and higher quality but costs per image.
    </p>
  </div>

  {#if err}
    <div class="rounded border border-red-900/60 bg-red-950/40 px-3 py-2 text-sm text-red-300">{err}</div>
  {/if}

  {#if !ov}
    <div class="text-sm text-neutral-500">Loading…</div>
  {:else}
    <!-- hardware + current selection -->
    <div class="flex flex-wrap items-center gap-x-6 gap-y-1 rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-sm">
      <span class="text-neutral-400">Your machine:</span>
      <span>{ov.hardware.cores} cores</span>
      <span>{ov.hardware.ram_total_gb ?? "?"} GB RAM</span>
      <span>{ov.hardware.gpu ?? "no GPU"}</span>
      <span class="ml-auto text-neutral-400">In use:</span>
      <span class="font-medium text-emerald-300">
        {ov.selected.model ? `${ov.selected.provider} · ${ov.selected.model}` : "none selected"}
      </span>
    </div>

    <!-- recommendation -->
    <div class="rounded-lg border border-indigo-900/50 bg-indigo-950/30 px-4 py-3 text-sm">
      <div class="font-medium text-indigo-200">Recommended: {ov.recommendation.model}</div>
      <div class="mt-0.5 text-neutral-300">{ov.recommendation.why}</div>
    </div>

    <!-- describe whole library (so chat/search can use AI descriptions) -->
    <div class="rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-sm">
      <div class="flex items-center justify-between">
        <div>
          <div class="font-medium text-neutral-200">Describe your library with AI</div>
          <div class="text-xs text-neutral-500">Runs the vision model over every photo in the background and caches a description, so chat &amp; search can find photos by what's in them. Slow on CPU — fast with a cloud model.</div>
        </div>
        {#if bulk?.running}
          <button class="rounded bg-neutral-800 px-3 py-1.5 text-xs text-neutral-200 hover:bg-neutral-700" onclick={stopDescribeAll}>Stop</button>
        {:else}
          <button class="rounded bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50" disabled={!ov.selected.model} onclick={describeAll}>Describe all</button>
        {/if}
      </div>
      {#if bulk && (bulk.running || bulk.done > 0)}
        <div class="mt-2">
          <div class="h-1.5 w-full overflow-hidden rounded bg-neutral-800">
            <div class="h-full bg-indigo-500 transition-[width]" style="width: {bulk.total ? Math.round((100 * bulk.done) / bulk.total) : 0}%"></div>
          </div>
          <div class="mt-1 text-[11px] text-neutral-500">
            {bulk.done}/{bulk.total} described{bulk.failed ? ` · ${bulk.failed} failed` : ""}{bulk.running ? " · running…" : " · done"}
          </div>
        </div>
      {/if}
      {#if bulk?.error}<p class="mt-1 text-[11px] text-red-400">{bulk.error}</p>{/if}
    </div>

    <!-- on-device -->
    <div class="space-y-2">
      <h2 class="text-sm font-semibold text-neutral-200">On-device (Ollama)</h2>
      {#if !ov.ollama_up}
        <p class="text-xs text-amber-400">Ollama isn’t running. Start it with <code>ollama serve</code> to use local models.</p>
      {/if}

      {#each ov.installed.filter((m) => m.vision) as m (m.name)}
        <div class="flex items-center gap-3 rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-2.5">
          <div class="min-w-0 flex-1">
            <div class="text-sm text-neutral-100">{m.name} <span class="ml-1 text-[11px] text-emerald-400">installed</span></div>
            <div class="text-xs text-neutral-500">{m.size_gb} GB · vision</div>
          </div>
          <button
            class="rounded px-3 py-1 text-xs font-medium {isSelected('ollama', m.name) ? 'bg-emerald-700 text-white' : 'bg-neutral-800 text-neutral-200 hover:bg-neutral-700'}"
            onclick={() => select('ollama', m.name)}>
            {isSelected('ollama', m.name) ? 'In use' : 'Use'}
          </button>
        </div>
      {/each}

      {#each ov.recommended.filter((m) => !m.installed) as m (m.name)}
        <div class="flex items-center gap-3 rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-2.5">
          <div class="min-w-0 flex-1">
            <div class="text-sm text-neutral-100">{m.name}</div>
            <div class="text-xs text-neutral-500">{m.blurb}</div>
            {#if !m.fits_ram}
              <div class="text-[11px] text-amber-400">may be tight on your {ov.hardware.ram_total_gb} GB RAM</div>
            {/if}
          </div>
          {#if pull && pull.name === m.name && pull.status === 'pulling'}
            <div class="w-32">
              <div class="h-1.5 overflow-hidden rounded bg-neutral-800">
                <div class="h-full bg-indigo-500 transition-all" style="width: {pull.percent}%"></div>
              </div>
              <div class="mt-0.5 text-right text-[11px] text-neutral-500">{pull.percent}%</div>
            </div>
          {:else}
            <button
              class="rounded bg-indigo-600 px-3 py-1 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
              disabled={!ov.ollama_up || (pull?.status === 'pulling')}
              onclick={() => download(m.name)}>
              Download {m.size_gb} GB
            </button>
          {/if}
        </div>
      {/each}
      {#if pull?.status === 'error'}
        <p class="text-xs text-red-400">Download failed: {pull.error}</p>
      {/if}
    </div>

    <!-- cloud -->
    <div class="space-y-2">
      <div class="flex items-center gap-3">
        <h2 class="text-sm font-semibold text-neutral-200">Cloud (OpenRouter)</h2>
        {#if !cloudLoaded}
          <button class="rounded bg-neutral-800 px-2.5 py-1 text-xs text-neutral-200 hover:bg-neutral-700" onclick={loadCloud} disabled={cloudLoading}>
            {cloudLoading ? "Loading…" : "Browse cloud models"}
          </button>
        {/if}
      </div>

      <!-- API key (required to actually use a cloud model) -->
      <div class="flex flex-wrap items-center gap-2 rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2">
        <span class="text-xs text-neutral-400">OpenRouter API key</span>
        {#if hasKey}<span class="text-[11px] text-emerald-400">✓ saved</span>{/if}
        <input
          type="password"
          bind:value={apiKey}
          placeholder={hasKey ? "•••••••• (saved — paste to replace)" : "sk-or-v1-…"}
          class="min-w-[14rem] flex-1 rounded border border-neutral-700 bg-neutral-950 px-2 py-1 text-sm placeholder:text-neutral-600" />
        <button
          class="rounded bg-indigo-600 px-3 py-1 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          disabled={!apiKey.trim()}
          onclick={saveKey}>
          {keySaved ? "Saved ✓" : "Save key"}
        </button>
        <a href="https://openrouter.ai/keys" target="_blank" rel="noreferrer" class="text-[11px] text-indigo-400 underline">get a key</a>
      </div>
      {#if cloudLoaded}
        <input
          bind:value={query}
          placeholder="Search {cloud.length} vision models…"
          class="w-full rounded border border-neutral-700 bg-neutral-950 px-3 py-2 text-sm placeholder:text-neutral-600 focus:border-neutral-500 focus:outline-none" />
        <div class="max-h-96 space-y-1 overflow-auto pr-1">
          {#each filteredCloud.slice(0, 80) as m (m.id)}
            <div class="flex items-center gap-3 rounded border border-neutral-800 bg-neutral-900 px-3 py-2">
              <div class="min-w-0 flex-1">
                <div class="truncate text-sm text-neutral-100">{m.name}</div>
                <div class="text-[11px] text-neutral-500">
                  ${m.in}/${m.out} per 1M tok · ~{estCost(m)}/image
                </div>
              </div>
              <button
                class="rounded px-3 py-1 text-xs font-medium {isSelected('openrouter', m.id) ? 'bg-emerald-700 text-white' : 'bg-neutral-800 text-neutral-200 hover:bg-neutral-700'}"
                onclick={() => select('openrouter', m.id)}>
                {isSelected('openrouter', m.id) ? 'In use' : 'Use'}
              </button>
            </div>
          {/each}
        </div>
        <p class="text-[11px] text-neutral-600">Cloud models need an OpenRouter API key (we’ll wire key entry when the VLM lands). Cost is an estimate for one image question.</p>
      {/if}
    </div>
  {/if}
</section>
