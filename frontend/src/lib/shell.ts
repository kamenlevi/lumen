// Unified-window shell state: one window that is either the compact spotlight
// bar or the expanded full app. Switching mode resizes the OS window.
import { writable } from "svelte/store";

export const mode = writable<"compact" | "expanded">("compact");
export const spotlightQuery = writable<string>("");

const SIZES = {
  compact: { w: 720, h: 96 },
  expanded: { w: 1120, h: 760 },
};

export async function applyMode(m: "compact" | "expanded") {
  mode.set(m);
  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    const { LogicalSize } = await import("@tauri-apps/api/dpi");
    const win = getCurrentWindow();
    const s = SIZES[m];
    await win.setSize(new LogicalSize(s.w, s.h));
    try { await win.center(); } catch { /* GNOME Wayland blocks positioning */ }
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
