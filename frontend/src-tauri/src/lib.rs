use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;

use once_cell::sync::Lazy;
use tauri::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{
    AppHandle, Emitter, LogicalSize, Manager, RunEvent, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

static SIDECAR: Lazy<Mutex<Option<Child>>> = Lazy::new(|| Mutex::new(None));

#[derive(Clone, serde::Serialize)]
struct SidecarReady {
    port: u16,
}

struct AppState {
    port: Arc<Mutex<Option<u16>>>,
}

// ---------- commands ----------

#[tauri::command]
fn sidecar_port(state: tauri::State<'_, AppState>) -> Option<u16> {
    *state.port.lock().unwrap()
}

#[tauri::command]
fn show_main(app: AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
    }
}

#[tauri::command]
fn hide_spotlight(app: AppHandle) {
    if let Some(win) = app.get_webview_window("spotlight") {
        let _ = win.hide();
    }
}

#[tauri::command]
fn open_in_main(app: AppHandle, route: String) {
    // Bring up the main window and navigate it to `route` (e.g. a photo page).
    // Used by the spotlight's Enter key so a result opens in the full UI.
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
        let _ = app.emit("navigate", route);
    }
}

#[tauri::command]
fn set_window_mode(app: AppHandle, expanded: bool) {
    // Resize the single window between the compact bar and the full app.
    // Done in Rust (not JS) because the JS setSize was failing on Wayland.
    if let Some(win) = app.get_webview_window("main") {
        let (w, h) = if expanded { (1120.0, 760.0) } else { (720.0, 96.0) };
        let _ = win.set_size(LogicalSize::new(w, h));
        position_window(&win, w, h);
    }
}

/// Centre the window horizontally, then nudge it a bit to the right (GNOME
/// Wayland may ignore the move; harmless if so). Keeps it vertically centred.
fn position_window(win: &WebviewWindow, w_logical: f64, h_logical: f64) {
    let monitor = win
        .current_monitor()
        .ok()
        .flatten()
        .or_else(|| win.primary_monitor().ok().flatten());
    if let Some(m) = monitor {
        let scale = m.scale_factor();
        let msize = m.size();
        let mpos = m.position();
        let win_w = w_logical * scale;
        let win_h = h_logical * scale;
        let center_x = (msize.width as f64 - win_w) / 2.0;
        let offset = msize.width as f64 * 0.18; // noticeably to the right
        let x = (center_x + offset).min(msize.width as f64 - win_w).max(0.0);
        let y = ((msize.height as f64 - win_h) / 2.0).max(0.0);
        let _ = win.set_position(tauri::PhysicalPosition::new(
            mpos.x as f64 + x,
            mpos.y as f64 + y,
        ));
    }
}

#[tauri::command]
fn frontmost_folder() -> Option<String> {
    frontmost_folder_impl()
}

// ---------- frontmost folder ----------

#[cfg(target_os = "macos")]
fn frontmost_folder_impl() -> Option<String> {
    // Ask Finder for the front window's folder. Returns empty on failure.
    let script = r#"
        try
            tell application "Finder"
                if (count of windows) is 0 then return ""
                return POSIX path of (target of front window as alias)
            end tell
        on error
            return ""
        end try
    "#;
    let out = Command::new("osascript").args(["-e", script]).output().ok()?;
    let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if s.is_empty() {
        None
    } else {
        Some(s)
    }
}

#[cfg(target_os = "linux")]
fn frontmost_folder_impl() -> Option<String> {
    // Wayland: querying the active window's PID needs compositor-specific
    // protocols that aren't exposed to regular apps. Return None gracefully.
    if std::env::var("WAYLAND_DISPLAY").is_ok()
        && std::env::var("XDG_SESSION_TYPE")
            .map(|s| s == "wayland")
            .unwrap_or(false)
    {
        return None;
    }

    // X11: use xdotool to find the focused window's PID, then read its cwd
    // via /proc. Only treat the result as a "folder" if the process is a
    // known file manager.
    let pid_out = Command::new("xdotool")
        .args(["getactivewindow", "getwindowpid"])
        .output()
        .ok()?;
    let pid: u32 = String::from_utf8_lossy(&pid_out.stdout)
        .trim()
        .parse()
        .ok()?;

    let comm = std::fs::read_to_string(format!("/proc/{pid}/comm"))
        .ok()?
        .trim()
        .to_string();

    const FILE_MANAGERS: &[&str] = &[
        "nautilus",
        "nemo",
        "caja",
        "dolphin",
        "thunar",
        "pcmanfm",
        "krusader",
        "files",
    ];
    if !FILE_MANAGERS.iter().any(|fm| comm.eq_ignore_ascii_case(fm)) {
        return None;
    }

    // /proc/<pid>/cwd is good enough for terminals; many file managers
    // chdir into the displayed folder too. When they don't, this still
    // returns *a* sensible folder.
    let cwd = std::fs::read_link(format!("/proc/{pid}/cwd")).ok()?;
    Some(cwd.to_string_lossy().to_string())
}

#[cfg(not(any(target_os = "macos", target_os = "linux")))]
fn frontmost_folder_impl() -> Option<String> {
    None
}

// ---------- sidecar lifecycle ----------

fn build_sidecar_command(app: &AppHandle) -> Command {
    if let Ok(custom) = std::env::var("LUMEN_SIDECAR") {
        let mut parts = custom.split_whitespace();
        let prog = parts.next().expect("LUMEN_SIDECAR must not be empty");
        let mut cmd = Command::new(prog);
        cmd.args(parts);
        return cmd;
    }

    let start: PathBuf = app
        .path()
        .resource_dir()
        .ok()
        .or_else(|| std::env::current_dir().ok())
        .unwrap_or_else(|| PathBuf::from("."));

    let mut repo_root = start.clone();
    for _ in 0..8 {
        if repo_root.join("sidecar").is_dir() {
            break;
        }
        if let Some(parent) = repo_root.parent() {
            repo_root = parent.to_path_buf();
        } else {
            break;
        }
    }

    let python = std::env::var("LUMEN_PYTHON").unwrap_or_else(|_| "python3".into());
    let mut cmd = Command::new(python);
    cmd.arg("-m").arg("sidecar.server");
    cmd.current_dir(repo_root);
    cmd
}

fn spawn_sidecar(app: &AppHandle, port_slot: Arc<Mutex<Option<u16>>>) -> anyhow::Result<()> {
    let mut cmd = build_sidecar_command(app);
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());

    eprintln!("[lumen] spawning sidecar: {:?}", cmd);
    let mut child = cmd.spawn()?;
    let stdout = child.stdout.take().expect("sidecar stdout missing");
    let stderr = child.stderr.take().expect("sidecar stderr missing");
    let app_clone = app.clone();

    thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines().flatten() {
            eprintln!("[sidecar] {}", line);
            if let Some(rest) = line.strip_prefix("LUMEN_PORT=") {
                if let Ok(port) = rest.trim().parse::<u16>() {
                    *port_slot.lock().unwrap() = Some(port);
                    let _ = app_clone.emit("sidecar://ready", SidecarReady { port });
                    let script = format!("window.__LUMEN_PORT = {port};");
                    for label in ["main", "spotlight"] {
                        if let Some(win) = app_clone.get_webview_window(label) {
                            let _ = win.eval(&script);
                        }
                    }
                }
            }
        }
        eprintln!("[lumen] sidecar stdout closed");
    });

    thread::spawn(move || {
        let reader = BufReader::new(stderr);
        for line in reader.lines().flatten() {
            eprintln!("[sidecar:err] {}", line);
        }
    });

    *SIDECAR.lock().unwrap() = Some(child);
    Ok(())
}

fn kill_sidecar() {
    if let Some(mut child) = SIDECAR.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

/// Lumen's data dir — mirrors sidecar/paths.py so we can find the port file.
fn data_dir() -> PathBuf {
    if let Ok(d) = std::env::var("LUMEN_DATA_DIR") {
        if !d.is_empty() {
            return PathBuf::from(d);
        }
    }
    if let Ok(x) = std::env::var("XDG_DATA_HOME") {
        if !x.is_empty() {
            return PathBuf::from(x).join("lumen");
        }
    }
    if let Ok(h) = std::env::var("HOME") {
        return PathBuf::from(h).join(".local/share/lumen");
    }
    PathBuf::from(".")
}

/// If a sidecar from another (first) instance is already serving, return its
/// port. A second instance reuses it instead of spawning a duplicate torch
/// process that would just leak when this instance is single-instanced away.
fn existing_sidecar_port() -> Option<u16> {
    use std::net::TcpStream;
    use std::time::Duration;
    let pf = data_dir().join("server.port");
    let port: u16 = std::fs::read_to_string(&pf).ok()?.trim().parse().ok()?;
    let addr = format!("127.0.0.1:{port}").parse().ok()?;
    TcpStream::connect_timeout(&addr, Duration::from_millis(400)).ok()?;
    Some(port)
}

// ---------- window (single, dual-mode: compact spotlight ⇄ expanded app) ----------

/// Show the one window in compact (spotlight) mode — a centred search bar.
/// The frontend listens for "ui://spotlight" to switch mode and resize.
fn show_compact(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.emit("ui://spotlight", ());
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
    }
}

/// Show the window in expanded (full UI) mode.
fn show_expanded(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.emit("ui://expand", ());
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
    }
}

/// Ctrl+Space always opens *something*:
///  - app focused/in front  → jump to the Chat tab, ready to type
///  - hidden or behind another app → the compact spotlight search
fn handle_hotkey(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let visible = win.is_visible().unwrap_or(false);
        let focused = win.is_focused().unwrap_or(false);
        if visible && focused {
            let _ = win.emit("ui://chat", ());
            let _ = win.show();
            let _ = win.set_focus();
        } else {
            show_compact(app);
        }
    }
}

// ---------- tray ----------

fn build_tray(app: &AppHandle) -> anyhow::Result<()> {
    let show_item = MenuItem::with_id(app, "show", "Open Lumen", true, None::<&str>)?;
    let search_item = MenuItem::with_id(
        app,
        "search",
        "Quick search…",
        true,
        Some("CmdOrCtrl+Space"),
    )?;
    let settings_item = MenuItem::with_id(app, "settings", "Settings…", true, None::<&str>)?;
    let library_item = MenuItem::with_id(app, "library", "Library…", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit Lumen", true, None::<&str>)?;
    let sep1 = PredefinedMenuItem::separator(app)?;
    let sep2 = PredefinedMenuItem::separator(app)?;

    let menu = Menu::with_items(
        app,
        &[
            &search_item,
            &show_item,
            &sep1,
            &library_item,
            &settings_item,
            &sep2,
            &quit_item,
        ],
    )?;

    let icon = app
        .default_window_icon()
        .cloned()
        .ok_or_else(|| anyhow::anyhow!("default window icon not configured"))?;

    TrayIconBuilder::with_id("main")
        .icon(icon)
        .icon_as_template(true) // macOS: lets the OS recolor for light/dark
        .tooltip("Lumen")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(handle_menu_event)
        .build(app)?;
    Ok(())
}

fn handle_menu_event(app: &AppHandle, event: MenuEvent) {
    match event.id().as_ref() {
        "show" => show_expanded(app),
        "search" => show_compact(app),
        "settings" => {
            show_expanded(app);
            let _ = app.emit("navigate", "/settings/");
        }
        "library" => {
            show_expanded(app);
            let _ = app.emit("navigate", "/library/");
        }
        "quit" => {
            kill_sidecar();
            app.exit(0);
        }
        _ => {}
    }
}

// ---------- entry point ----------

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let port = Arc::new(Mutex::new(None));
    let port_for_state = Arc::clone(&port);

    // CmdOrCtrl+Space — Cmd on macOS (Modifiers::META), Ctrl elsewhere.
    // macOS users will want to free this from system Spotlight in System
    // Settings → Keyboard → Shortcuts → Spotlight.
    #[cfg(target_os = "macos")]
    let primary_mod = Modifiers::META;
    #[cfg(not(target_os = "macos"))]
    let primary_mod = Modifiers::CONTROL;
    let shortcut = Shortcut::new(Some(primary_mod), Code::Space);
    let trigger = shortcut.clone();

    tauri::Builder::default()
        // Must be the FIRST plugin. On Wayland (GNOME), apps can't grab a
        // system-wide hotkey, so the native Ctrl+Space below won't fire. The
        // workaround: a GNOME custom keyboard shortcut runs the Lumen binary
        // again; this catches that second launch and toggles the spotlight
        // instead of opening a duplicate window.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            handle_hotkey(app);
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(move |app, sc, event| {
                    if event.state() == ShortcutState::Pressed && sc == &trigger {
                        handle_hotkey(app);
                    }
                })
                .build(),
        )
        .manage(AppState { port: port_for_state })
        .invoke_handler(tauri::generate_handler![
            sidecar_port,
            show_main,
            open_in_main,
            set_window_mode,
            frontmost_folder
        ])
        .setup(move |app| {
            let handle = app.handle().clone();
            // Reuse a sidecar that's already running (e.g. this is a second
            // launch from Ctrl+Space) rather than spawning a duplicate.
            if let Some(p) = existing_sidecar_port() {
                *port.lock().unwrap() = Some(p);
                let script = format!("window.__LUMEN_PORT = {p};");
                if let Some(win) = handle.get_webview_window("main") {
                    let _ = win.eval(&script);
                }
                let _ = handle.emit("sidecar://ready", SidecarReady { port: p });
            } else if let Err(e) = spawn_sidecar(&handle, Arc::clone(&port)) {
                eprintln!("[lumen] failed to spawn sidecar: {e:?}");
            }

            // Tray icon — lets the app live without a window.
            if let Err(e) = build_tray(&handle) {
                eprintln!("[lumen] failed to build tray: {e:?}");
            }

            // Spotlight-first: opening Lumen pops the compact search bar. It
            // expands into the full UI in place when you search or open a result.
            show_compact(&handle);

            // Try registering the global hotkey. Some Linux compositors
            // refuse — fail soft so the rest of the app keeps working.
            if let Err(e) = handle.global_shortcut().register(shortcut.clone()) {
                eprintln!(
                    "[lumen] global shortcut registration failed: {e:?} \
                     (use the tray icon to launch the spotlight)"
                );
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing just hides — the tray icon keeps Lumen alive. Compact-mode
            // blur-to-hide is handled in the frontend (only when it's the bar).
            if window.label() == "main" {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Tauri application")
        .run(|_app, event| {
            if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
                kill_sidecar();
            }
        });
}
