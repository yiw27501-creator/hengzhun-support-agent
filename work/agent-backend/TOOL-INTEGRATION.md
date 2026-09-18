# 业务工具接入边界 v1

目的：将模型的查询建议与订单/库存系统实现分离，不扩大退款执行权限。

## 已完成

- `tool_gateway.py` 提供五个只读工具：get_order、check_inventory、get_purchase_relation、get_product、get_execution_receipt。
- BusinessChat 的库存、购买关系和商品查询经过统一网关，不再在对话分支里构造工具结果。
- 订单与回执由 SQLite 参数化查询获得；库存、购买关系、商品仍是明确标注的模拟夹具。
- 模型不得传入订单号、SQL、URL、密钥；当前 task_id 由服务端工作流绑定，再解析对应订单。
- 统一返回 ok/data/error/call_id/tool/schema_version/sandbox/latency_ms，并记录 tool.called 审计事件。
- 适配器故障、超时、库存字段不合法时停止该查询流程并转人工，不回退成“模拟成功”。查询购买关系绝不授予退款权限。
- tool_definitions() 提供中立 JSON Schema，可供后续 provider tools 或 MCP 包装器复用。

## 接口位置

实现 ReadAdapter.read(connection, name, order_id) 并注入 ToolGateway(engine, adapter)，再注入 BusinessChat(engine, runtime, tools)。UnconfiguredAdapter 用于明确拒绝未配置接入。

真实接入时由适配器管理服务端凭据和固定地址白名单，不允许客户/模型决定连接目标。数据库应使用限定表/视图的只读账号，优先采用现有业务 API。不能把浏览器的“当前订单”视为生产身份校验。

## 尚未完成，不可对外宣称

- 本次没有真实商家数据库/API，没有原生模型 Function Calling 请求，也没有启动 MCP server；现有模型仍返回受约束动作 JSON。
- 仅自然语言 BusinessChat 查询分支使用新网关，旧规则演练 prepare/advance 仍保留原有模拟实现。
- get_order 和 get_execution_receipt 已有可调用适配器及测试，尚未增加新的模型路由选项。
- 不提供退款写工具；原有授权、幂等与执行前校验仍由执行引擎负责。
- 当前调用发生在本地事务内，仅适合快速沙箱查询。真实远程适配前必须将 I/O 移出事务，返回后重验对话/授权版本，并实现网络超时、资源预算、库存时效与响应完整校验。
- 生产多租户授权、每工具角色权限、真实回执查询的最终一致性处理尚未实现；未查到回执不能自动推断可以重试付款。

## 本地验证

在 agent-backend 目录运行 `python -m unittest test_tool_gateway test_business_chat`。
测试使用临时数据库及模型替身，不消耗模型 API，不连接商家。
验收覆盖当前订单范围、禁止任意参数/退款工具、未配置拒绝、超时脱敏、无库存返回与模型→工具→业务状态联动。
