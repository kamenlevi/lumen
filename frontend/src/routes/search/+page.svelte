<script lang="ts">
  import { goto } from "$app/navigation";
  import { onMount } from "svelte";
  // Search has been folded into Chat — one find/ask surface. Anyone landing
  // here (old link, bookmark) is sent on. The compact bar's query rides along
  // in the spotlightQuery store, which the chat page consumes.
  onMount(() => goto("/chat/", { replaceState: true }));
</script>
