# 受限异步任务入口

2026-09-15：本机HTTP → SQLite持久化队列 → 后台worker → 词法RAG → 官方DeepSeek → 确定性校验 → 沙箱执行 → 结果核验已完成一次真实联调。返回202后消费者才启动，证明受理与处理分开。调用预算1次、实际预留1次，任务最终COMPLETED。

记录：data/http-smoke-20260915T082326561126Z/report.json。该记录是单条技术联调，不是模型准确率评测。

## 启动

不调用模型的页面验证：

```powershell
python server.py --desktop --async-jobs
```

打开 http://127.0.0.1:8765/lab。创建任务、提交异步处理、观察队列状态。冲突任务需填写确认依据并保存，再次提交。原同步工作台不用于该模式。

真实模型模式（先在本机环境变量配置DEEPSEEK_API_KEY）：

```powershell
python server.py --desktop --async-jobs --planner rag --max-model-calls 3
```

也可通过 --key-file 指定本机.env文件；--direct表示不使用环境代理。不把.env上传仓库，不将模型密钥填到网页。

预算按队列数据库持久化累计，重启不会清零。网络超时的预留次数不会退还，因为供应商可能已经计费。剩余预算不足时拒绝新模型任务；已经排队的请求仍受发送前的原子预算检查约束。最多3次是调用次数限制，不是精确金额上限。

后台消费者不依赖网页轮询继续运行，但依赖本机服务进程。不能据此声称关机后本机任务继续执行。云端公开工作台仍是上一个规则沙箱版本，尚未部署此RAG/队列入口。

## 新增接口

- POST /api/workflow/tasks/{id}/enqueue：只允许READY/RETRYABLE，立即返回202，重复请求复用活动作业；同步run在异步模式禁用。
- GET /api/workflow/tasks/{id}/job：返回队列状态，不暴露租约令牌。
- GET /api/workflow/health：返回后台worker状态、真实规划器、累计预留次数和预算。
- 人工确认后再次提交：允许将已完成自动阶段的DONE作业重新入队；DEAD作业不能盲目复活。

model.observed轨迹包含政策ID、语料哈希、Prompt哈希、模型版本、请求ID、usage和错误类型。保留原始消息与人工确认说明，不记录密钥。

## 验证与已知限制

22项单元/集成测试通过；smoke_runtime.py --live --direct真实联调通过，最多1次模型请求。无--live时使用规则规划器。

目前单个后台worker；Engine仍在SQLite写事务中规划，长模型请求会限制并发写入。队列实现并不等于高并发已解决。下一阶段需拆分事务、版本检查与领取租约，再设计云端持久化消费者，而不是仅使用请求内异步函数。

本机实验台未连接真实支付或商家接口。云端升级和GitHub公开均未在本次操作中执行。
