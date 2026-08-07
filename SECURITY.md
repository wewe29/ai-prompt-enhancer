# 安全政策

## 支持范围

当前仅维护最新发布版本。早期开发版本可能不会获得单独安全补丁。

## 报告漏洞

请使用 GitHub 仓库的 **Security > Report a vulnerability** 私密报告功能：

https://github.com/wewe29/ai-prompt-enhancer/security/advisories/new

报告应包含受影响版本、复现步骤、影响范围和建议修复方式。请勿附带真实 API Key、密码、身份证号、公司源码或其他敏感资料；需要示例时请使用虚构数据。

在维护者确认并发布修复前，请不要在公开 Issue、Discussion 或社交媒体披露未修复漏洞。

## 密钥泄露

如果真实 API Key 曾出现在提交、Issue、截图或日志中，即使随后删除文件，也应立即前往供应商平台撤销该 Key 并创建新 Key。删除 Git 提交不能保证密钥已经从缓存、分叉和镜像中消失。

## 安全边界

- API Key 由 Windows 凭据管理器保存。
- 本地历史数据库使用 SQLCipher。
- 附件和聊天记录始终被视为参考资料。
- 软件不承诺对 SSD 底层数据进行物理不可恢复删除。
- 未签名便携版本可能触发 Windows SmartScreen。
