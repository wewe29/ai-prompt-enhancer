use crate::models::{AppSettings, HistoryRecord, ProviderConfig, default_history_mb};
use chrono::{Duration, Utc};
use keyring::Entry;
use rusqlite::{Connection, params};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::{Read, Write},
    path::Path,
    sync::Mutex,
};
use tauri::{AppHandle, Manager};
use zip::{CompressionMethod, ZipArchive, ZipWriter, write::SimpleFileOptions};

const SERVICE: &str = "PromptCraft";
const API_KEY_ACCOUNT: &str = "deepseek-api-key";
const DB_KEY_ACCOUNT: &str = "database-key";
const ARCHIVE_SCHEMA_VERSION: u8 = 1;
const MAX_ARCHIVE_BYTES: u64 = 100 * 1024 * 1024;
// 导入包单个条目解压后的硬上限：防止声明大小造假或极端压缩比导致的压缩包炸弹
const MAX_ARCHIVE_ENTRY_BYTES: u64 = 8 * 1024 * 1024;
// 历史记录文本内容总量上限（title+original+enhanced 的字节数），超出后按最旧未置顶清理
const MAX_HISTORY_CONTENT_BYTES: i64 = 64 * 1024 * 1024;

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ArchiveManifest {
    schema_version: u8,
    product: String,
    exported_at: String,
}

pub struct Storage {
    connection: Mutex<Connection>,
    credential_service: String,
}

impl Storage {
    pub fn initialize(app: &AppHandle) -> Result<Self, String> {
        let app_dir = app
            .path()
            .app_data_dir()
            .map_err(|error| format!("无法确定应用数据目录：{error}"))?;
        Self::open(&app_dir, SERVICE)
    }

    pub(crate) fn open(app_dir: &Path, credential_service: &str) -> Result<Self, String> {
        fs::create_dir_all(app_dir).map_err(|error| format!("无法创建应用数据目录：{error}"))?;
        let database_path = app_dir.join("promptcraft.db");
        let key = get_or_create_database_key(credential_service)?;
        let connection = Connection::open(&database_path)
            .map_err(|error| format!("无法打开本地数据库：{error}"))?;
        connection.execute_batch(&format!(
            "PRAGMA key = \"x'{}'\"; PRAGMA cipher_memory_security = ON; PRAGMA foreign_keys = ON; PRAGMA journal_mode = WAL;",
            key
        )).map_err(|error| format!("无法解密本地数据库：{error}"))?;
        connection
            .execute_batch(
                "
            CREATE TABLE IF NOT EXISTS app_settings (
              key TEXT PRIMARY KEY,
              value_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS history (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              original TEXT NOT NULL,
              enhanced TEXT NOT NULL,
              model TEXT NOT NULL,
              target TEXT NOT NULL,
              created_at TEXT NOT NULL,
              pinned INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS history_created_at_idx ON history(created_at DESC);
            CREATE TABLE IF NOT EXISTS usage_records (
              id TEXT PRIMARY KEY,
              model_id TEXT NOT NULL,
              input_tokens INTEGER NOT NULL,
              output_tokens INTEGER NOT NULL,
              estimated_cost REAL NOT NULL,
              duration_ms INTEGER NOT NULL,
              status TEXT NOT NULL,
              error_code TEXT,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS usage_created_at_idx ON usage_records(created_at DESC);
            ",
            )
            .map_err(|error| format!("无法初始化本地数据库：{error}"))?;
        let version: i64 = connection
            .query_row("PRAGMA user_version", [], |row| row.get(0))
            .unwrap_or(0);
        if version < 2 {
            connection
                .execute_batch(
                    "ALTER TABLE history ADD COLUMN delivery_status TEXT;
                     ALTER TABLE history ADD COLUMN enhancement_level TEXT;
                     ALTER TABLE history ADD COLUMN prompt_version TEXT;
                     PRAGMA user_version = 2;",
                )
                .map_err(|error| format!("无法迁移数据库结构：{error}"))?;
        }
        let storage = Self {
            connection: Mutex::new(connection),
            credential_service: credential_service.to_string(),
        };
        storage.housekeeping()?;
        Ok(storage)
    }

    pub fn provider_config(&self) -> Result<ProviderConfig, String> {
        let connection = self
            .connection
            .lock()
            .map_err(|_| "数据库锁已损坏".to_string())?;
        let stored: Option<String> = connection
            .query_row(
                "SELECT value_json FROM app_settings WHERE key = 'provider.deepseek'",
                [],
                |row| row.get(0),
            )
            .ok();
        let mut config: ProviderConfig = stored
            .and_then(|json| serde_json::from_str(&json).ok())
            .unwrap_or_default();
        let legacy_v4_id = config.v4_flash_model_id.eq_ignore_ascii_case("V4-Flash")
            || config.v4_flash_model_id.eq_ignore_ascii_case("v4-flash");
        if legacy_v4_id {
            config.v4_flash_model_id = "deepseek-v4-flash".into();
            let json = serde_json::to_string(&config).map_err(|error| error.to_string())?;
            connection.execute(
                "INSERT INTO app_settings(key, value_json, updated_at) VALUES('provider.deepseek', ?1, ?2)
                 ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                params![json, Utc::now().to_rfc3339()],
            ).map_err(|error| format!("无法迁移 V4-Flash 模型配置：{error}"))?;
        }
        config.has_api_key = self.api_key().is_ok();
        config.api_key = None;
        Ok(config)
    }

    pub fn save_provider_config(&self, config: &ProviderConfig) -> Result<(), String> {
        if let Some(api_key) = config.api_key.as_ref().filter(|key| !key.trim().is_empty()) {
            Entry::new(&self.credential_service, API_KEY_ACCOUNT)
                .map_err(|error| format!("无法访问 Windows 凭据管理器：{error}"))?
                .set_password(api_key.trim())
                .map_err(|error| format!("无法保存 API Key：{error}"))?;
        }
        let mut safe_config = config.clone();
        safe_config.api_key = None;
        safe_config.has_api_key = self.api_key().is_ok();
        let json = serde_json::to_string(&safe_config).map_err(|error| error.to_string())?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| "数据库锁已损坏".to_string())?;
        connection.execute(
            "INSERT INTO app_settings(key, value_json, updated_at) VALUES('provider.deepseek', ?1, ?2)
             ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            params![json, Utc::now().to_rfc3339()],
        ).map_err(|error| format!("无法保存供应商配置：{error}"))?;
        Ok(())
    }

    pub fn api_key(&self) -> Result<String, String> {
        Entry::new(&self.credential_service, API_KEY_ACCOUNT)
            .map_err(|error| format!("无法访问 Windows 凭据管理器：{error}"))?
            .get_password()
            .map_err(|_| "尚未配置 DeepSeek API Key".to_string())
    }

    pub fn save_history(&self, record: &HistoryRecord) -> Result<(), String> {
        let connection = self
            .connection
            .lock()
            .map_err(|_| "数据库锁已损坏".to_string())?;
        connection.execute(
            "INSERT INTO history(id,title,original,enhanced,model,target,created_at,delivery_status,enhancement_level,prompt_version,pinned)
             VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11)
             ON CONFLICT(id) DO UPDATE SET title=excluded.title,original=excluded.original,enhanced=excluded.enhanced,
               model=excluded.model,target=excluded.target,created_at=excluded.created_at,
               delivery_status=excluded.delivery_status,enhancement_level=excluded.enhancement_level,
               prompt_version=excluded.prompt_version,pinned=excluded.pinned",
            params![record.id, record.title, record.original, record.enhanced, record.model, record.target, record.created_at, record.delivery_status, record.enhancement_level, record.prompt_version, record.pinned],
        ).map_err(|error| format!("无法保存历史记录：{error}"))?;
        Ok(())
    }

    /// 切换置顶状态。置顶记录永不被时间清理与容量清理删除。
    pub fn set_history_pinned(&self, id: &str, pinned: bool) -> Result<(), String> {
        let connection = self
            .connection
            .lock()
            .map_err(|_| "数据库锁已损坏".to_string())?;
        let changed = connection
            .execute(
                "UPDATE history SET pinned=?2 WHERE id=?1",
                params![id, pinned],
            )
            .map_err(|error| format!("无法更新置顶状态：{error}"))?;
        if changed == 0 {
            return Err("记录不存在".into());
        }
        Ok(())
    }

    pub fn list_history(&self, query: Option<&str>) -> Result<Vec<HistoryRecord>, String> {
        let connection = self
            .connection
            .lock()
            .map_err(|_| "数据库锁已损坏".to_string())?;
        let pattern = format!("%{}%", query.unwrap_or_default());
        let mut statement = connection
            .prepare(
                "SELECT id,title,original,enhanced,created_at,model,target,delivery_status,enhancement_level,prompt_version,pinned FROM history
             WHERE (?1 = '%%' OR title LIKE ?1 OR original LIKE ?1 OR enhanced LIKE ?1)
             ORDER BY pinned DESC, created_at DESC LIMIT 500",
            )
            .map_err(|error| error.to_string())?;
        let rows = statement
            .query_map([pattern], |row| {
                Ok(HistoryRecord {
                    id: row.get(0)?,
                    title: row.get(1)?,
                    original: row.get(2)?,
                    enhanced: row.get(3)?,
                    created_at: row.get(4)?,
                    model: row.get(5)?,
                    target: row.get(6)?,
                    delivery_status: row.get(7)?,
                    enhancement_level: row.get(8)?,
                    prompt_version: row.get(9)?,
                    pinned: row.get::<_, i64>(10)? != 0,
                })
            })
            .map_err(|error| error.to_string())?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|error| error.to_string())
    }

    pub fn delete_history(&self, id: &str) -> Result<(), String> {
        let connection = self
            .connection
            .lock()
            .map_err(|_| "数据库锁已损坏".to_string())?;
        connection
            .execute("DELETE FROM history WHERE id=?1", [id])
            .map_err(|error| error.to_string())?;
        Ok(())
    }

    pub fn app_settings(&self) -> Result<AppSettings, String> {
        let connection = self
            .connection
            .lock()
            .map_err(|_| "数据库锁已损坏".to_string())?;
        let stored: Option<String> = connection
            .query_row(
                "SELECT value_json FROM app_settings WHERE key = 'ui.local'",
                [],
                |row| row.get(0),
            )
            .ok();
        Ok(stored
            .and_then(|json| serde_json::from_str(&json).ok())
            .unwrap_or_default())
    }

    pub fn save_app_settings(&self, settings: &AppSettings) -> Result<(), String> {
        let json = serde_json::to_string(settings).map_err(|error| error.to_string())?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| "数据库锁已损坏".to_string())?;
        connection.execute(
            "INSERT INTO app_settings(key,value_json,updated_at) VALUES('ui.local',?1,?2)
             ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
            params![json, Utc::now().to_rfc3339()],
        ).map_err(|error| error.to_string())?;
        Ok(())
    }

    pub fn clear_all_data(&self) -> Result<(), String> {
        let mut connection = self
            .connection
            .lock()
            .map_err(|_| "数据库锁已损坏".to_string())?;
        let transaction = connection
            .transaction()
            .map_err(|error| error.to_string())?;
        transaction
            .execute("DELETE FROM history", [])
            .map_err(|error| error.to_string())?;
        transaction
            .execute("DELETE FROM usage_records", [])
            .map_err(|error| error.to_string())?;
        transaction
            .execute("DELETE FROM app_settings", [])
            .map_err(|error| error.to_string())?;
        transaction.commit().map_err(|error| error.to_string())?;
        drop(connection);
        if let Ok(entry) = Entry::new(&self.credential_service, API_KEY_ACCOUNT) {
            match entry.delete_credential() {
                Ok(()) => {}
                // 凭据本就不存在（用户从未配置过 Key）视为清理完成
                Err(keyring::Error::NoEntry) => {}
                Err(error) => {
                    return Err(format!(
                        "本地数据已清空，但删除 Windows 凭据失败：{error}；请打开凭据管理器手动确认。"
                    ));
                }
            }
        }
        Ok(())
    }

    pub fn export_data(&self, path: &Path) -> Result<usize, String> {
        if path
            .extension()
            .and_then(|value| value.to_str())
            .map(|value| value.eq_ignore_ascii_case("zip"))
            != Some(true)
        {
            return Err("导出文件必须使用 .zip 扩展名".into());
        }
        let history = self.list_history(None)?;
        let settings = self.app_settings()?;
        let provider = self.provider_config()?;
        let manifest = ArchiveManifest {
            schema_version: ARCHIVE_SCHEMA_VERSION,
            product: "PromptCraft".into(),
            exported_at: Utc::now().to_rfc3339(),
        };
        let file = fs::File::create(path).map_err(|error| format!("无法创建导出文件：{error}"))?;
        let mut writer = ZipWriter::new(file);
        let options = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);
        for (name, value) in [
            (
                "manifest.json",
                serde_json::to_vec_pretty(&manifest).map_err(|error| error.to_string())?,
            ),
            (
                "history.json",
                serde_json::to_vec_pretty(&history).map_err(|error| error.to_string())?,
            ),
            (
                "settings.json",
                serde_json::to_vec_pretty(&settings).map_err(|error| error.to_string())?,
            ),
            (
                "provider.json",
                serde_json::to_vec_pretty(&provider).map_err(|error| error.to_string())?,
            ),
        ] {
            writer
                .start_file(name, options)
                .map_err(|error| format!("无法写入导出包：{error}"))?;
            writer
                .write_all(&value)
                .map_err(|error| format!("无法写入导出包：{error}"))?;
        }
        writer
            .finish()
            .map_err(|error| format!("无法完成导出：{error}"))?;
        Ok(history.len())
    }

    pub fn import_data(&self, path: &Path) -> Result<usize, String> {
        let metadata = fs::metadata(path).map_err(|error| format!("无法读取导入文件：{error}"))?;
        if metadata.len() > MAX_ARCHIVE_BYTES {
            return Err("导入包不能超过 100 MB".into());
        }
        let file = fs::File::open(path).map_err(|error| format!("无法打开导入文件：{error}"))?;
        let mut archive =
            ZipArchive::new(file).map_err(|_| "导入文件不是有效的 ZIP 包".to_string())?;
        if archive.len() > 8 {
            return Err("导入包包含过多文件".into());
        }
        let allowed = [
            "manifest.json",
            "history.json",
            "settings.json",
            "provider.json",
        ];
        let mut total_uncompressed = 0_u64;
        for index in 0..archive.len() {
            let entry = archive.by_index(index).map_err(|error| error.to_string())?;
            let enclosed = entry
                .enclosed_name()
                .and_then(|path| path.to_str().map(str::to_owned))
                .ok_or_else(|| "导入包包含不安全路径".to_string())?;
            if !allowed.contains(&enclosed.as_str()) {
                return Err(format!("导入包包含不支持的内容：{enclosed}"));
            }
            total_uncompressed = total_uncompressed.saturating_add(entry.size());
        }
        if total_uncompressed > MAX_ARCHIVE_BYTES {
            return Err("导入包解压后的内容超过 100 MB".into());
        }

        let manifest: ArchiveManifest = read_archive_json(&mut archive, "manifest.json")?;
        if manifest.schema_version != ARCHIVE_SCHEMA_VERSION || manifest.product != "PromptCraft" {
            return Err("导入包版本或产品标识不兼容".into());
        }
        let history: Vec<HistoryRecord> = read_archive_json(&mut archive, "history.json")?;
        let mut settings: AppSettings = read_archive_json(&mut archive, "settings.json")?;
        let mut provider: ProviderConfig = read_archive_json(&mut archive, "provider.json")?;
        drop(archive);

        // 导入包是外部不可信输入，绝不允许其改写本机凭据。
        // 纵深防御：即使 ProviderConfig 已加 skip_deserializing，这里再显式清空一次，
        // 防止将来有人误删该 serde 属性后重新打开凭据投毒路径。
        provider.api_key = None;
        // 同样不让导入包把容量上限设成 0/负数导致容量清理失效。
        if settings.max_history_mb <= 0 {
            settings.max_history_mb = default_history_mb();
        }

        let mut imported = 0;
        for mut record in history.into_iter().take(5_000) {
            record.id = uuid::Uuid::new_v4().to_string();
            self.save_history(&record)?;
            imported += 1;
        }
        self.save_app_settings(&settings)?;
        self.save_provider_config(&provider)?;
        Ok(imported)
    }

    pub fn record_usage(
        &self,
        model: &str,
        input_tokens: u64,
        output_tokens: u64,
        cost: f64,
        duration_ms: u64,
        status: &str,
        error_code: Option<&str>,
    ) -> Result<f64, String> {
        let connection = self
            .connection
            .lock()
            .map_err(|_| "数据库锁已损坏".to_string())?;
        connection.execute(
            "INSERT INTO usage_records(id,model_id,input_tokens,output_tokens,estimated_cost,duration_ms,status,error_code,created_at)
             VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9)",
            params![uuid::Uuid::new_v4().to_string(), model, input_tokens, output_tokens, cost, duration_ms, status, error_code, Utc::now().to_rfc3339()],
        ).map_err(|error| error.to_string())?;
        let month_prefix = Utc::now().format("%Y-%m").to_string();
        connection.query_row(
            "SELECT COALESCE(SUM(estimated_cost),0) FROM usage_records WHERE substr(created_at,1,7)=?1 AND status='success'",
            [month_prefix], |row| row.get(0),
        ).map_err(|error| error.to_string())
    }

    pub fn housekeeping(&self) -> Result<(), String> {
        let cutoff = (Utc::now() - Duration::days(90)).to_rfc3339();
        {
            let connection = self
                .connection
                .lock()
                .map_err(|_| "数据库锁已损坏".to_string())?;
            connection
                .execute(
                    "DELETE FROM history WHERE pinned=0 AND created_at < ?1",
                    [&cutoff],
                )
                .map_err(|error| error.to_string())?;
            connection
                .execute("DELETE FROM usage_records WHERE created_at < ?1", [&cutoff])
                .map_err(|error| error.to_string())?;
        }
        self.enforce_history_capacity(self.configured_history_limit())?;
        Ok(())
    }

    /// 容量上限来自用户设置（MB），并夹在 1..=1024 之间防止误设 0 或超大值。
    /// 设置读取失败时回落到默认 64 MB，绝不因为读取失败而跳过容量清理。
    fn configured_history_limit(&self) -> i64 {
        let fallback = MAX_HISTORY_CONTENT_BYTES;
        let settings = self.app_settings().unwrap_or_default();
        let mb = settings.max_history_mb;
        if mb <= 0 {
            return fallback;
        }
        mb.min(1024) * 1024 * 1024
    }

    /// 历史文本内容总量超过上限时，按最旧未置顶优先分批删除，直到达标或只剩置顶记录。
    /// 返回删除的记录数；置顶记录永不被容量清理删除。
    pub fn enforce_history_capacity(&self, max_content_bytes: i64) -> Result<u32, String> {
        let connection = self
            .connection
            .lock()
            .map_err(|_| "数据库锁已损坏".to_string())?;
        let mut deleted = 0_u32;
        loop {
            let total: i64 = connection
                .query_row(
                    // SQLite 的 LENGTH() 对 TEXT 返回**字符数**而非字节数。
                    // 强制转 BLOB 后再取长度，才能得到与 MAX_HISTORY_CONTENT_BYTES
                    // 一致的字节语义（中文 UTF-8 为 3 字节/字，否则实际占用约为名义值的 3 倍）。
                    "SELECT COALESCE(SUM(LENGTH(CAST(title AS BLOB)) + LENGTH(CAST(original AS BLOB)) + LENGTH(CAST(enhanced AS BLOB))), 0) FROM history",
                    [],
                    |row| row.get(0),
                )
                .map_err(|error| error.to_string())?;
            if total <= max_content_bytes {
                break;
            }
            let removed = connection
                .execute(
                    "DELETE FROM history WHERE id = (SELECT id FROM history WHERE pinned=0 ORDER BY created_at ASC LIMIT 1)",
                    [],
                )
                .map_err(|error| error.to_string())?;
            if removed == 0 {
                break;
            }
            deleted += removed as u32;
        }
        Ok(deleted)
    }
}

fn read_archive_json<T: for<'de> Deserialize<'de>>(
    archive: &mut ZipArchive<fs::File>,
    name: &str,
) -> Result<T, String> {
    let entry = archive
        .by_name(name)
        .map_err(|_| format!("导入包缺少 {name}"))?;
    if entry.size() > MAX_ARCHIVE_ENTRY_BYTES {
        return Err(format!(
            "{name} 解压后超过 {} MB，已拒绝读取（疑似压缩包炸弹）",
            MAX_ARCHIVE_ENTRY_BYTES / 1024 / 1024
        ));
    }
    // 声明大小可以造假：按硬上限截断读取，超出即拒绝，避免内存被撑爆
    let mut limited = entry.take(MAX_ARCHIVE_ENTRY_BYTES + 1);
    let mut bytes = Vec::new();
    limited
        .read_to_end(&mut bytes)
        .map_err(|error| format!("无法读取 {name}：{error}"))?;
    if bytes.len() as u64 > MAX_ARCHIVE_ENTRY_BYTES {
        return Err(format!(
            "{name} 实际内容超过声明大小，已拒绝读取（疑似压缩包炸弹）"
        ));
    }
    serde_json::from_slice(&bytes).map_err(|_| format!("{name} 内容格式无效"))
}

fn get_or_create_database_key(credential_service: &str) -> Result<String, String> {
    let entry = Entry::new(credential_service, DB_KEY_ACCOUNT)
        .map_err(|error| format!("无法访问 Windows 凭据管理器：{error}"))?;
    if let Ok(existing) = entry.get_password() {
        return Ok(existing);
    }
    let bytes: [u8; 32] = rand::random();
    let key = hex::encode(bytes);
    entry
        .set_password(&key)
        .map_err(|error| format!("无法保存数据库密钥：{error}"))?;
    Ok(key)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::{ops::Deref, path::PathBuf};

    /// 测试沙箱守卫：持有独立临时目录与独立凭据服务名，并在 Drop 时清理二者。
    ///
    /// 早期实现只在创建时保证隔离、从不清理，导致每跑一次 `cargo test` 就往**真实**
    /// Windows 凭据管理器里追加 `PromptCraftTest-<uuid>` 条目、并在 `%TEMP%` 留下目录。
    /// 实测一度积累 73 个测试库密钥 + 7 个测试 API Key + 18 个目录。
    /// 合法的 provider.json（无 apiKey 字段），供导入类测试复用。
    const VALID_PROVIDER: &str = r#"{"baseUrl":"https://api.deepseek.com","hasApiKey":false,"defaultModel":"deepseek-chat","v4FlashModelId":"deepseek-v4-flash","inputPrice":0.001,"outputPrice":0.002}"#;

    /// 合法的 settings.json 最小集。
    const VALID_SETTINGS: &str = r#"{"clearClipboard":false}"#;

    struct TestSandbox {
        storage: Storage,
        dir: PathBuf,
    }

    impl Deref for TestSandbox {
        type Target = Storage;
        fn deref(&self) -> &Storage {
            &self.storage
        }
    }

    impl Drop for TestSandbox {
        fn drop(&mut self) {
            // 提前释放 SQLite 连接，让 Windows 能真正删除临时目录里的 .db/-wal/-shm
            self.storage.connection = Mutex::new(Connection::open_in_memory().expect("in-memory"));
            let _ = fs::remove_dir_all(&self.dir);
            // 用**进程级全局锁**把凭据删除串行化：Windows 凭据管理器在并发删改时
            // 会静默失败（keyring 既不重试也不报错），实测串行跑 12 个 storage 测试
            // delta=0，而默认并行跑每次残留 1~3 条。串行化 + 退避重试后归零。
            let _guard = test_credential_lock().lock();
            delete_test_credential(&self.storage.credential_service, API_KEY_ACCOUNT);
            delete_test_credential(&self.storage.credential_service, DB_KEY_ACCOUNT);
        }
    }

    fn test_credential_lock() -> &'static Mutex<()> {
        static LOCK: Mutex<()> = Mutex::new(());
        &LOCK
    }

    /// 删除单个测试凭据，退避重试以对抗凭据管理器的瞬时占用。
    fn delete_test_credential(service: &str, account: &str) {
        for attempt in 0..8 {
            let Ok(entry) = Entry::new(service, account) else {
                return;
            };
            match entry.delete_credential() {
                Ok(()) | Err(keyring::Error::NoEntry) => return,
                Err(_) => std::thread::sleep(std::time::Duration::from_millis(10 * (attempt + 1))),
            }
        }
    }

    /// 每个测试使用独立临时目录与独立凭据服务名，绝不触碰真实的 PromptCraft 凭据；
    /// 目录与凭据条目由 `TestSandbox` 在测试结束时自动清理。
    fn test_storage() -> TestSandbox {
        let dir = std::env::temp_dir().join(format!("PromptCraft-test-{}", uuid::Uuid::new_v4()));
        let service = format!("PromptCraftTest-{}", uuid::Uuid::new_v4());
        let storage = Storage::open(&dir, &service).expect("open test storage");
        TestSandbox { storage, dir }
    }

    fn record(id: &str, enhanced: &str, age_days: u64) -> HistoryRecord {
        HistoryRecord {
            id: id.into(),
            title: format!("t-{id}"),
            original: "原始提示词".into(),
            enhanced: enhanced.into(),
            created_at: (Utc::now() - Duration::days(age_days as i64)).to_rfc3339(),
            model: "deepseek-chat".into(),
            target: "豆包".into(),
            delivery_status: None,
            enhancement_level: None,
            prompt_version: None,
            pinned: false,
        }
    }

    fn write_archive(path: &Path, entries: &[(&str, Vec<u8>)]) {
        let file = fs::File::create(path).expect("create archive");
        let mut writer = ZipWriter::new(file);
        for (name, bytes) in entries {
            writer
                .start_file(*name, SimpleFileOptions::default())
                .expect("start entry");
            writer.write_all(bytes).expect("write entry");
        }
        writer.finish().expect("finish archive");
    }

    fn manifest_json() -> Vec<u8> {
        json!({"schemaVersion": ARCHIVE_SCHEMA_VERSION, "product": "PromptCraft", "exportedAt": "2026-01-01T00:00:00Z"})
            .to_string()
            .into_bytes()
    }

    #[test]
    fn history_roundtrip_and_single_delete() {
        let storage = test_storage();
        storage.save_history(&record("a", "增强A", 0)).unwrap();
        storage.save_history(&record("b", "增强B", 0)).unwrap();
        assert_eq!(storage.list_history(None).unwrap().len(), 2);
        storage.delete_history("a").unwrap();
        let remaining = storage.list_history(None).unwrap();
        assert_eq!(remaining.len(), 1);
        assert_eq!(remaining[0].id, "b");
    }

    #[test]
    fn clear_all_wipes_history_settings_usage_and_credential() {
        let storage = test_storage();
        storage.save_history(&record("a", "增强A", 0)).unwrap();
        storage
            .record_usage("deepseek-chat", 10, 20, 0.001, 5, "success", None)
            .unwrap();
        // 关键：写入**非默认**设置再清空。原实现存的是 AppSettings::default()，
        // 而 default 的 profile_rules 本身就是空数组，导致"设置已清空"断言恒真。
        storage
            .save_app_settings(&AppSettings {
                clear_clipboard: true,
                profile_enabled: false,
                custom_target_url: "https://example.com".into(),
                monthly_warning_limit: 3.5,
                monthly_limit: 7.25,
                max_history_mb: 128,
                profile_rules: json!([{"id": "r1", "kind": "goal"}]),
            })
            .unwrap();
        storage.clear_all_data().unwrap();
        assert!(storage.list_history(None).unwrap().is_empty());
        // 清空后应回落到默认值：所有非默认字段都必须变回默认，
        // 否则说明 app_settings 行没被删除。
        let settings = storage.app_settings().unwrap();
        assert!(!settings.clear_clipboard, "clear_clipboard 应回落为 false");
        assert!(
            settings.custom_target_url.is_empty(),
            "custom_target_url 应被清空"
        );
        assert_eq!(settings.monthly_warning_limit, 8.0);
        assert_eq!(settings.monthly_limit, 10.0);
        assert_eq!(settings.max_history_mb, default_history_mb());
        assert!(
            settings
                .profile_rules
                .as_array()
                .map(|items| items.is_empty())
                .unwrap_or(true),
            "profile_rules 应回落为空"
        );
        // 凭据已从 Windows 凭据管理器删除（加锁读取，避免与并发测试的删除竞争）
        assert!({
            let _guard = test_credential_lock().lock();
            storage.api_key().is_err()
        });
    }

    #[test]
    fn import_never_plants_api_key_into_credential_store() {
        // 回归测试（凭据投毒）：攻击者在 provider.json 里植入 apiKey 字段。
        // 该字段曾经能被反序列化进 ProviderConfig，再经 save_provider_config
        // 写入真实 Windows 凭据管理器，把用户的 Key 偷换成攻击者的。
        // 现在必须被彻底忽略。
        let storage = test_storage();
        // 先给沙箱写入一个"用户自己的 Key"，导入后必须原样保留。
        //
        // 凭据的**写入**同样要加锁：Windows 凭据管理器在多个测试并发删改时会
        // 静默失败，只在 Drop 里给删除加锁是不够的——别的测试的删除可能正好
        // 落在本测试的 set 与 get 之间，导致 api_key() 读不到刚写入的值。
        // 这会让本用例在并行运行时约 3/4 概率假失败。
        {
            let _guard = test_credential_lock().lock();
            storage
                .save_provider_config(&ProviderConfig {
                    api_key: Some("sk-users-own-key-000001".into()),
                    ..Default::default()
                })
                .unwrap();
            assert!(storage.api_key().is_ok(), "写入用户 Key 后应可读回");
        }

        let provider = r#"{"baseUrl":"https://api.deepseek.com","hasApiKey":true,"defaultModel":"deepseek-chat","v4FlashModelId":"deepseek-v4-flash","inputPrice":0.001,"outputPrice":0.002,"apiKey":"sk-attacker-planted-key-000001"}"#;
        let path =
            std::env::temp_dir().join(format!("PromptCraft-poison-{}", uuid::Uuid::new_v4()));
        write_archive(
            &path,
            &[
                ("manifest.json", manifest_json()),
                ("history.json", b"[]".to_vec()),
                ("settings.json", VALID_SETTINGS.as_bytes().to_vec()),
                ("provider.json", provider.as_bytes().to_vec()),
            ],
        );
        // 导入与后续读取同样整体加锁（import_data 内部会写凭据配置）
        let stored_key = {
            let _guard = test_credential_lock().lock();
            storage.import_data(&path).unwrap();

            // 关键断言：凭据管理器里的 Key 未被导入包改写
            storage.api_key().expect("用户原有 Key 应当保留")
        };
        assert_eq!(
            stored_key, "sk-users-own-key-000001",
            "导入包不得改写本机 API Key"
        );
        assert!(
            !stored_key.contains("attacker"),
            "攻击者 Key 不得进入凭据管理器"
        );
        let _ = fs::remove_file(&path);
    }

    #[test]
    fn import_cannot_disable_history_capacity_limit() {
        // 导入包不得把容量上限设成 0/负数，否则容量清理永久失效
        let storage = test_storage();
        let settings = r#"{"clearClipboard":false,"monthlyLimit":10,"maxHistoryMb":0}"#;
        let path = std::env::temp_dir().join(format!("PromptCraft-cap-{}", uuid::Uuid::new_v4()));
        write_archive(
            &path,
            &[
                ("manifest.json", manifest_json()),
                ("history.json", b"[]".to_vec()),
                ("settings.json", settings.as_bytes().to_vec()),
                ("provider.json", VALID_PROVIDER.as_bytes().to_vec()),
            ],
        );
        storage.import_data(&path).unwrap();
        assert_eq!(
            storage.app_settings().unwrap().max_history_mb,
            default_history_mb(),
            "非法的 0 应被回落为默认值"
        );
        let _ = fs::remove_file(&path);
    }

    #[test]
    fn export_and_stored_config_never_contain_api_key() {
        let storage = test_storage();
        // 同 import_never_plants_*：凭据写入与紧随其后的读取必须整体加锁，
        // 否则并发测试的凭据删除会落在 set 与 get 之间，造成假失败。
        {
            let _guard = test_credential_lock().lock();
            storage
                .save_provider_config(&ProviderConfig {
                    api_key: Some("sk-secret-test-key-123456".into()),
                    ..Default::default()
                })
                .unwrap();
            // 数据库内的供应商配置不含 Key 明文
            let stored = storage.provider_config().unwrap();
            assert!(stored.api_key.is_none());
            assert!(stored.has_api_key);
        }
        // 导出包同样不含 Key 明文
        let path =
            std::env::temp_dir().join(format!("PromptCraft-export-{}.zip", uuid::Uuid::new_v4()));
        storage.export_data(&path).unwrap();
        let mut archive = ZipArchive::new(fs::File::open(&path).unwrap()).unwrap();
        let mut provider_json = String::new();
        archive
            .by_name("provider.json")
            .unwrap()
            .read_to_string(&mut provider_json)
            .unwrap();
        assert!(!provider_json.contains("sk-secret-test-key-123456"));
        let provider_value: serde_json::Value = serde_json::from_str(&provider_json).unwrap();
        assert_eq!(provider_value["hasApiKey"], true);
        let _ = fs::remove_file(&path);
    }

    #[test]
    fn import_rejects_path_traversal() {
        let path = std::env::temp_dir().join(format!(
            "PromptCraft-traversal-{}.zip",
            uuid::Uuid::new_v4()
        ));
        write_archive(
            &path,
            &[
                ("manifest.json", manifest_json()),
                ("../evil.json", b"{}".to_vec()),
            ],
        );
        let storage = test_storage();
        let error = storage.import_data(&path).unwrap_err();
        assert!(error.contains("不安全路径"), "{error}");
        let _ = fs::remove_file(&path);
    }

    #[test]
    fn import_rejects_oversized_entry() {
        // 声明解压大小 9 MB，超过单条目 8 MB 硬上限（压缩后极小，模拟压缩包炸弹）
        let history = json!([{
            "id": "h1",
            "title": "t",
            "original": "o",
            "enhanced": "1".repeat(9 * 1024 * 1024),
            "created_at": "2026-01-01T00:00:00Z",
            "model": "m",
            "target": "豆包",
        }]);
        let path =
            std::env::temp_dir().join(format!("PromptCraft-bomb-{}.zip", uuid::Uuid::new_v4()));
        write_archive(
            &path,
            &[
                ("manifest.json", manifest_json()),
                ("history.json", history.to_string().into_bytes()),
            ],
        );
        let storage = test_storage();
        let error = storage.import_data(&path).unwrap_err();
        assert!(error.contains("压缩包炸弹"), "{error}");
        let _ = fs::remove_file(&path);
    }

    #[test]
    fn import_rejects_unknown_entries_and_bad_manifest() {
        let path =
            std::env::temp_dir().join(format!("PromptCraft-unknown-{}.zip", uuid::Uuid::new_v4()));
        write_archive(
            &path,
            &[
                ("manifest.json", manifest_json()),
                ("extra.txt", b"unexpected".to_vec()),
            ],
        );
        let storage = test_storage();
        let error = storage.import_data(&path).unwrap_err();
        assert!(error.contains("不支持的内容"), "{error}");

        let path =
            std::env::temp_dir().join(format!("PromptCraft-manifest-{}.zip", uuid::Uuid::new_v4()));
        write_archive(
            &path,
            &[
                (
                    "manifest.json",
                    json!({"schemaVersion": 99, "product": "SomeoneElse", "exportedAt": "x"})
                        .to_string()
                        .into_bytes(),
                ),
                ("history.json", b"[]".to_vec()),
                ("settings.json", b"{}".to_vec()),
                ("provider.json", b"{}".to_vec()),
            ],
        );
        let error = storage.import_data(&path).unwrap_err();
        assert!(error.contains("不兼容"), "{error}");
    }

    #[test]
    fn import_accepts_legacy_minimal_archive() {
        // 旧版本导出包：provider 缺 models 字段、history 缺交付字段、settings 缺大部分字段
        let provider = r#"{"baseUrl":"https://api.deepseek.com","hasApiKey":false,"defaultModel":"deepseek-chat","v4FlashModelId":"deepseek-v4-flash","inputPrice":0.001,"outputPrice":0.002}"#;
        let history = r#"[{"id":"old1","title":"旧记录","original":"旧原文","enhanced":"旧增强","createdAt":"2025-01-01T00:00:00Z","model":"deepseek-chat","target":"豆包"}]"#;
        let settings = r#"{"clearClipboard":false,"monthlyLimit":10}"#;
        let path =
            std::env::temp_dir().join(format!("PromptCraft-legacy-{}.zip", uuid::Uuid::new_v4()));
        write_archive(
            &path,
            &[
                ("manifest.json", manifest_json()),
                ("history.json", history.as_bytes().to_vec()),
                ("settings.json", settings.as_bytes().to_vec()),
                ("provider.json", provider.as_bytes().to_vec()),
            ],
        );
        let storage = test_storage();
        let imported = storage.import_data(&path).unwrap();
        assert_eq!(imported, 1);
        let items = storage.list_history(None).unwrap();
        assert_eq!(items.len(), 1);
        assert_eq!(items[0].original, "旧原文");
        assert_eq!(items[0].delivery_status, None);
        assert_eq!(
            storage.provider_config().unwrap().default_model,
            "deepseek-chat"
        );
        let _ = fs::remove_file(&path);
    }

    #[test]
    fn capacity_enforcement_deletes_oldest_unpinned_first() {
        let storage = test_storage();
        let payload = "x".repeat(200_000);
        for index in 0..5 {
            storage
                .save_history(&record(&format!("h{index}"), &payload, index as u64))
                .unwrap();
        }
        // 总量约 1MB，上限 500KB：最旧的 3 条应被删除，剩余 2 条且总量达标
        let deleted = storage.enforce_history_capacity(500_000).unwrap();
        assert_eq!(deleted, 3);
        let remaining = storage.list_history(None).unwrap();
        let ids: Vec<&str> = remaining.iter().map(|item| item.id.as_str()).collect();
        assert_eq!(ids, vec!["h0", "h1"]);
        // 再次执行不再删除
        assert_eq!(storage.enforce_history_capacity(500_000).unwrap(), 0);
    }

    #[test]
    fn capacity_enforcement_never_deletes_pinned_records() {
        let storage = test_storage();
        let payload = "x".repeat(200_000);
        for index in 0..3 {
            storage
                .save_history(&record(&format!("p{index}"), &payload, index as u64))
                .unwrap();
        }
        // 走**公开 API** 置顶，而不是裸 SQL。
        // 原实现用 `UPDATE history SET pinned=1` 直接改库，绕过了所有生产入口，
        // 于是"置顶受保护"这条需求在真实使用中根本无法被触发（D-2）。
        for index in 0..3 {
            storage
                .set_history_pinned(&format!("p{index}"), true)
                .unwrap();
        }
        let deleted = storage.enforce_history_capacity(1).unwrap();
        assert_eq!(deleted, 0);
        let remaining = storage.list_history(None).unwrap();
        assert_eq!(remaining.len(), 3);
        assert!(
            remaining.iter().all(|item| item.pinned),
            "三条记录都应仍处于置顶状态"
        );
    }

    #[test]
    fn pinned_flag_survives_roundtrip_and_sorts_first() {
        // 置顶必须能从 save → list 完整往返，并在列表中排在最前
        let storage = test_storage();
        let mut older = record("older", "旧", 5);
        older.title = "旧记录".into();
        let mut newer = record("newer", "新", 0);
        newer.title = "新记录".into();
        storage.save_history(&older).unwrap();
        storage.save_history(&newer).unwrap();
        storage.set_history_pinned("older", true).unwrap();

        let listed = storage.list_history(None).unwrap();
        assert_eq!(listed[0].id, "older", "置顶记录应排最前");
        assert!(listed[0].pinned);
        assert!(!listed[1].pinned);

        // 取消置顶后应恢复时间倒序
        storage.set_history_pinned("older", false).unwrap();
        let listed = storage.list_history(None).unwrap();
        assert_eq!(listed[0].id, "newer");
        assert!(!listed[0].pinned);

        // 对不存在的记录应报错，而不是静默成功
        assert!(storage.set_history_pinned("nope", true).is_err());
    }

    #[test]
    fn history_capacity_limit_follows_user_setting() {
        // 需求"按配置清理"：上限必须来自 AppSettings，而不是硬编码常量
        let storage = test_storage();
        let payload = "x".repeat(200_000);
        for index in 0..4 {
            storage
                .save_history(&record(&format!("c{index}"), &payload, index as u64))
                .unwrap();
        }
        // 把上限设为 1 MB：housekeeping 走设置值，应把约 0.8MB 的 4 条清到达标
        let mut settings = AppSettings::default();
        settings.max_history_mb = 1;
        storage.save_app_settings(&settings).unwrap();
        assert_eq!(storage.configured_history_limit(), 1024 * 1024);
        storage.housekeeping().unwrap();
        let remaining = storage.list_history(None).unwrap();
        let total: usize = remaining
            .iter()
            .map(|item| item.title.len() + item.original.len() + item.enhanced.len())
            .sum();
        assert!(total <= 1024 * 1024, "清理后应回到设置上限内，实际 {total}");

        // 非法值（0/负数）必须回落到默认值，而不是让清理永久失效
        settings.max_history_mb = 0;
        storage.save_app_settings(&settings).unwrap();
        assert_eq!(
            storage.configured_history_limit(),
            MAX_HISTORY_CONTENT_BYTES
        );
        settings.max_history_mb = -5;
        storage.save_app_settings(&settings).unwrap();
        assert_eq!(
            storage.configured_history_limit(),
            MAX_HISTORY_CONTENT_BYTES
        );
    }

    #[test]
    fn capacity_measurement_uses_bytes_not_characters() {
        // SQLite 的 LENGTH() 对 TEXT 返回字符数；常量语义是字节数。
        // 中文内容下两者差约 3 倍，必须确认按字节计量。
        let storage = test_storage();
        let chinese = "中".repeat(100_000); // 100k 字符 = 300k 字节
        let mut item = record("cn", &chinese, 0);
        item.original = "中".repeat(10_000); // 10k 字符 = 30k 字节
        storage.save_history(&item).unwrap();

        let connection = storage.connection.lock().unwrap();
        let chars: i64 = connection
            .query_row(
                "SELECT COALESCE(SUM(LENGTH(title) + LENGTH(original) + LENGTH(enhanced)), 0) FROM history",
                [],
                |row| row.get(0),
            )
            .unwrap();
        let bytes: i64 = connection
            .query_row(
                "SELECT COALESCE(SUM(LENGTH(CAST(title AS BLOB)) + LENGTH(CAST(original AS BLOB)) + LENGTH(CAST(enhanced AS BLOB))), 0) FROM history",
                [],
                |row| row.get(0),
            )
            .unwrap();
        // 字符数约 110k，字节数约 330k，实际应按后者判定
        assert!(chars < 200_000, "字符数口径 = {chars}");
        assert!(bytes > 300_000, "字节数口径 = {bytes}");
        assert!(
            bytes > chars * 2,
            "中文内容下字节口径应显著大于字符口径（{bytes} vs {chars}）"
        );
    }

    #[test]
    fn usage_records_track_month_total() {
        let storage = test_storage();
        let total = storage
            .record_usage("deepseek-chat", 100, 200, 0.003, 10, "success", None)
            .unwrap();
        assert!((total - 0.003).abs() < 1e-9);
        let total = storage
            .record_usage("deepseek-chat", 100, 200, 0.002, 10, "success", None)
            .unwrap();
        assert!((total - 0.005).abs() < 1e-9);
    }
}
