//! WP0 验收探针（P0-4）：`provider.rs` 的温度注释不得越界。
//!
//! P0-4 的根因是**注释越界**：`provider.rs` 把"噪声地板内的单点观测"
//! 写成了已证实的改进。bac4185 的原话是：
//!
//! ```text
//! // v0.4.0：由 0.35 降到 0.1。60 条样本 × 3 次的实测显示，
//! // clear 档 none/light 档位翻转率从 55% 降到 30%（约 2.5 个标准误），
//! // 且 light 出现率未下降（52% -> 57%），不是靠"什么都不做"换来的稳定。
//! ```
//!
//! 这三处都与留存文档对不上（真实 n=20/组、z 检验只有 1.65 个标准误、
//! 噪声地板 41 个百分点；"52% -> 57%"在任何留存文档里都不存在）。
//!
//! 本模块用 `include_str!` 把 `provider.rs` 的源码文本**编进测试二进制**
//! （编译期取快照，不依赖运行时的文件路径），因此：
//!
//! - 修复前（bac4185）必然失败：注释里还留着那几句越界表述；
//! - 修复后通过：注释已校准，且**校准表述不许被删光**
//!   （只删不算修 —— 把结论说没了同样是误导）。
//!
//! 运行入口与 P0-1/P0-2/P0-3 共用：`tools\probe-p0-baseline.ps1`。

/// 不得出现的越界表述。每一条都能在 bac4185 的 `provider.rs` 里找到出处。
const FORBIDDEN_CLAIMS: [&str; 5] = [
    "约 2.5 个标准误",
    "60 条样本",
    "档位翻转率从 55% 降到 30%",
    "52% -> 57%",
    "不是靠",
];

/// 必须保留的校准表述。缺任何一条都算不合格。
const REQUIRED_CLAIMS: [&str; 5] = [
    "ENHANCEMENT_TEMPERATURE",
    "方向未确认",
    "噪声地板",
    "1.65 个标准误",
    "50.5%",
];

#[test]
fn probe_p0_4_temperature_comment_makes_no_overclaim() {
    let source = include_str!("provider.rs");

    // ① 先查越界表述：修复前跑出来的失败信息要能直接指出"哪句话越界了"。
    for forbidden in FORBIDDEN_CLAIMS {
        assert!(
            !source.contains(forbidden),
            "provider.rs 的温度注释仍含越界表述「{forbidden}」"
        );
    }

    // ② 再查校准表述是否还在。只删不算修。
    for required in REQUIRED_CLAIMS {
        assert!(
            source.contains(required),
            "provider.rs 的温度注释必须保留校准表述「{required}」"
        );
    }

    println!(
        "[P0-4] 温度注释已校准：保留 1.65 个标准误 / 噪声地板 41 点 / 无偏估计 50.5% / 方向未确认"
    );
}
