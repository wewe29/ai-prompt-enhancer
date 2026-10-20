# evaluation/results/ —— 本机中间产物，**不入库**

这个目录里的东西都是**可再生**的：缓存、逐次落盘的中间状态、临时产物。
它们体积大、每次重跑都会变，入库只会污染版本历史。

## 证据在哪里

**参与结论的产物不在这里。** 它们归档在：

```
docs/evaluation-data-v040/
├── manifest.json          三次实验的机器可读索引（身份、来源脚本、结论文件）
├── conclusions/*.json     报告声称值（机器可读，供重跑后逐项对账）
└── answers/               脱敏后的人工盲评答案归档
```

`manifest.json` 里登记了每个实验的**运行身份**（提示词版本 + 正文哈希 + 模型 +
温度 + 样本集哈希 + 重复次数）—— 这是 v0.4.0 架构审查 P0-4 / D7 的闭环：
报告里写的每一个数字，都能顺着 manifest 找到它的产生者。

## 为什么这个目录曾经被整条忽略、现在不是

旧版 `.gitignore` 是 `evaluation/results/`（整条）。而报告里写着
「数据见 `evaluation/results/v035_deterministic.json`」——**那个文件根本不在
仓库里**。于是「结论」和「结论的复现材料」分属两个版本，
**没有人能复核任何一个数字**。

现在分两层：

1. 可再生的中间产物留在本机（`evaluation/results/*` 不入库）；
2. 参与结论的产物经 `docs/evaluation-data-v040/` 归档入库，
   并在 `manifest.json` 登记身份与来源。

## 产物命名约定（v0.4.0 起）

新脚本的产物名与目录名**编码自身身份**，每次运行一个独立目录，**绝不覆盖**：

```
evaluation/results/stability/20261020-145056-756565__m-deepseek-flash__t0.35__r3__7b2b4ea682/
    v040_stability_raw__m-deepseek-flash__t0.35__r3__7b2b4ea682.json
    v040_stability_summary__m-deepseek-flash__t0.35__r3__7b2b4ea682.json
```

机制见 `evaluation/run_identity.py`：`--resume` 前会做**身份一致性闸**，
身份不同直接拒绝续跑，而不是把新旧结果混进同一份产物。
