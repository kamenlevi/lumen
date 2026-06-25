<script lang="ts">
  import { goto } from "$app/navigation";
  import { onMount } from "svelte";
  // The compact bar overlays this until the user searches; the underlying
  // route is the search page.
  onMount(() => goto("/search/", { replaceState: true }));
</script>
