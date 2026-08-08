# PromptCraft 增强效果评测脚本

对比「提示词增强前 vs 增强后」在多个大模型（网页版）上的实际效果，输出结构化评测报告。

## 评测流程

```
原始提示词 ──► 增强器(DeepSeek API，复刻 PromptCraft 后端逻辑) ──► 增强提示词
     │                                                              │
     └───────────────► 网页版目标模型（豆包 / DeepSeek / 千问…） ◄──┘
                                │
                                ▼
          原始版回答  vs  增强版回答（同一提示词两组输出）
                                │
                    LLM-as-judge（DeepSeek API 四维度打分）
                                │
                                ▼
           report.md / report.html / raw JSON（逐条对比 + 整体汇总）
```

- 增强器逻辑逐字复刻自 `src-tauri/src/provider.rs`（SYSTEM_PROMPT、请求体、解析、校验），
  由 `test_enhancer.py` 直接读取 Rust 源码断言一致性（15 项测试）。
- 评测维度：**准确性 / 完整性 / 相关性 / 清晰度**，每项 1-10 分，裁判前随机互换 A/B 消除位置偏差。

## 环境准备

```powershell
cd evaluation
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m playwright install chromium
```

## 使用步骤

### 1. 配置

复制并检查 `config.yaml`：

- `key_source`: 默认 `keyring` —— 脚本直接读取 PromptCraft 已存入 Windows 凭据管理器的
  DeepSeek API Key（`SERVICE=PromptCraft, ACCOUNT=deepseek-api-key`），无需重复填写；
  也可改为 `env`（设置 `DEEPSEEK_API_KEY`）或 `config`（在文件里写 `api_key`）。
- `targets`: 启用/停用各网页版站点；`selectors` 里的 DOM 选择器在站点改版时可自行调整。
- `judge.model` / `enhancer.model`: 裁判与增强器使用的 DeepSeek 模型。
- `browser.channel`: 自动化用的浏览器，默认 `msedge`（系统自带 Edge，无需下载浏览器、不易被安全软件拦截）；
  也可改 `chrome`（系统 Chrome）或 `chromium`（Playwright 自带）。

### 2. 登录网页版（一次性）

```powershell
.\.venv\Scripts\python run_eval.py --login
```

脚本会用 **Microsoft Edge**（默认）打开浏览器逐站访问，请手动完成登录（含验证码）。
登录态保存在 `.browser_profile/`，之后评测自动复用。

> 也可以不用自带浏览器：用 `msedge.exe --remote-debugging-port=9222 --user-data-dir=<你的profile>`
> 启动自己的 Edge（或 Chrome）并登录，然后把 `browser.mode` 改为 `cdp`，脚本将连接 `localhost:9222` 复用登录态。

### 3. 运行评测

```powershell
.\.venv\Scripts\python run_eval.py
```

常用参数：

| 参数 | 说明 |
|---|---|
| `--target doubao deepseek_web` | 只评测指定目标（可多次） |
| `--skip-enhance` / `--skip-infer` | 复用 `results/_cache/` 缓存，断点续跑 |
| `--max-cost 3.0` | 覆盖增强+裁判的 API 预算（默认 2 美元） |
| `--login` | 打开浏览器登录各站点 |
| `--offline` | 完全离线自测：mock 目标 + 确定性裁判，验证管线 |
| `--personas novice student office` | 用户画像模拟：按 `personas.yaml` 中定义的画像（纯AI小白/学生/普通办公员工…）改写样本后展开评测 |

> 画像模拟只在样本自带对应画像改写（`samples.yaml` 中的 `personas` 字段）时生效；不指定 `--personas` 时行为与旧版完全一致。

### 4. 查看报告

输出在 `results/<时间戳>/`：

- `report.md` / `report.html` — 可读报告（逐条对比 + 整体汇总）
- `raw/` — 每条原始数据（增强结果、两组回答的 JSON）
- `summary.json` — 聚合统计（按维度/场景/目标模型）

## 目录结构

```
evaluation/
├── config.yaml            # 全局配置（API、目标站点选择器、预算）
├── run_eval.py            # 主入口
├── config.py              # 配置加载与 API Key 解析
├── enhancer.py            # 复刻 Rust 增强逻辑（准确性红线）
├── judge.py               # LLM-as-judge 裁判
├── report.py              # 报告生成
├── targets/               # 目标适配层（web 网页版 / api 直连 / mock 离线）
├── samples/samples.yaml   # 评测样本（12 条，可自行增改）
├── personas.yaml          # 用户画像定义（--personas 模拟用，可自行增改）
├── templates/             # HTML 报告模板
├── test_enhancer.py       # 增强逻辑等价性测试（对照 Rust 源码）
├── .browser_profile/      # 网页版登录态（勿提交）
└── results/               # 评测结果（勿提交）
```

## 常见问题

- **提示「未找到输入框」**：未登录或站点改版。先跑 `--login` 登录；仍失败则检查该站点
  `selectors.input` 是否匹配当前页面。
- **裁判/增强报错**：检查 `keyring` 是否能读到 API Key（`python config.py` 可自检）、
  账户余额、模型名是否可用。
- **网页版触发风控/验证码**：降低 `run.delay_between` 的调用频率；改用 `browser.mode: cdp`
  连接真实 Chrome（有头、带登录态）更不易被风控。
