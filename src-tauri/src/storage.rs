use crate::models::{AppSettings, HistoryRecord, ProviderConfig};
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
            "INSERT INTO history(id,title,original,enhanced,model,target,created_at,delivery_status,enhancement_level,prompt_version)
             VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)",
            params![record.id, record.title, record.original, record.enhanced, record.model, record.target, record.created_at, record.delivery_status, record.enhancement_level, record.prompt_version],
        ).map_err(|error| format!("无法保存历史记录：{error}"))?;
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
                "SELECT id,title,original,enhanced,created_at,model,target,delivery_status,enhancement_level,prompt_version FROM history
             WHERE (?1 = '%%' OR title LIKE ?1 OR original LIKE ?1 OR enhanced LIKE ?1)
             ORDER BY created_at DESC LIMIT 500",
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
        let settings: AppSettings = read_archive_json(&mut archive, "settings.json")?;
        let provider: ProviderConfig = read_archive_json(&mut archive, "provider.json")?;
        drop(archive);

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
        self.enforce_history_capacity(MAX_HISTORY_CONTENT_BYTES)?;
        Ok(())
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
                    "SELECT COALESCE(SUM(LENGTH(title) + LENGTH(original) + LENGTH(enhanced)), 0) FROM history",
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

    /// 每个测试使用独立临时目录与独立凭据服务名，绝不触碰真实的 PromptCraft 凭据。
    fn test_storage() -> Storage {
        let dir = std::env::temp_dir().join(format!("PromptCraft-test-{}", uuid::Uuid::new_v4()));
        let service = format!("PromptCraftTest-{}", uuid::Uuid::new_v4());
        Storage::open(&dir, &service).expect("open test storage")
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
        storage.save_app_settings(&AppSettings::default()).unwrap();
        storage.clear_all_data().unwrap();
        assert!(storage.list_history(None).unwrap().is_empty());
        let settings = storage.app_settings().unwrap();
        assert!(
            settings
                .profile_rules
                .as_array()
                .map(|items| items.is_empty())
                .unwrap_or(true)
        );
        // 凭据已从 Windows 凭据管理器删除
        assert!(storage.api_key().is_err());
    }

    #[test]
    fn export_and_stored_config_never_contain_api_key() {
        let storage = test_storage();
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
        {
            let connection = storage.connection.lock().unwrap();
            connection
                .execute("UPDATE history SET pinned=1", [])
                .unwrap();
        }
        let deleted = storage.enforce_history_capacity(1).unwrap();
        assert_eq!(deleted, 0);
        assert_eq!(storage.list_history(None).unwrap().len(), 3);
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
