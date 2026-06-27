<script lang="ts">
  import { goto } from "$app/navigation";
  import { onMount } from "svelte";
  // The compact bar overlays this until the user searches; the underlying
  // route is Chat (the single find/ask surface).
  onMount(() => goto("/chat/", { replaceState: true }));
</script>
