use crate::models::{AppSettings, HistoryRecord, ProviderConfig};
use chrono::{Duration, Utc};
use keyring::Entry;
use rusqlite::{Connection, params};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::Mutex,
};
use tauri::{AppHandle, Manager};
use zip::{CompressionMethod, ZipArchive, ZipWriter, write::SimpleFileOptions};

const SERVICE: &str = "PromptCraft";
const API_KEY_ACCOUNT: &str = "deepseek-api-key";
const DB_KEY_ACCOUNT: &str = "database-key";
const ARCHIVE_SCHEMA_VERSION: u8 = 1;
const MAX_ARCHIVE_BYTES: u64 = 100 * 1024 * 1024;

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ArchiveManifest {
    schema_version: u8,
    product: String,
    exported_at: String,
}

pub struct Storage {
    connection: Mutex<Connection>,
    database_path: PathBuf,
}

impl Storage {
    pub fn initialize(app: &AppHandle) -> Result<Self, String> {
        let app_dir = app
            .path()
            .app_data_dir()
            .map_err(|error| format!("无法确定应用数据目录：{error}"))?;
        fs::create_dir_all(&app_dir).map_err(|error| format!("无法创建应用数据目录：{error}"))?;
        let database_path = app_dir.join("promptcraft.db");
        let key = get_or_create_database_key()?;
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
            PRAGMA user_version = 1;
            ",
            )
            .map_err(|error| format!("无法初始化本地数据库：{error}"))?;
        let storage = Self {
            connection: Mutex::new(connection),
            database_path,
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
            Entry::new(SERVICE, API_KEY_ACCOUNT)
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
        Entry::new(SERVICE, API_KEY_ACCOUNT)
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
            "INSERT INTO history(id,title,original,enhanced,model,target,created_at) VALUES(?1,?2,?3,?4,?5,?6,?7)",
            params![record.id, record.title, record.original, record.enhanced, record.model, record.target, record.created_at],
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
                "SELECT id,title,original,enhanced,created_at,model,target FROM history
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
        if let Ok(entry) = Entry::new(SERVICE, API_KEY_ACCOUNT) {
            let _ = entry.delete_credential();
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
        if fs::metadata(&self.database_path)
            .map(|metadata| metadata.len())
            .unwrap_or(0)
            > 500 * 1024 * 1024
        {
            connection.execute(
                "DELETE FROM history WHERE id IN (SELECT id FROM history WHERE pinned=0 ORDER BY created_at ASC LIMIT 50)",
                [],
            ).map_err(|error| error.to_string())?;
        }
        Ok(())
    }
}

fn read_archive_json<T: for<'de> Deserialize<'de>>(
    archive: &mut ZipArchive<fs::File>,
    name: &str,
) -> Result<T, String> {
    let mut entry = archive
        .by_name(name)
        .map_err(|_| format!("导入包缺少 {name}"))?;
    let mut bytes = Vec::with_capacity(entry.size().min(4 * 1024 * 1024) as usize);
    entry
        .read_to_end(&mut bytes)
        .map_err(|error| format!("无法读取 {name}：{error}"))?;
    serde_json::from_slice(&bytes).map_err(|_| format!("{name} 内容格式无效"))
}

fn get_or_create_database_key() -> Result<String, String> {
    let entry = Entry::new(SERVICE, DB_KEY_ACCOUNT)
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
