mod attachments;
mod models;
mod provider;
mod security;
mod storage;

use crate::{
    models::{AppSettings, BackendEvent, EnhancementRequest, ExtractedAttachment, HistoryRecord, ProviderConfig},
    storage::Storage,
};
use sha2::{Digest, Sha256};
use std::sync::Arc;
use tauri::{ipc::Channel, Manager, State};
use tokio::sync::Mutex as AsyncMutex;
use tokio_util::sync::CancellationToken;
use url::Url;

pub struct AppState {
    pub client: reqwest::Client,
    pub cancellation: AsyncMutex<Option<CancellationToken>>,
    pub storage: Arc<Storage>,
}

#[tauri::command]
async fn enhance_prompt(
    request: EnhancementRequest,
    on_event: Channel<BackendEvent>,
    state: State<'_, AppState>,
) -> Result<(), String> {
    provider::enhance(request, on_event, state).await
}

#[tauri::command]
async fn cancel_enhancement(state: State<'_, AppState>) -> Result<(), String> {
    if let Some(token) = state.cancellation.lock().await.take() { token.cancel(); }
    Ok(())
}

#[tauri::command]
fn get_provider_config(state: State<'_, AppState>) -> Result<ProviderConfig, String> {
    state.storage.provider_config()
}

#[tauri::command]
fn save_provider_config(mut config: ProviderConfig, state: State<'_, AppState>) -> Result<(), String> {
    validate_base_url(&config.base_url)?;
    config.base_url = config.base_url.trim_end_matches('/').to_string();
    state.storage.save_provider_config(&config)
}

#[tauri::command]
async fn validate_provider(api_key: String, base_url: String, state: State<'_, AppState>) -> Result<(), String> {
    validate_base_url(&base_url)?;
    if api_key.trim().is_empty() { return Err("请输入 API Key".into()); }
    let endpoint = format!("{}/models", base_url.trim_end_matches('/'));
    let response = state.client.get(endpoint).bearer_auth(api_key.trim()).send().await
        .map_err(|error| format!("连接 DeepSeek 失败：{error}"))?;
    if response.status().is_success() { return Ok(()); }
    match response.status().as_u16() {
        401 | 403 => Err("API Key 无效或没有访问权限".into()),
        status => Err(format!("连接验证失败，DeepSeek 返回 HTTP {status}")),
    }
}

#[tauri::command]
async fn extract_attachment(path: String) -> Result<ExtractedAttachment, String> {
    tauri::async_runtime::spawn_blocking(move || attachments::extract(&path)).await
        .map_err(|error| format!("文件处理任务失败：{error}"))?
}

#[tauri::command]
async fn copy_and_open(text: String, target_url: String, clear_clipboard: bool) -> Result<(), String> {
    if text.trim().is_empty() { return Err("没有可复制的提示词".into()); }
    validate_target_url(&target_url)?;
    let mut clipboard = arboard::Clipboard::new().map_err(|error| format!("无法访问剪贴板：{error}"))?;
    clipboard.set_text(text.clone()).map_err(|error| format!("无法写入剪贴板：{error}"))?;
    open::that(&target_url).map_err(|error| format!("提示词已复制，但无法打开目标网页：{error}"))?;

    if clear_clipboard {
        let expected = Sha256::digest(text.as_bytes()).to_vec();
        tauri::async_runtime::spawn(async move {
            tokio::time::sleep(std::time::Duration::from_secs(120)).await;
            if let Ok(mut clipboard) = arboard::Clipboard::new() {
                if let Ok(current) = clipboard.get_text() {
                    if Sha256::digest(current.as_bytes()).as_slice() == expected.as_slice() {
                        let _ = clipboard.set_text(String::new());
                    }
                }
            }
        });
    }
    Ok(())
}

#[tauri::command]
fn copy_text(text: String) -> Result<(), String> {
    if text.trim().is_empty() { return Err("没有可复制的提示词".into()); }
    let mut clipboard = arboard::Clipboard::new().map_err(|error| format!("无法访问剪贴板：{error}"))?;
    clipboard.set_text(text).map_err(|error| format!("无法写入剪贴板：{error}"))
}

#[tauri::command]
fn scan_sensitive(text: String) -> Vec<String> {
    security::findings(&text)
}

#[tauri::command]
fn save_history(record: HistoryRecord, state: State<'_, AppState>) -> Result<(), String> {
    state.storage.save_history(&record)
}

#[tauri::command]
fn list_history(query: Option<String>, state: State<'_, AppState>) -> Result<Vec<HistoryRecord>, String> {
    state.storage.list_history(query.as_deref())
}

#[tauri::command]
fn delete_history(id: String, state: State<'_, AppState>) -> Result<(), String> {
    state.storage.delete_history(&id)
}

#[tauri::command]
fn get_app_settings(state: State<'_, AppState>) -> Result<AppSettings, String> {
    state.storage.app_settings()
}

#[tauri::command]
fn save_app_settings(settings: AppSettings, state: State<'_, AppState>) -> Result<(), String> {
    state.storage.save_app_settings(&settings)
}

#[tauri::command]
fn clear_all_data(state: State<'_, AppState>) -> Result<(), String> {
    state.storage.clear_all_data()
}

#[tauri::command]
fn export_data(path: String, state: State<'_, AppState>) -> Result<usize, String> {
    state.storage.export_data(std::path::Path::new(&path))
}

#[tauri::command]
fn import_data(path: String, state: State<'_, AppState>) -> Result<usize, String> {
    state.storage.import_data(std::path::Path::new(&path))
}

fn validate_base_url(value: &str) -> Result<(), String> {
    let parsed = Url::parse(value).map_err(|_| "API Base URL 无效".to_string())?;
    let is_local = matches!(parsed.host_str(), Some("127.0.0.1" | "localhost"));
    if parsed.scheme() != "https" && !(parsed.scheme() == "http" && is_local) {
        return Err("API 地址必须使用 HTTPS；仅本机地址允许 HTTP".into());
    }
    Ok(())
}

fn validate_target_url(value: &str) -> Result<(), String> {
    let parsed = Url::parse(value).map_err(|_| "目标网页地址无效".to_string())?;
    if !matches!(parsed.scheme(), "http" | "https") { return Err("只允许打开 HTTP 或 HTTPS 网页".into()); }
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    ensure_webview2_runtime();
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let storage = Storage::initialize(app.handle()).map_err(std::io::Error::other)?;
            app.manage(AppState {
                client: reqwest::Client::builder()
                    .connect_timeout(std::time::Duration::from_secs(10))
                    .read_timeout(std::time::Duration::from_secs(30))
                    .build()?,
                cancellation: AsyncMutex::new(None),
                storage: Arc::new(storage),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            enhance_prompt,
            cancel_enhancement,
            get_provider_config,
            save_provider_config,
            validate_provider,
            extract_attachment,
            copy_and_open,
            copy_text,
            scan_sensitive,
            save_history,
            list_history,
            delete_history,
            get_app_settings,
            save_app_settings,
            clear_all_data,
            export_data,
            import_data,
        ])
        .run(tauri::generate_context!())
        .expect("PromptCraft failed to start");
}

#[cfg(target_os = "windows")]
fn ensure_webview2_runtime() {
    use std::{env, path::PathBuf, process::Command};

    let reg = env::var_os("WINDIR").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(r"C:\Windows"))
        .join("System32").join("reg.exe");
    let registry_roots = [
        r"HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients",
        r"HKCU\Software\Microsoft\EdgeUpdate\Clients",
    ];
    let installed = registry_roots.iter().any(|root| {
        Command::new(&reg).args(["query", root, "/s", "/f", "Microsoft Edge WebView2 Runtime"])
            .output().map(|output| output.status.success()).unwrap_or(false)
    });
    if installed { return; }

    let installer = env::current_exe().ok().and_then(|path| path.parent().map(|parent| parent.join("MicrosoftEdgeWebView2Setup.exe")));
    if let Some(installer) = installer.filter(|path| path.is_file()) {
        let _ = Command::new(installer).args(["/silent", "/install"]).status();
    }
}

#[cfg(not(target_os = "windows"))]
fn ensure_webview2_runtime() {}
