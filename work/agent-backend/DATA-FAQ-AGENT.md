# 客服数据、FAQ 与事件驱动 Agent 迭代

## 数据原则

来源为 Bitext Innovations 官方公开客服数据集：
https://github.com/bitext/customer-support-llm-chatbot-training-dataset

它是英文混合合成数据，不是真实商家聊天记录。数据及修改后的问题集合按 CDLA-Sharing-1.0 标记，保留来源、固定 Git 版本、行号及修改说明：
https://cdla.dev/sharing-1-0/

本地导入结果位于 `data/bitext-corpus-20260915/`。只有导入完成并生成 manifest.json 后才能报告已导入数量；上游宣称数量不是本地核验结果。

本轮已完成历史版本 `337b96868e02b40a05f9c0df3290c65a15ea573e` 中 `data/train/Bitext_Sample_Customer_Support_Training_Dataset.csv` 的完整下载和 Git blob 校验。实际解析 4514 条，保留 4070 条，规范化重复 68 条，联系方式/长数字模式隔离 376 条；开发 3251 条、测试 819 条。长数字可能是合成订单号，此处保守隔离不表示发现真实隐私。

最新 27K 文件的下载曾多次中断，未导入，也未拿其数量充当本轮结果。来源数量是合成数据分布，不代表业务问题频次。

处理步骤：分段下载 → 校验 Git blob SHA1 → 解析 → 联系方式模式隔离 → 规范化精确去重 → 稳定哈希分组 → SQLite 入库 → 按原始意图标签生成 FAQ 草稿。

不复制上游 response 到商家执行知识库，不把英文自动当作中文覆盖。精确去重不能消除模板近重复；哈希测试划分不能称为独立业务测试集，后续需按模板族或来源组拆分。

## FAQ

每个意图生成一个聚合草稿，包括中文标题、处理说明、必需信息、原始问题示例、来源数量、适用的沙箱政策引用。

本轮实得 27 类 FAQ 草稿。过滤后类别不均衡，例如 cancel_order 24 条、track_order 52 条；不能忽略该偏差去报告整体平均准确率。后续应利用合成实体标注做占位符替换，减少直接隔离带来的覆盖损失。

- 退款相关内容只引用本项目的 refund、risk、retry 沙箱政策。
- 其他类别给出需要核对的信息，明确尚未接入真实商家系统，不编造费用、链接、到账/送达时间。
- 全部状态为 draft，execution_enabled=false。当前是按已有标签分组及模板生成，不是无监督聚类或批量 LLM 生成。
- 草稿不会自动进入执行 RAG。正式发布仍需业务审核、版本和生效范围管理。

## 已实现链路

```text
网页原始多轮消息
  → POST /api/workflow/dialogues（事件 ID 幂等，返回 202）
  → dialogue_submissions 入库
  → SQL AFTER INSERT 触发器：同事务写入 jobs 与 intake_audit
  → 后台 worker 领取租约、预留持久化模型预算
  → 官方模型理解 + JSON/客户原文证据校验
  → 原子保存理解结果、完成 job、记录 Prompt 哈希
  → NEEDS_CLARIFICATION / NEEDS_REVIEW
```

模型关闭时明确显示 human_review_model_disabled，不伪装成模型判断。异常有限重试，最多 3 次，之后 DEAD；只记录异常类型，不泄露密钥或原始异常响应。租约过期可恢复，旧 worker 不可覆盖新结果。

**授权边界**：此链路只理解消息，不直接授权财务操作。已有任务链路仍为“任务事实 → RAG/规则规划 → 确定性校验 → 沙箱工具 → 回执核验”，存在人工复核入口。两条链路尚未通过经过验证的订单身份与最新消息版本完成自动绑定，不能称为原话到退款的全自动闭环。

## 数据库分工

| 数据 | 表 | 用途 |
| --- | --- | --- |
| 外部语料 | questions、faq_drafts | 研究问题、开发案例、FAQ 草稿；不作为执行授权 |
| 消息理解 | dialogue_submissions、intake_audit、jobs | 原话快照、幂等事件、后台任务、结果审计 |
| 任务执行 | orders、tasks、executions、traces | 合成订单、状态机、业务效果幂等、工具回执 |
| 调用限额 | model_calls | 发送前持久化预留预算；重启不清零 |

这是本机单用户 SQLite POC，不是云端多租户生产架构。没有接入真实订单、支付或客服渠道。

## Prompt 两次修改、三版实测

| 版本 | 变更假设 | 开发通过数 | Tokens |
| --- | --- | --- | --- |
| v1 | 原始对话、意图、原文证据基线 | 4/4 | 1494 |
| v2 | 区分咨询与执行请求，明确范围外意图 | 4/4 | 1691 |
| v3 | 单一选项承接、多选歧义、条件句及撤回规则 | 4/4 | 1967 |

共 12 次调用，5152 tokens。三版使用相同开发案例，并非三次独立业务验证；没有测出准确率提升，v3 用量更高。不能用 Prompt 更长来宣称更好。v3 是可回退的候选配置，仍需要更大、独立的评测确认。

另外运行了 1 次真实 HTTP→SQL 触发→模型→审计冒烟测试，491 tokens。该测试验证链路连通，不验证财务执行。两部分合计 13 次调用、5643 tokens；不估算未经核验的金额。

最新自动化回归 39 项通过，前端 JavaScript 语法检查通过，本机 /lab HTTP 返回 200。尚未进行视觉与可用性验收。

原始结果：
- `data/prompt-rounds-20260915T125502169149Z/report.json`
- `data/intake-smoke-1789477379319421900.json`

## 复现与展示

```powershell
python import_support_data.py --legacy --output data/bitext-corpus-20260915
python -m unittest discover -v
python prompt_rounds.py
python smoke_intake.py
python server.py --desktop --async-jobs --port 8766 --db data/workbench-v2.db
```

首次导入使用新目录，脚本不会覆盖已存在的语料。Prompt 默认 dry run；加 --live --direct 会产生模型费用。模型模式启动需显式配置官方密钥及调用上限；不将密钥写入代码或上传 GitHub。

打开 http://127.0.0.1:8766/lab 查看 FAQ 和消息触发流程。当前默认启动规则模式，不会因访问页面而产生模型费用。本轮未更新公开网站，也未上传 GitHub。

## 下一阶段验收

1. P0：订单身份、会话和消息版本绑定；新消息撤回旧授权；确认后再连接现有工具执行链路。
2. P0：FAQ 审核发布与撤销；未审批、过期、其他租户材料不得用于回答或执行。
3. P1：近重复分组、中文本地化样本与人工复核，避免将英文合成数据规模当作业务代表性。
4. P1：使用外部问题构建任务路由评测；当前三分类理解器不能代表 27 类意图识别器。
5. P1：固定配置后评测原有保留测试集，并比较无 RAG/词法 RAG/改进 RAG；本轮没有消融结论。
6. P1：真实客服渠道 webhook 签名校验、持久化云端 worker、监控与业务系统适配。
