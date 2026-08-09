# 模型横向评测脚本设计(Volcano API)

> 日期：2026-08-09 · 状态：已批准

## 目标

在 PromptCraft 评测体系基础上新增 `evaluation/model_benchmark.py`：给定多个模型，先对原始提示词做增强（PromptCraft v2 逻辑），再用增强提示词驱动每个模型生成，从**输出质量、内容相关性、响应速度**三个维度系统评分，输出 Markdown + JSON 结构化报告，横向对比模型表现。

与现有 `run_eval.py` 互补：run_eval 回答"增强 vs 原始"（同模型对比），本脚本回答"模型 vs 模型"（同提示词对比）。

## 关键决策（已确认）

1. **全链路走火山方舟**：增强器、裁判、目标模型均通过 Agent Plan 接口（Anthropic 兼容协议，`POST {base}/v1/messages`，头 `x-api-key` + `anthropic-version: 2023-06-01`）。
2. **报告形式**：`report_benchmark.md` + `summary.json`。
3. 增强逻辑复用 `evaluation/enhancer.py`（SYSTEM_PROMPT v2.0.0 与 Rust 逐字一致，由 test_enhancer.py 保障）。

## 架构（4 个单元 + 配置）

```
benchmark_config.yaml  ──►  model_benchmark.py  主流程（编排/重试/缓存/进度）
        │
        ├─► bench_api.py      通用调用层：anthropic|openai 双协议 → (文本, 耗时, usage)
        ├─► bench_judge.py    LLM 裁判：单答案四维度打分 1-10
        └─► bench_report.py   汇总 → report_benchmark.md + summary.json
```

### bench_api.py
- `call_chat(base_url, api_key, protocol, model, messages, temperature, max_tokens, timeout) -> ChatResult(text, latency_s, usage)`
- anthropic：POST `/v1/messages`，拼 `content[].text`；openai：POST `/chat/completions`，拼 `choices[0].message.content`
- 异常分类：`RateLimited`(429) / `TimeoutError` / `NetworkError` / `HttpError`(其余状态码)
- 密钥解析优先级：配置 `api_key_env` 环境变量 > `api_key_file`(本地文件) > `api_key` 明文

### bench_judge.py
- `JUDGE_SYSTEM_PROMPT`：对**单个回答**按 准确性/完整性/相关性/清晰度 各打 1-10 整数，输出 JSON `{"accuracy":n,"completeness":n,"relevance":n,"clarity":n,"reason":"..."}`
- `judge_answer(task, prompt, answer, api, judge_cfg) -> {accuracy, completeness, relevance, clarity, reason}`
- 解析失败视为评分失败（可重试一次）；`temperature=0`

### model_benchmark.py（主流程）
1. 读配置（模型列表、增强器、裁判、提示词、参数）；CLI：`--config/--models/--prompts/--skip-enhance/--skip-infer/--max-prompts/--max-models`
2. **增强阶段**：每个提示词 → `enhancer.enhance_via_api()`（复用 enhancer.py 的 build_user_message/parse_result/validate_result，调用走 bench_api，默认模型 deepseek-v4-flash）→ 缓存 `bench/enhance_<pid>.json`
3. **生成阶段**：对每(模型 × 提示词)：增强提示词 → `bench_api.call_chat` → 记录文本/延迟/usage → 缓存 `bench/infer_<pid>_<mid>.json`
4. **裁判阶段**：对每(模型 × 提示词)：`bench_judge.judge_answer` → 缓存
5. **汇总**：按模型聚合 → 各维度均分、平均延迟(s)、平均输出长度、tokens/s、胜率（每提示词上得分最高者的比例）、综合分
6. **报告**：`bench_report.generate()` → report_benchmark.md + summary.json

### 综合分（可配置权重，默认）
`综合分 = 0.6×(准确性+完整性+清晰度)/3 + 0.2×相关性 + 0.2×速度分`
速度分 = `1 - min(latency, 120)/120`（0-1 线性归一，120s 封顶），供排名使用；报告同时列出原始延迟秒数。

### 错误处理与重试
- 每(模型×提示词)独立 try/except，单点失败不影响整体，标记 error 进入报告
- 429/5xx/网络错误：重试 2 次，退避 2s/5s；超时(默认 120s)记入 latency 并标记 timeout
- `--skip-enhance/--skip-infer` 读取 `results/bench/` 缓存，支持断点续跑

### 提示词来源
- 配置内嵌 `prompts: [{id, text}]`；也可 `samples_file: samples/samples.yaml` 引用现有 12 样本（取 original 字段）
- 默认配置引用 samples.yaml 前 12 条

## 配置 schema（benchmark_config.yaml）

```yaml
api:
  base_url: https://ark.cn-beijing.volces.com/api/plan
  protocol: anthropic            # anthropic | openai
  api_key_env: ARK_PLAN_API_KEY  # 优先
  api_key_file: key.local        # 回退（相对 evaluation/ 目录）
enhancer:
  model: deepseek-v4-flash
  temperature: 0.35
judge:
  model: deepseek-v4-flash
  temperature: 0
  weights: { quality: 0.6, relevance: 0.2, speed: 0.2 }
models:
  - { id: doubao-seed-2.0-mini, label: 豆包 2.0 mini }
  - { id: glm-5.2, label: GLM-5.2 }
  - { id: deepseek-v4-flash, label: DeepSeek V4 Flash }
prompts: []                     # 内嵌提示词（samples_file 为空时使用）
samples_file: samples/samples.yaml
params:
  max_tokens: 4096
  timeout_s: 120
  retries: 2
  retry_backoff_s: [2, 5]
  delay_between_s: 1.0
run:
  out_dir: results/benchmark
```

## 报告结构（report_benchmark.md）

1. 元信息（时间/增强器/裁判/模型列表/提示词数）
2. **总览表**：模型 | 质量均分 | 相关性 | 速度(延迟s) | 综合分 | 排名
3. **按提示词详情表**：每(模型×提示词)的四维度分/延迟/得分
4. **逐模型小结**：优点/短板（从得分结构与 reason 归纳）
5. 失败清单（error/timeout 条目）

## 测试（pytest）

- `test_bench_api.py`：anthropic 请求体/响应解析（mock urllib）、openai 协议、异常分类（429→RateLimited）、密钥解析优先级
- `test_bench_judge.py`：裁判 JSON 解析、非法输出重试路径
- `test_bench_report.py`：聚合正确性（均分/胜率/综合分/排名）、markdown 生成含总览表
- 复用 `test_enhancer.py` 保证增强逻辑一致（不改动 enhancer.py 现有接口，新增 `enhance_via_api()` 包装）

## 验收标准

1. `pytest` 全绿（新增用例 + 既有 32 项）
2. 真实跑通一次小评测（3 模型 × 3 提示词）生成 report_benchmark.md + summary.json
3. 单模型故意失效时其他模型照常出结果
