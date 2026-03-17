mod desktop;

use desktop::{desktop_status, initialize_desktop, DesktopManager};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let desktop_manager = DesktopManager::default();
    let setup_manager = desktop_manager.clone();
    let exit_manager = desktop_manager.clone();
    tauri::Builder::default()
        .manage(desktop_manager.clone())
        .setup(move |app| {
            desktop::spawn_bootstrap(app.handle().clone(), setup_manager.clone());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![desktop_status, initialize_desktop])
        .build(tauri::generate_context!())
        .expect("error while building AI CAD desktop shell")
        .run(move |_app, event| {
            if matches!(event, tauri::RunEvent::Exit) {
                exit_manager.shutdown_backend_process();
            }
        });
}
