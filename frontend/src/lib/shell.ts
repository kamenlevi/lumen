// Unified-window shell state: one window that is either the compact spotlight
// bar or the expanded full app. Switching mode resizes the OS window.
import { writable } from "svelte/store";

export const mode = writable<"compact" | "expanded">("compact");
export const spotlightQuery = writable<string>("");

export async function applyMode(m: "compact" | "expanded") {
  mode.set(m);
  try {
    // Resize in Rust — the JS setSize was silently failing on Wayland, which
    // left the expanded window stuck at the compact bar's height.
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("set_window_mode", { expanded: m === "expanded" });
  } catch { /* not running in Tauri */ }
}

export async function hideWindow() {
  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    await getCurrentWindow().hide();
  } catch { /* not in Tauri */ }
}

export async function minimizeWindow() {
  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    await getCurrentWindow().minimize();
  } catch { /* not in Tauri */ }
}
