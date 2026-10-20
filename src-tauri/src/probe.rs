//! WP0 端到端探针：P0-1 / P0-2 / P0-3。
//!
//! 这些探针走**真实命令层**（Tauri IPC 的参数反序列化 + 真实命令处理函数），
//! 而不是直接调用内部函数。这一点是刻意的：
//! P0-1 的根因正是"命令入参的 serde 行为"——`ProviderConfig.api_key` 带
//! `skip_deserializing`，命令收到的值恒为 None。
//! 任何绕过命令层的测试都测不出它（它在内部函数里看起来完全正常）。
//!
//! 运行方式：**不要**直接 cargo test，用 tools\probe-p0-baseline.ps1，
//! 它会准备 OpenSSL 环境、只跑本文件、用可读格式打印结果。
//!
//! 探针清单：
//! - `probe_p0_1_*`：保存 Key → 重新打开（模拟重启）→ has_api_key == true
//!   → 用该 Key 对 mock 端点发一次增强 → 断言 Key 确实被使用。
//! - `probe_p0_2_*`：导入含攻击者 baseUrl 的包 → 断言端点没被改写，
//!   且**攻击者端点收到 0 个请求、真实 Key 不出现在任何外发请求里**。
//! - `probe_p0_3_*`：造 >500 条历史 → 导出 → 断言导出条数 == 总条数。
//!
//! P0-4（`provider.rs` 温度注释越界）是静态断言，不在本文件：
//! 见同目录的 `claims.rs`。

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;

use serde_json::{Value, json};

use crate::{
    AppState,
    models::{BackendEvent, EnhancementRequest, HistoryRecord},
    provider,
    storage::Storage,
};

/// 探针用的 API Key（假的，只写进沙箱凭据服务）。
const PROBE_KEY: &str = "sk-probe-end-to-end-key-000001";
/// mock 端点返回的模型输出。
const MODEL_OUTPUT: &str = r#"{"status":"ready","task_type":"code","primary_prompt":"探针用的增强结果","assumptions":[],"questions":[],"changes":[],"suggestions":[],"risk_flags":[]}"#;

// ---------------------------------------------------------------- mock 端点

#[derive(Debug, Clone)]
struct CapturedRequest {
    method: String,
    path: String,
    authorization: Option<String>,
    body: String,
}

/// 本机 mock 端点：记录收到的每个请求（含 Authorization 头），并回一段 SSE。
///
/// 刻意用 `http://127.0.0.1:<port>`：这个地址能通过 base_url 的 HTTPS 校验
/// （本机地址允许 HTTP），因此"攻击者端点"可以是一个**真的能被连上的本地服务**
/// —— 比 `https://evil.example` 这种连不上的地址更有说服力：
/// 如果导入包能改写端点，探针会当场看到 Key 被发到攻击者端口。
struct MockEndpoint {
    base_url: String,
    captured: Arc<Mutex<Vec<CapturedRequest>>>,
    #[allow(dead_code)]
    worker: Option<JoinHandle<()>>,
}

impl MockEndpoint {
    fn requests(&self) -> Vec<CapturedRequest> {
        self.captured.lock().expect("captured lock").clone()
    }
}

fn spawn_mock_endpoint(model_output: &str) -> MockEndpoint {
    let listener = TcpListener::bind("127.0.0.1:0").expect("绑定 mock 端点");
    let port = listener.local_addr().expect("读取 mock 端点地址").port();
    let captured: Arc<Mutex<Vec<CapturedRequest>>> = Arc::new(Mutex::new(Vec::new()));
    let sink = Arc::clone(&captured);
    let payload = sse_payload(model_output);
    let worker = std::thread::spawn(move || {
        for incoming in listener.incoming() {
            let Ok(mut stream) = incoming else { break };
            let Ok(request) = read_request(&mut stream) else {
                continue;
            };
            sink.lock().expect("captured lock").push(request);
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                payload.len(),
                payload
            );
            let _ = stream.write_all(response.as_bytes());
            let _ = stream.flush();
        }
    });
    MockEndpoint {
        base_url: format!("http://127.0.0.1:{port}"),
        captured,
        worker: Some(worker),
    }
}

fn sse_payload(model_output: &str) -> String {
    let event = json!({"choices": [{"delta": {"content": model_output}}]});
    format!("data: {event}\n\ndata: [DONE]\n\n")
}

fn read_request(stream: &mut TcpStream) -> std::io::Result<CapturedRequest> {
    let mut buffer: Vec<u8> = Vec::new();
    let mut chunk = [0u8; 2048];
    let header_end = loop {
        let read = stream.read(&mut chunk)?;
        if read == 0 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::UnexpectedEof,
                "读完请求头前连接就断了",
            ));
        }
        buffer.extend_from_slice(&chunk[..read]);
        if let Some(position) = buffer.windows(4).position(|window| window == b"\r\n\r\n") {
            break position;
        }
    };
    let head = String::from_utf8_lossy(&buffer[..header_end]).to_string();
    let mut lines = head.lines();
    let request_line = lines.next().unwrap_or_default().to_string();
    let mut parts = request_line.split_whitespace();
    let method = parts.next().unwrap_or_default().to_string();
    let path = parts.next().unwrap_or_default().to_string();
    let mut authorization = None;
    let mut content_length = 0usize;
    for line in lines {
        if let Some((name, value)) = line.split_once(':') {
            match name.trim().to_ascii_lowercase().as_str() {
                "authorization" => authorization = Some(value.trim().to_string()),
                "content-length" => content_length = value.trim().parse().unwrap_or(0),
                _ => {}
            }
        }
    }
    let body_start = header_end + 4;
    while buffer.len() < body_start + content_length {
        let read = stream.read(&mut chunk)?;
        if read == 0 {
            break;
        }
        buffer.extend_from_slice(&chunk[..read]);
    }
    let body = String::from_utf8_lossy(&buffer[body_start.min(buffer.len())..]).to_string();
    Ok(CapturedRequest {
        method,
        path,
        authorization,
        body,
    })
}

// ---------------------------------------------------------------- 沙箱

/// 探针沙箱：独立的临时目录 + 独立的凭据服务名，绝不触碰真实的 PromptCraft 凭据。
struct ProbeSandbox {
    dir: PathBuf,
    service: String,
}

impl ProbeSandbox {
    fn new() -> Self {
        let dir = std::env::temp_dir().join(format!("PromptCraft-probe-{}", uuid::Uuid::new_v4()));
        // 凭据服务名**每个测试实例唯一**。
        //
        // 曾经试过改成固定名（`PromptCraftProbe`）以防崩溃时泄漏，结果更糟：
        // 探针的这些用例是 `cargo test` 的并行测试，共享服务名时，
        // **一个用例的 Drop 会删掉另一个用例正在用的凭据**，
        // 于是出现「增强请求应当成功: 尚未配置 DeepSeek API Key」的随机失败。
        // 隔离优先于防泄漏。
        //
        // 崩溃时的泄漏由 tools/cleanup-test-credentials.py 兜底清理
        // （只删带 Test-/Probe- 后缀的沙箱条目，绝不碰生产条目）。
        let service = format!("PromptCraftProbe-{}", uuid::Uuid::new_v4());
        Self { dir, service }
    }

    fn open_storage(&self) -> Storage {
        Storage::open(&self.dir, &self.service).expect("打开探针存储")
    }
}

impl Drop for ProbeSandbox {
    fn drop(&mut self) {
        // 凭据删除要与其它测试的凭据操作串行化：Windows 凭据管理器在并发删改时
        // 会静默失败（keyring 既不重试也不报错），表现为读回偶发失败。
        let _guard = crate::storage::test_credential_lock().lock();
        // **两个账号都要删**：API Key 账号与数据库密钥账号。
        // 此前只删了前者 ⇒ 每次探针运行都留下一条 `database-key.PromptCraftProbe`
        // （实测清理后仍剩这一条）。storage.rs 的 TestSandbox 本来就两个都删，
        // 这里必须对齐。
        for account in [crate::storage::API_KEY_ACCOUNT, "database-key"] {
            // 退避重试：凭据管理器在被占用时**连条目都打不开**，
            // `Entry::new` 失败也必须重试，不能一次就放弃（否则会残留）。
            for attempt in 0..20 {
                let Ok(entry) = keyring::Entry::new(&self.service, account) else {
                    std::thread::sleep(std::time::Duration::from_millis(25 * (attempt + 1)));
                    continue;
                };
                match entry.delete_credential() {
                    Ok(()) | Err(keyring::Error::NoEntry) => break,
                    Err(_) => std::thread::sleep(std::time::Duration::from_millis(25 * (attempt + 1))),
                }
            }
        }
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

// ---------------------------------------------------------------- app / IPC

type ProbeApp = tauri::App<tauri::test::MockRuntime>;

fn build_probe_app(storage: Arc<Storage>) -> ProbeApp {
    let state = AppState {
        client: reqwest::Client::builder()
            .build()
            .expect("构造探针 HTTP 客户端"),
        cancellation: tokio::sync::Mutex::new(None),
        storage,
    };
    tauri::test::mock_builder()
        .manage(state)
        .invoke_handler(tauri::generate_handler![
            crate::save_provider_config,
            crate::get_provider_config,
            crate::import_data,
            crate::export_data,
        ])
        .build(tauri::test::mock_context(tauri::test::noop_assets()))
        .expect("构造探针 mock 应用")
}

fn open_webview(app: &ProbeApp) -> tauri::WebviewWindow<tauri::test::MockRuntime> {
    tauri::WebviewWindowBuilder::new(app, "main", Default::default())
        .build()
        .expect("构造探针 webview")
}

/// 通过真实 IPC 调用命令（参数会经过 Tauri 的 serde 反序列化）。
fn invoke(
    webview: &tauri::WebviewWindow<tauri::test::MockRuntime>,
    command: &str,
    body: Value,
) -> Result<tauri::ipc::InvokeResponseBody, Value> {
    tauri::test::get_ipc_response(
        webview,
        tauri::webview::InvokeRequest {
            cmd: command.into(),
            callback: tauri::ipc::CallbackFn(0),
            error: tauri::ipc::CallbackFn(1),
            url: "http://tauri.localhost".parse().unwrap(),
            body: tauri::ipc::InvokeBody::Json(body),
            headers: Default::default(),
            invoke_key: tauri::test::INVOKE_KEY.to_string(),
        },
    )
}

fn provider_config_body(base_url: &str) -> Value {
    json!({
        "baseUrl": base_url,
        "hasApiKey": false,
        "defaultModel": "deepseek-chat",
        "v4FlashModelId": "deepseek-v4-flash",
        "inputPrice": 0.001,
        "outputPrice": 0.002,
        "models": ["deepseek-chat"],
    })
}

/// 跑一次真实增强流程（provider::enhance），返回收到的后端事件。
fn run_enhancement(app: &ProbeApp) -> Vec<Value> {
    use tauri::Manager;
    let events: Arc<Mutex<Vec<Value>>> = Arc::new(Mutex::new(Vec::new()));
    let sink = Arc::clone(&events);
    let channel = tauri::ipc::Channel::<BackendEvent>::new(move |body| {
        if let Ok(value) = body.deserialize::<Value>() {
            sink.lock().expect("events lock").push(value);
        }
        Ok(())
    });
    let request = EnhancementRequest {
        original_text: "帮我把这段需求写清楚".into(),
        context_text: String::new(),
        attachments: Vec::new(),
        model: "deepseek-chat".into(),
        target_model: "豆包".into(),
        verbosity: "standard".into(),
        custom_instructions: None,
        clarification_round: 0,
        clarification_answers: Vec::new(),
        profile_summary: Vec::new(),
    };
    let state = app.state::<AppState>();
    tauri::async_runtime::block_on(provider::enhance(request, channel, state))
        .expect("增强请求应当成功");
    events.lock().expect("events lock").clone()
}

// ---------------------------------------------------------------- 导入包构造

fn write_import_package(path: &Path, provider_json: &str) {
    use zip::{CompressionMethod, ZipWriter, write::SimpleFileOptions};
    let file = std::fs::File::create(path).expect("创建导入包");
    let mut writer = ZipWriter::new(file);
    let options = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);
    let manifest = json!({
        "schemaVersion": 1,
        "product": "PromptCraft",
        "exportedAt": "2026-01-01T00:00:00Z",
    })
    .to_string();
    let settings = json!({
        "clearClipboard": false,
        "profileEnabled": true,
        "customTargetUrl": "",
        "monthlyWarningLimit": 8.0,
        "monthlyLimit": 10.0,
        "maxHistoryMb": 64,
        "profileRules": [],
    })
    .to_string();
    for (name, content) in [
        ("manifest.json", manifest),
        ("history.json", "[]".to_string()),
        ("settings.json", settings),
        ("provider.json", provider_json.to_string()),
    ] {
        writer.start_file(name, options).expect("写入导入包条目");
        writer
            .write_all(content.as_bytes())
            .expect("写入导入包内容");
    }
    writer.finish().expect("完成导入包");
}

// ---------------------------------------------------------------- P0-1 探针

#[test]
fn probe_p0_1_key_is_saved_and_really_used_against_the_endpoint() {
    let sandbox = ProbeSandbox::new();
    let endpoint = spawn_mock_endpoint(MODEL_OUTPUT);

    // ① 通过真实命令层保存 Key（这一步就走 IPC 的 serde 反序列化）
    //    凭据写入必须与其它测试的凭据操作串行化：Windows 凭据管理器在并发删改时
    //    会静默失败，写进去的值可能当场读不回来。
    let app = build_probe_app(Arc::new(sandbox.open_storage()));
    let webview = open_webview(&app);
    let saved = {
        let _guard = crate::storage::test_credential_lock().lock();
        invoke(
            &webview,
            "save_provider_config",
            json!({
                "config": provider_config_body(&endpoint.base_url),
                "apiKey": PROBE_KEY,
            }),
        )
    };
    assert!(saved.is_ok(), "保存供应商配置命令失败：{:?}", saved.err());

    // ② 重新打开（模拟重启）：全新的 Storage 实例 + 真实命令读回
    let reopened = sandbox.open_storage();
    // 读回凭据必须与其它测试的凭据操作串行化（见 test_credential_lock 的说明）：
    // 并发删改时 Windows 凭据管理器会静默失败，表现为"刚存的 Key 读不回来"。
    let read_back = {
        let _guard = crate::storage::test_credential_lock().lock();
        crate::storage::read_test_credential(&reopened)
    };
    assert_eq!(
        read_back.expect("重启后应能读回 API Key"),
        PROBE_KEY,
        "重启后读回的 API Key 与写入值不一致"
    );
    let app_after_restart = build_probe_app(Arc::new(reopened));
    let webview_after_restart = open_webview(&app_after_restart);
    let config = invoke(&webview_after_restart, "get_provider_config", json!({}))
        .expect("读取供应商配置")
        .deserialize::<Value>()
        .expect("反序列化供应商配置");
    assert_eq!(
        config["hasApiKey"],
        json!(true),
        "hasApiKey 必须为 true（UI 据此显示「已配置」）"
    );
    assert!(
        config.get("apiKey").is_none(),
        "读取配置不得回传 API Key 明文：{config}"
    );

    // ③ 用该 Key 对 mock 端点发一次增强，断言 Key 确实被使用
    let events = run_enhancement(&app_after_restart);
    let requests = endpoint.requests();
    assert_eq!(requests.len(), 1, "应当恰好发出 1 次增强请求");
    assert_eq!(requests[0].method, "POST");
    assert_eq!(requests[0].path, "/chat/completions");
    assert_eq!(
        requests[0].authorization.as_deref(),
        Some(format!("Bearer {PROBE_KEY}").as_str()),
        "请求必须携带刚保存的 API Key（Authorization 头不对）"
    );
    assert!(
        requests[0].body.contains("primary_prompt"),
        "请求体应当是一次真实的增强请求（找不到 primary_prompt 提示词）"
    );
    assert!(
        events
            .iter()
            .any(|event| event.get("type").and_then(Value::as_str) == Some("result")),
        "增强流程应当产生 result 事件，实际事件：{events:?}"
    );

    println!(
        "[P0-1] 保存 → 重启读回 → 增强请求：Authorization={:?}",
        requests[0].authorization
    );
}

// ---------------------------------------------------------------- P0-2 探针

#[test]
fn probe_p0_2_import_cannot_redirect_the_key_to_another_host() {
    let sandbox = ProbeSandbox::new();
    let legit = spawn_mock_endpoint(MODEL_OUTPUT);
    // 攻击者端点：同样跑在本机 HTTP 上，**能通过 base_url 的 HTTPS 校验**，
    // 因此"导入包改写端点"在这里是真实可发生的，而不是理论上的。
    let attacker = spawn_mock_endpoint(MODEL_OUTPUT);

    let app = build_probe_app(Arc::new(sandbox.open_storage()));
    let webview = open_webview(&app);
    let saved = invoke(
        &webview,
        "save_provider_config",
        json!({
            "config": provider_config_body(&legit.base_url),
            "apiKey": PROBE_KEY,
        }),
    );
    assert!(saved.is_ok(), "保存供应商配置命令失败：{:?}", saved.err());

    // 构造含攻击者 baseUrl 的导入包
    let package_path = sandbox.dir.join("malicious-import.zip");
    let malicious_provider = json!({
        "baseUrl": attacker.base_url,
        "hasApiKey": true,
        "defaultModel": "deepseek-chat",
        "v4FlashModelId": "deepseek-v4-flash",
        "inputPrice": 0.001,
        "outputPrice": 0.002,
        "apiKey": "sk-attacker-planted-key-000001",
    })
    .to_string();
    write_import_package(&package_path, &malicious_provider);

    let imported = invoke(
        &webview,
        "import_data",
        json!({ "path": package_path.to_string_lossy() }),
    );
    assert!(
        imported.is_ok(),
        "导入命令本身应当执行成功（端点被忽略不等于导入失败）：{:?}",
        imported.err()
    );

    // 断言 ①：端点没有被改写
    let config = invoke(&webview, "get_provider_config", json!({}))
        .expect("读取供应商配置")
        .deserialize::<Value>()
        .expect("反序列化供应商配置");
    assert_eq!(
        config["baseUrl"],
        json!(legit.base_url),
        "导入包不得改写 API 地址（Key 的目的地）"
    );
    assert_ne!(
        config["baseUrl"],
        json!(attacker.base_url),
        "导入包的攻击者地址必须被忽略"
    );

    // 断言 ②：导入包里的 apiKey 没有进本机凭据
    let local_key = {
        let _guard = crate::storage::test_credential_lock().lock();
        crate::storage::read_test_credential(&sandbox.open_storage())
    };
    assert_eq!(
        local_key.expect("读回本机 Key"),
        PROBE_KEY,
        "导入包不得改写本机 API Key"
    );

    // 断言 ③：真实 Key 没有出现在任何外发请求里
    //   —— 增强仍打在合法端点上，攻击者端点收到 0 个请求
    let events = run_enhancement(&app);
    let legit_requests = legit.requests();
    let attacker_requests = attacker.requests();
    assert_eq!(legit_requests.len(), 1, "增强请求应当打到合法端点");
    assert_eq!(
        attacker_requests.len(),
        0,
        "攻击者端点收到了请求，真实 Key 可能已外发"
    );
    assert!(
        attacker_requests.iter().all(|request| !request
            .authorization
            .as_deref()
            .unwrap_or_default()
            .contains(PROBE_KEY)),
        "真实 Key 绝不能出现在攻击者端点的请求里"
    );
    assert_eq!(
        legit_requests[0].authorization.as_deref(),
        Some(format!("Bearer {PROBE_KEY}").as_str()),
        "合法端点应当收到本机 Key"
    );
    assert!(
        events
            .iter()
            .any(|event| event.get("type").and_then(Value::as_str) == Some("result")),
        "增强流程应当成功完成"
    );

    println!(
        "[P0-2] 恶意导入包 baseUrl={} → 实际端点={}；攻击者端点收到 {} 个请求",
        attacker.base_url,
        config["baseUrl"],
        attacker_requests.len()
    );
}

// ---------------------------------------------------------------- P0-3 探针

#[test]
fn probe_p0_3_export_contains_every_record() {
    let sandbox = ProbeSandbox::new();
    let storage = sandbox.open_storage();
    let total = 600;
    for index in 0..total {
        let record = HistoryRecord {
            id: format!("h{index}"),
            title: format!("标题 {index}"),
            original: "原始提示词".into(),
            enhanced: "增强后的提示词".into(),
            created_at: chrono::Utc::now().to_rfc3339(),
            model: "deepseek-chat".into(),
            target: "豆包".into(),
            delivery_status: None,
            enhancement_level: None,
            prompt_version: None,
            pinned: false,
        };
        storage.save_history(&record).expect("写入历史记录");
    }

    let app = build_probe_app(Arc::new(storage));
    let webview = open_webview(&app);
    let export_path = sandbox.dir.join("probe-export.zip");
    let exported = invoke(
        &webview,
        "export_data",
        json!({ "path": export_path.to_string_lossy() }),
    )
    .expect("导出数据")
    .deserialize::<u64>()
    .expect("反序列化导出条数");
    assert_eq!(
        exported, total as u64,
        "导出条数必须等于总条数（此前会被 LIMIT 500 静默截断）"
    );

    // 再把导出包读回来核对条数
    let file = std::fs::File::open(&export_path).expect("打开导出包");
    let mut archive = zip::ZipArchive::new(file).expect("读取导出包");
    let mut history_json = String::new();
    archive
        .by_name("history.json")
        .expect("导出包内应有 history.json")
        .read_to_string(&mut history_json)
        .expect("读取 history.json");
    let items: Vec<Value> = serde_json::from_str(&history_json).expect("解析 history.json");
    assert_eq!(items.len(), total, "导出包里的历史记录条数必须等于总条数");

    println!(
        "[P0-3] 历史 {total} 条 → 导出 {exported} 条，导出包内 {} 条",
        items.len()
    );
}
