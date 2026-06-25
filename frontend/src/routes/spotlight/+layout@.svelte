<script lang="ts">
  import "../../app.css";
  let { children } = $props();
</script>

<div class="spotlight-root">
  {@render children()}
</div>

<style>
  :global(html), :global(body), :global(.spotlight-root) {
    height: 100%;
    width: 100%;
    overflow: hidden;
    /* Solid background matching the card — the spotlight window is opaque,
       so this fills it edge-to-edge with no black gaps. */
    background: #15151a !important;
  }
</style>
