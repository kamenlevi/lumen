<script lang="ts">
  import { goto } from "$app/navigation";
  import { onMount } from "svelte";

  // This SPA always boots at "/". Which view to show depends on which window
  // we're in: the frameless "spotlight" window shows the search bar; the main
  // window shows the full app. (In a plain browser there's no Tauri window, so
  // we default to the main app.)
  onMount(async () => {
    let label = "main";
    try {
      const { getCurrentWindow } = await import("@tauri-apps/api/window");
      label = getCurrentWindow().label;
    } catch {
      /* not in Tauri */
    }
    goto(label === "spotlight" ? "/spotlight/" : "/search/", { replaceState: true });
  });
</script>
