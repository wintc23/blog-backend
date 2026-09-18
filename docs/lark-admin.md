# 飞书网站管理助手

本模块由 `lark-cli` 接收消息，Codex 生成结构化工具调用，Python 调用网站原有 Flask API 并回传结果。独立运行，不会在导入网站或启动 gunicorn 时开启监听。当前按要求仅保留本地实现，尚未作为生产功能交付上线。

## 身份与接口

- 显式配置 `LARK_BRIDGE_APP_ID`、`LARK_BRIDGE_OWNER_OPEN_ID`、`LARK_BRIDGE_ADMIN_USER_ID`；CLI 每次调用固定使用该应用 profile。昵称、当前登录用户和消息正文中的“我是管理员”不能改变绑定。
- 仅接收 Owner 给机器人的私聊；拒绝其他用户、机器人消息和群聊。执行前再次获取原始消息验证发送者和会话。
- 每次工具调用重新检查绑定用户的管理员权限；网站接口由短期管理员凭据通过 Flask 请求分发执行，仍经过原有认证、验证、业务逻辑与请求钩子。没有新增公开的提权 HTTP 入口。
- `site.request` 支持业务路由目录中的 GET/POST/PUT/PATCH/DELETE JSON 请求，`site.describe` 获取路由函数及本模块辅助函数作为参数文档。接口目录来自当前 Flask 路由表，不写死域名或复制业务逻辑。
- 登录/OAuth、独立 AI 聊天认证、任意文件下载和原始上传接口不在通用目录中。文件使用资产工具。管理类 JSON 接口可按原权限调用；不同业务的组合操作仍需要专项联调，不能仅凭目录存在就宣称全部经过验证。
- Codex 只能返回工具名与参数；工具子进程关闭 shell、网页搜索和子代理，不继承数据库、七牛和飞书密钥。API 返回的凭据字段会被遮蔽。网站内容及附件文字只能作为资料，不能授权新的操作。

## 支持的交互

给机器人发送图片后，可继续发送：

- “用刚才的照片发一条生活动态，文案是……”
- “把背景调亮，主体保持不变。”
- “把修好的照片配一句话发到动态。”
- “查一下最近发布的动态。”
- “把刚才那条动态的文案改成……”

只发图片不会自动发布。生活动态接口要求图片；缺少必要资料时会追问。日期默认北京时间当天。修图使用原图调用部署配置指定的 CPA `/v1/images/edits`，生成使用 `/v1/images/generations`；结果独立保存，不覆盖原图。图片返回飞书，发布时上传七牛并引用返回地址。

控制命令：`/status` 查询最近任务，`/cancel` 停止当前会话尚未完成的任务，`/new` 清空后续消息使用的会话上下文。取消不会撤销已完成操作；正在执行的图片 HTTP 请求可能仍完成，但之后不继续发布。

## 审核卡片（评论与留言）

启动后每 30 秒扫描隐藏的评论、留言及回复，包括启动前已有的待审核内容。每个内容版本只生成一张审核卡片，通过 `lark-cli` 以机器人身份私发给配置的 Owner，不必先在飞书发指令。卡片展示类型、编号、作者 ID、提交时间和文字摘要，包含“通过并公开”“拒绝并保持隐藏”按钮。设置了公开站点地址时还提供网站后台链接；图片及超长内容提示到原文查看，不自动下载访客图片。

- **通过**：验证 Owner、应用、原卡片 ID、会话和随机 nonce，然后重新检查管理员权限、当前内容版本与隐藏状态，通过原网站审核 API 公开。版本检查在该请求事务内锁定记录，避免看过旧内容却批准新内容。
- **拒绝**：保留隐藏内容，不删除评论、留言或回复；决定记录在桥接审核表内，同版本不会再次推送。网站原模型只有隐藏标志，后台依然显示隐藏状态。需要重新考虑时可在网站后台处理；编辑后是新版本，会收到新卡片。
- 点击事件只携带审核 ID、nonce 和决定，不能传入任意 API 地址或参数。按钮处理不调用 Codex。非 Owner、伪造卡片、重复/相反决定、过期点击均不执行。
- 卡片有效期 7 天，过期仍待审的内容会发新卡片。内容变更、已在后台处理或被删除时，旧卡片变为失效状态；执行后卡片更新并移除审核按钮。
- 卡片、点击决定和更新回执全部持久化。重启后继续已接收但尚未执行的决定；正在执行而结果不确定的审核标记中断，不自动重做。
- Codex 请求评论/留言审核通过接口也只会排队发卡片，不能绕过按钮直接批准。普通动态发布、修图不增加审核步骤。

**上线前必须在飞书应用控制台的“事件与回调 → 回调配置”启用 `card.action.trigger`，并确保机器人具备消息发送、读取权限。** CLI 的 `ready` 或 `--dry-run` 无法验证该控制台设置：未启用时可能发送正常但收不到点击。服务为消息和卡片各启动一个 CLI consumer，两者共用同一应用事件总线，不需要公网回调 URL。断线期间未送达的卡片点击无法靠聊天历史补收，可重新点击同一张仍待审的卡片。

审核记录保存在同一 SQLite 数据库的 `reviews` 和 `review_callbacks` 表中；`python lark_admin.py status` 会列出最近审核状态。本功能仅本地实现和模拟验证，未部署或发送真实审核卡片。

## 持久化与恢复

`LARK_BRIDGE_STATE_DIR` 保存 SQLite WAL 数据库、图片和服务锁，目录权限 0700。数据库包含消息、任务、每步工具调用与结果、待发送回执。不能将该目录加入代码仓库。

按 `message_id` 去重；同一任务重复提出相同写操作会复用结果。回执使用稳定的飞书幂等键，并独立重试。重启后继续排队中的任务；已经开始但未完成的任务标记中断，不自动重做可能已生效的操作。任务最多 12 步，Codex 单轮 180 秒，图片请求 600 秒，任务在步骤之间检查 30 分钟总限额。

首次启动前的历史消息不会作为新指令执行。已知私聊每分钟补收消息并去重，恢复时保留两分钟重叠窗口；单次最多补收 1000 条，超过会保留游标并记录告警。首次未建立过的私聊在离线期间发来的消息不保证补收，需要用户重发。使用同一应用只运行一个生产监听，避免多连接分流。

`event consume` 的 stdin 必须保持打开；无限监听在 stdin EOF 时会退出。服务解析 stderr 的 ready 标记并持续读取输出，不用固定睡眠冒充就绪。断开后指数退避重连。systemd 负责进程级重启，文件锁禁止多个 worker。

## 配置与本地验证

先加载网站所需环境（数据库、签名密钥、七牛及 CPA 配置），再参照 `lark.env.example` 配置桥接层。CLI 服务账户需要能读取该应用的机器人凭据和 Codex 的认证。

`LARK_BRIDGE_SITE_URL` 是可选的公开站点地址，允许域名、端口及路径前缀。例如 `https://blog.example.test/project`；留空时返回 `/moments` 等相对链接。模型只使用显式传入的 `public_links`，没有默认生产域名。`CONTENT_CODEX_BIN`、`LARK_BRIDGE_CLI` 可以是 PATH 中的命令或绝对路径。PATH 需包含支持当前 CLI 的 Node.js。

```bash
# 在后端目录、现有 Python 虚拟环境中运行。测试数据库为隔离 SQLite。
python -m unittest discover -s tests -p 'test_lark*.py'

# 已加载对应环境后；check 不启动监听、不发送消息。
python lark_admin.py check
python lark_admin.py catalog
python lark_admin.py status

# 真正启用后才执行；会接受 Owner 新消息并执行接口。
python lark_admin.py run
```

`ops/lark-admin.service` 是未安装的模板，需要替换项目目录、运行账号、账号 HOME、环境文件和 Python 路径。`ops/start-lark-admin.sh` 只加载 `LARK_BRIDGE_ENV_FILE` 指定的 shell 环境文件；该文件可自行 source 网站现有配置。不要将环境文件写进代码仓库。

当前验证：隔离数据库中的动态真实增删改、权限撤销、去重、取消、会话重置、恢复和回执重试；模拟图片服务验证携带原图的 multipart 编辑请求。曾短暂验证服务器真实 Codex 只读动态查询和事件 ready 标记，随后按用户要求撤回部署。尚未完成真实飞书指令的端到端发布和图片服务编辑出图联调，后续允许部署时再进行。

## 文件

- `lark_admin.py`：检查、目录、状态与启动入口。
- `lark_bridge/service.py`：监听、Owner 校验、任务执行和消息回执。
- `lark_bridge/store.py`：持久化任务、操作记录、会话、资产与 outbox。
- `lark_bridge/site.py`：原有 Flask API 的管理员调用及敏感字段过滤。
- `lark_bridge/planner.py`：Codex 非交互结构化规划。
- `lark_bridge/media.py`：原图存储、CPA 修图/生成、七牛上传。
- `lark_bridge/configuration.py`：公开链接配置。
- `lark_bridge/reviews.py`：审核扫描、卡片、按钮校验、版本保护与决定执行。

参考：[lark-cli 事件约定](https://github.com/larksuite/cli/blob/main/skills/lark-event/SKILL.md)、[Codex 非交互执行](https://learn.chatgpt.com/docs/non-interactive-mode)、[图片编辑接口](https://developers.openai.com/api/reference/resources/images/methods/edit)。
