use crate::models::ExtractedAttachment;
use encoding_rs::GBK;
use quick_xml::{events::Event, Reader};
use std::{fs, io::Read, path::Path};
use zip::ZipArchive;

const MAX_FILE_BYTES: u64 = 10 * 1024 * 1024;
const TEXT_EXTENSIONS: &[&str] = &[
    "txt", "md", "json", "csv", "py", "js", "ts", "tsx", "jsx", "java", "c", "cpp", "h",
    "rs", "go", "html", "css", "sql", "xml", "yaml", "yml", "toml", "ini",
];

pub fn extract(path: &str) -> Result<ExtractedAttachment, String> {
    let path = Path::new(path);
    let metadata = fs::metadata(path).map_err(|_| "无法读取文件，请检查文件是否仍然存在".to_string())?;
    if !metadata.is_file() { return Err("只支持单个文件，不支持文件夹".into()); }
    if metadata.len() > MAX_FILE_BYTES { return Err("单个文件不能超过 10 MB".into()); }
    let extension = path.extension().and_then(|value| value.to_str()).unwrap_or("").to_ascii_lowercase();
    let text = if TEXT_EXTENSIONS.contains(&extension.as_str()) {
        read_text(path)?
    } else if extension == "pdf" {
        pdf_extract::extract_text(path).map_err(|_| "无法提取 PDF 文本；V1 不支持扫描版 PDF".to_string())?
    } else if extension == "docx" {
        extract_docx(path)?
    } else {
        return Err(format!("不支持 .{extension} 文件"));
    };
    if text.chars().count() > 100_000 { return Err("文件提取文本超过 100,000 字符，请缩小范围后重试".into()); }
    if text.trim().is_empty() { return Err("文件中没有可提取的文本".into()); }
    let chars = text.chars().count();
    Ok(ExtractedAttachment { text, kind: extension, chars })
}

fn read_text(path: &Path) -> Result<String, String> {
    let bytes = fs::read(path).map_err(|_| "读取文件失败".to_string())?;
    if bytes.iter().filter(|byte| **byte == 0).count() > 4 { return Err("文件内容不像纯文本，已拒绝读取".into()); }
    if let Ok(text) = std::str::from_utf8(&bytes) { return Ok(text.trim_start_matches('\u{feff}').to_string()); }
    let (decoded, _, had_errors) = GBK.decode(&bytes);
    if had_errors { return Err("无法识别文件编码，请将文件保存为 UTF-8 后重试".into()); }
    Ok(decoded.into_owned())
}

fn extract_docx(path: &Path) -> Result<String, String> {
    let file = fs::File::open(path).map_err(|_| "读取 DOCX 失败".to_string())?;
    let mut archive = ZipArchive::new(file).map_err(|_| "DOCX 文件结构无效".to_string())?;
    let mut document = archive.by_name("word/document.xml").map_err(|_| "DOCX 缺少正文内容".to_string())?;
    let mut xml = String::new();
    document.read_to_string(&mut xml).map_err(|_| "DOCX 正文编码无效".to_string())?;
    let mut reader = Reader::from_str(&xml);
    reader.config_mut().trim_text(false);
    let mut output = String::new();
    loop {
        match reader.read_event() {
            Ok(Event::Text(text)) => {
                if let Ok(decoded) = text.decode() { output.push_str(&decoded); }
            }
            Ok(Event::End(tag)) if tag.name().as_ref() == b"w:p" => output.push('\n'),
            Ok(Event::Eof) => break,
            Err(_) => return Err("DOCX 正文解析失败".into()),
            _ => {}
        }
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_unknown_extension() {
        assert!(extract("not-a-real-file.exe").is_err());
    }
}
