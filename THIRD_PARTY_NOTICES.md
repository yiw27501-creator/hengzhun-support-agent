# 第三方数据说明

本仓库的 `work/agent-backend/data/bitext-corpus-20260915/support.db` 和 `faq_drafts.json` 包含由 Bitext Innovations 提供的数据经本项目处理后的内容。

- 来源：[Bitext Customer Support LLM Chatbot Training Dataset](https://github.com/bitext/customer-support-llm-chatbot-training-dataset)，取用修订版 `337b96868e02b40a05f9c0df3290c65a15ea573e`。
- 数据提供方：Bitext Innovations。上游仓库保留其版权和署名声明。
- 数据许可证：[CDLA-Sharing-1.0](https://cdla.dev/sharing-1-0/)。此许可证适用于上述第三方数据及其增强数据，不代表本项目原创代码采用相同许可证。
- **修改说明**：本项目仅保留问题文本，隔离匹配联系方式或长数字的记录，按规范化文本精确去重，以稳定哈希划分开发集和测试集，并按意图生成 FAQ 草稿；上游答案未纳入这些数据文件。
- 详细来源修订号、原始哈希和处理数量见 [数据清单](work/agent-backend/data/bitext-corpus-20260915/manifest.json)。

`work/agent-backend/data/operations-source/user-20260917.txt` 为项目提供的手工编写模拟记录，与 Bitext 数据无关。它不是真实客户工单，也不是系统效果的实测样本。
