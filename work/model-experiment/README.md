# 衡准模型实验

## 集川接入状态

使用 `python experiment.py --live --direct --provider jichuan` 运行首批5条。
此模式读取同一个本地密钥配置，向 https://jc.jichuanai.com/responses 请求目录中的 deepseek-v4.1-flash。
输出上限600 tokens；采样及思考模式采用平台默认值，实验配置会记录这一差异。
按 Responses 的 input_tokens/output_tokens 统计用量，集川价格未知，费用字段为 null。
2026-09-14 接入验证返回 HTTP 403、error code: 1010，未取得模型输出。需要平台确认是否允许外部脚本访问此接口。
该结果是接口访问失败，不能记为模型能力失败或Prompt通过率0%。

目标：验证库存冲突场景的下一步建议是否符合政策。这里调用真实模型，但不执行退款或改单。
当前状态：实验代码与 V1 已准备，尚未产生真实模型结果；V2 在分析错误之后再创建。

## 本地配置和运行

1. 撤销曾发到聊天中的旧密钥。打开本目录 `.env`，在等号后填写新密钥并保存。不发聊天，不提交 Git。
2. 在本目录打开终端，执行 `python experiment.py`。这是无费用的 dry run，只检查准备状态。
3. 执行 `python experiment.py --live`，默认 V1、开发集前5条、每条1次，共5次真实调用。
4. 查看 `results/时间戳/records.jsonl` 和 `summary.json`，逐条复核。

扩大开发集：`python experiment.py --live --limit 15 --repeat 3`
保存 V2 后：`python experiment.py --live --prompt v2 --limit 15 --repeat 3`
最终验证：分别增加 `--split holdout` 测试冻结后的 V1 和 V2。

默认无自动重试，单次45秒超时、输出上限600 tokens、关闭思考模式。HTTP错误或超时中止整批，避免反复扣费。超时可能已在服务端产生费用，用量不能视为零。
程序只访问 https://api.deepseek.com/chat/completions，不读取订单或个人数据。

## 案例与标注

experiment.py 中 cases() 定义30条合成案例：15条开发、15条参数变化后的留出案例。
覆盖客户偏好、无库存、差价边界、支付缺失、身份风险、退款进行中、安全风险、指令注入和政策冲突。
留出集与开发集同模板，仅用于初步稳定性验证；不能证明真实业务泛化能力。后续需要独立编写的新场景和真实脱敏样本。
expected 标签只在本地评分，不发送给模型。商家政策是为实验定义的假设，不是实际商家的政策。

## 你要亲自参与的产品工作

- 先读 POLICY，判断政策优先级是否符合业务预期，复核案例标签，再冻结数据集。
- 逐条阅读输出，记录错误类型、证据和业务影响。自动评分不能判断解释是否忠实、是否真的需要所引用证据。
- 根据开发集错误提出一个改进假设，再修改 prompts/v2.txt。不要故意削弱V1，也不要依据留出集反复改Prompt。
- 保持模型、政策、参数与数据一致比较V1/V2。报告通过条数/总条数，逐案例对比新增通过和新增失败；重复调用不等于独立业务样本。
- 无错误时增加有业务依据的挑战案例；不能编造提升数据。

## 实验记录模板

日期：
业务假设与政策版本：
模型及返回模型标识：
开发集观察到的失败案例ID：
错误证据与归因（Prompt/数据/政策/模型/评分器）：
V2改动及预期作用：
V1与V2通过条数、总条数、新增失败：
人工复核发现：
token用量与费用估算：
限制与下一步：

每次运行保存 Prompt 原文、哈希、数据集哈希、请求事实、期望标签、模型输出、finish_reason、usage和耗时。
summary中的费用按输入缓存未命中、高峰价格粗估，实际账单以DeepSeek为准。失败调用费用可能缺失。
参考：https://api-docs.deepseek.com/api/create-chat-completion/ 和 https://api-docs.deepseek.com/quick_start/pricing/
