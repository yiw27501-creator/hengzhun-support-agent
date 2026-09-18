# 衡准 · 电商售后任务型 Agent

可本地运行的业务沙箱：缺货处理、客户改口、退货验收、人工接管、回执核验，以及知识与质量运营。

## 快速开始

1. 安装 Python 3.12，并加入 PATH。无需 Node、Codex 或模型密钥。
2. 完整解压本文件夹。Windows 双击 START-WINDOWS.cmd；其他系统运行 python3 start.py。
3. 打开 http://127.0.0.1:8776/service ，创建缺货或已签收退货演练工单。
4. 模拟运营分析：http://127.0.0.1:8776/operations 。首次启动自动导入附带的100条模拟运营记录及5条待复核案例，重复启动不重复登记。
5. 知识管理 /knowledge；质量复盘 /quality；实验工作台 /lab。

关闭终端即停止服务。端口占用时运行 python start.py --port 8780，并使用对应端口访问。
默认不调用模型、不产生API费用、不接真实订单或支付，不要暴露到公网。

## 目录

- work/agent-backend：Python后端、测试、提示词、知识/流程配置、技术文档与历史实验记录。
- work/hengzhun-business：业务服务台和运营页面。
- work/support-agent：后端仍使用的早期静态页面资产，不包含旧版Node服务。
- work/model-experiment：模型实验源码、提示词和历史结果；不包含密钥。

## 测试与可选模型配置

进入 work/agent-backend，运行 python -m unittest discover。
默认规则模式；真实模型联动覆盖范围和限制见 BUSINESS-MODEL-BRIDGE.md。
接口适配边界见 TOOL-INTEGRATION.md；运营指标口径见 OPERATIONS-DATA-REPORT.md。
若启用模型，请在新电脑自行配置本地密钥，先查看 python server.py --help 并限制调用预算。
不要提交 .env、运行数据库或凭据到公开仓库。模型实验脚本可能主动调用付费API，启动主程序不运行这些脚本。

## 数据与边界

本包不迁移旧工单、会话及运行数据库，首次启动生成干净的本机数据库。
仅保留公开语料数据库作为FAQ/评测资源；来源和CDLA-Sharing-1.0许可信息见 data/bitext-corpus-20260915/manifest.json。再次公开分发前请核对第三方数据许可要求。
附带运营数据为用户提供的模拟记录；历史模型结果不代表本次重跑。自动办结标记不是实际准确率或降本收益。
原生Function Calling、MCP、真实商家接口、多租户权限及生产部署尚未完成。
技术文档中的历史路径和端口仅作历史记录，迁移启动以本文为准。
