mod commands;
mod config;
mod daemon;
mod models;
mod secrets;

fn app_builder() -> tauri::Builder<tauri::Wry> {
    tauri::Builder::default()
        .setup(|app| {
            config::bootstrap(app.handle()).map_err(|error| {
                Box::new(std::io::Error::new(std::io::ErrorKind::Other, error))
                    as Box<dyn std::error::Error>
            })?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::get_machine_info,
            commands::get_settings,
            commands::get_daemon_status,
            commands::connect_to_central,
            commands::bootstrap_operator_connect,
            commands::create_server_setup_bundle,
            commands::provision_server_setup_bundle,
            commands::send_chat_message,
        ])
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    app_builder()
        .run(tauri::generate_context!())
        .expect("error while running Hermes Companion");
}
