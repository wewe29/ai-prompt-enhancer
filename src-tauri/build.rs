use std::path::PathBuf;

fn main() {
    // tauri-build 默认会往 **bin** 目标里塞一份 SxS 清单（Common-Controls 6.0），
    // 但只覆盖 bin：`cargo test` 生成的测试可执行文件（`cargo test --lib`）拿不到，
    // 进程**加载即失败**：
    //
    //     0xc0000139 STATUS_ENTRYPOINT_NOT_FOUND
    //
    // 原因不是缺 DLL，而是**缺清单**：Tauri 的 mock runtime 链路会引用
    // `comctl32` v6 才有的导出（`TaskDialogIndirect`）；没有 SxS 清单时
    // Windows 加载到的是 comctl32 **v5**（System32 里的旧版），找不到该导出。
    // 症状是测试二进制直接起不来、而不是测试失败 —— 极易被误判成"环境问题"。
    //
    // 这里的做法：**不让 tauri-build 嵌自己的清单**，改为由本文件给**所有目标**
    // （bin / cdylib / 测试可执行文件）嵌**同一份**清单。
    // 这样不会出现"两个 RT_MANIFEST 冲突"（cvtres 会报 CVT1100/LNK1123）。
    let attributes = tauri_build::Attributes::new()
        .windows_attributes(tauri_build::WindowsAttributes::new_without_app_manifest());
    if let Err(error) = tauri_build::try_build(attributes) {
        panic!("tauri-build 失败：{error:#}");
    }
    embed_app_manifest_for_all_targets();
}

/// 把 SxS 清单（Common-Controls 6.0）编成资源文件，链给所有目标。
#[cfg(windows)]
fn embed_app_manifest_for_all_targets() {
    use std::{env, fs};

    const SXS_MANIFEST: &str = r#"<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <dependency>
    <dependentAssembly>
      <assemblyIdentity
        type="win32"
        name="Microsoft.Windows.Common-Controls"
        version="6.0.0.0"
        processorArchitecture="*"
        publicKeyToken="6595b64144ccf1df"
        language="*"
      />
    </dependentAssembly>
  </dependency>
</assembly>
"#;

    let out_dir = PathBuf::from(env::var("OUT_DIR").expect("OUT_DIR"));
    let manifest_path = out_dir.join("app-manifest.xml");
    fs::write(&manifest_path, SXS_MANIFEST).expect("写入 SxS 清单");
    let resource_path = out_dir.join("app-manifest.rc");
    // 资源类型必须写**数字 24**（RT_MANIFEST）。
    // 写符号名 `RT_MANIFEST` 在不 include Windows SDK 头文件时会被当成
    // "名叫 RT_MANIFEST 的字符串资源"，而加载器只认数字类型 24
    // ⇒ 清单形同没嵌，进程照样 0xc0000139。
    fs::write(&resource_path, "1 24 \"app-manifest.xml\"\n").expect("写入资源脚本");
    let compiled = out_dir.join("app-manifest.lib");

    let Some(rc) = find_rc_exe() else {
        // 找不到 rc.exe：不阻断构建。缺清单的后果是**测试可执行文件起不来**
        // （0xc0000139），会立刻暴露，不会静默通过。
        println!(
            "cargo:warning=找不到 rc.exe，SxS 清单未嵌入：cargo test 生成的测试可执行文件\
             可能加载失败（0xc0000139）。设置 RC 环境变量指向 rc.exe 可修复。"
        );
        return;
    };
    let status = std::process::Command::new(&rc)
        .args([
            "/fo",
            &compiled.to_string_lossy(),
            "/I",
            &out_dir.to_string_lossy(),
        ])
        .arg(&resource_path)
        .status()
        .unwrap_or_else(|error| panic!("运行 rc.exe 失败：{error}"));
    assert!(status.success(), "rc.exe 编译 SxS 清单失败：{status}");

    // `cargo:rustc-link-arg` 覆盖 bin / cdylib / 测试可执行文件等所有需要链接的目标。
    println!("cargo:rustc-link-arg={}", compiled.display());
}

/// 定位 Windows 资源编译器 rc.exe。
///
/// 顺序：环境变量 RC → Windows Kits 的 bin 目录。
/// （MSVC 的 rc.exe 不在 PATH 里，只能按位置找。）
#[cfg(windows)]
fn find_rc_exe() -> Option<PathBuf> {
    use std::env;

    if let Some(rc) = env::var_os("RC") {
        let path = PathBuf::from(rc);
        if path.is_file() {
            return Some(path);
        }
    }
    let roots = [
        env::var_os("ProgramFiles(x86)").map(PathBuf::from),
        env::var_os("ProgramFiles").map(PathBuf::from),
    ];
    for root in roots.into_iter().flatten() {
        let kits = root.join("Windows Kits").join("10").join("bin");
        let Ok(versions) = std::fs::read_dir(&kits) else {
            continue;
        };
        let mut candidates: Vec<PathBuf> = versions
            .filter_map(|entry| entry.ok())
            .map(|entry| entry.path().join("x64").join("rc.exe"))
            .filter(|path| path.is_file())
            .collect();
        // 版本号大的优先（新的 Windows SDK）
        candidates.sort();
        if let Some(found) = candidates.pop() {
            return Some(found);
        }
    }
    None
}

#[cfg(not(windows))]
fn embed_app_manifest_for_all_targets() {}
