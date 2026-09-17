# 自动生成：开发与部署

实现日期：2026-09-16，Codex 配图接入更新于 2026-09-17。文字和图片生成均支持 Codex CLI 与兼容 API，历史期次可显式沿用原封面。国内及海外 RSS 已配置；启用日常生成前，需在所选图片通道完成配置并验证一次真实出图。

## 已实现

- `/manage/generation`：任务配置、RSS / Atom 来源管理与连通性测试、手动试运行、执行记录、取消与重试。
- `/manage/content`：实际内容列表、结构化编辑、预览、来源快照、修订记录、发布与撤回。
- 独立 scheduler / worker：北京时间调度、持久化队列、原子领取、租约续期、失效恢复、重试退避、到期发布。
- 通用 task / version / job / run / content / revision 数据结构；`daily_digest` 是首个类型适配器。
- 采集真实 Feed 正文／摘要，保留发布时间和采集时间；模型只引用已有来源 id，服务端回填来源链接。
- 文字生成 → 本期封面生成 → 七牛上传 → 结构和来源时间校验 → 第二次模型事实核对 → 内容修订入库。历史重生成可复用原封面。
- 每篇包含“应用与工具”和“技术与开发”两个阅读分区，名称、说明及新闻归属保存到正文 JSON；空分区不显示，后台可调整归属。
- 模型输入、资料、图片提示词、请求标识、模型用量、阶段产物及失败原因保存在 job / run 中；密钥不进入快照。
- 图片上传失败复用已生成的本地 PNG，避免重复生成正文和图片。旧图片保留。
- 已发布版本与最新编辑版本分开；保存修改不会立即替换线上内容。

模型核对降低错误率，不代表保证每条事实正确；后台保留原始资料供检查。首版来源适配器支持 RSS / Atom，历史 `webpage` 来源作为追溯记录保留，不自动抓取任意网页。首版图片适配器接受 PNG 文件。

## 模型与资料配置

1. 在“资料来源”添加官方 RSS / Atom HTTPS 地址，点击“测试来源”。Feed 条目必须有明确的原文发布日期／时间和足够的正文／摘要；不会把 Atom 的 updated 时间当成原始发布日期。
2. 在任务中选择来源，配置两组模型：
   - 文字服务：选择 Codex CLI（使用当前服务账户的登录）或兼容 `POST /chat/completions` 的 API。
   - 图片服务：Codex 内置图片工具，或兼容 `POST /images/generations` 的 API。Codex 配图需要独立的 ChatGPT 登录；API 返回 PNG 的 `b64_json` 或 HTTPS 图片 URL。
   - API 地址应包含供应商 API 前缀，例如 `/v1`；模型名必须填写实际可用名称。
3. 密钥部署到服务器环境中，默认引用 `CONTENT_TEXT_API_KEY` 和 `CONTENT_IMAGE_API_KEY`。可用不同供应商；其他凭据引用需符合 `CONTENT_*_KEY` 命名。
4. 生成时间默认 08:30，计划发布时间默认 09:00，时区固定 Asia/Shanghai；允许延迟 180 分钟，失败最多自动重试三次。
5. 先试运行生成草稿。检查来源、正文和配图后，再启用自动执行与自动发布。

服务端的外部请求只使用 HTTPS，并检查地址、跳转、超时和响应大小。不能访问回环、内网或保留地址，带鉴权的模型请求不能跟随重定向。若本机使用会将公网域名解析为 `198.18.0.0/15` 的 Fake-IP 代理，需在部署环境使用正常公网 DNS；不能为了本地代理关闭地址校验。

接口参考：[文字生成](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[图片生成](https://developers.openai.com/api/reference/resources/images/methods/generate)。

## Codex CLI

在执行 worker 的服务账户下安装 Codex 并完成 `codex login`，用 `codex login status` 检查。后台文字生成方式选择“Codex CLI”；模型名可留空使用 CLI 默认模型，建议将超时设为 600 秒。服务进程的 PATH 必须包含 Codex，或设置 `CONTENT_CODEX_BIN` 为绝对可执行路径。Codex 方式无需 `CONTENT_TEXT_API_KEY`。

正文和事实核对分别调用 `codex exec`，通过 stdin 传入资料、`--output-schema` 约束 JSON 输出，使用临时目录、只读模式、关闭命令工具和网页搜索。子进程不继承数据库与七牛凭据。CLI 会读取服务账户已保存的认证；部署到新服务器后须独立完成登录。本模块不加载个人 config.toml；模型以任务配置为准。

官方说明：[Codex 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)。

### Codex 内置配图

后台图片生成方式选择“Codex CLI”。配置 `CONTENT_IMAGE_CODEX_HOME` 指向独立的绝对目录（服务器为 `/root/.codex-content-images`），并在该目录完成支持图片生成的 ChatGPT 账号登录。API 与 worker 都需要读取此环境变量。目录只允许服务账户访问，凭据不进入数据库或代码仓库。

Codex CLI 0.154.0 的内置 `image_gen.imagegen` 会检查 Codex 后端认证；普通 API Key 登录不能直接获得这一工具。仅安装 imagegen 技能或打开功能开关不能代替账号授权。远程服务器可采用[设备码登录或 SSH 回调登录](https://learn.chatgpt.com/docs/auth#login-on-headless-devices)。图片进程不继承文字通道的 API Key 和自定义模型地址，也不读取个人工具配置。

图片配置中的模型名是调用图片工具的 Codex 调度模型，可留空使用 CLI 默认值；实际图片模型由 Codex 内置工具管理。期望图片尺寸写入画面要求，保存尺寸以返回 PNG 为准。正文和事实核对继续使用原有文字账号。

一次调用只请求一张图片。适配器根据 CLI 返回的会话编号，从对应 `generated_images/<thread_id>/` 读取新图片；不采信模型文本中的任意路径，不采用其他会话的图片，不使用代码绘图兜底。PNG 完整性与大小校验通过后，图片复制到持久化资产目录，沿用七牛上传与重试机制。

启用前先验证账号权限、网络连接、生成和上传成功。缺少登录时，后台提示“Codex 配图账号登录”，不会创建任务；仅通过文件配置检查不等同于真实出图成功。自动发布仍由独立开关控制。

## 阅读分区与兼容

正文 `schema_version=2` 包含 `groups: [{id,title,description}]`；每条 `sections` 必须有 `group_id=applications|development`。定义来自任务版本的 `digest_groups`，随本期正文快照保存。后台编辑只允许选择有效分区，空分区不渲染；保留 v1 历史内容的平铺兼容。公开 API 保持 v1 基础契约并附加分区字段，旧前端仍可平铺阅读；新前端按分区字段组织内容。AI 简析允许为空。生成要求通常 800–1500 字，最低校验为 300 字以容纳资讯较少的日期。

## 首次部署

在服务器后端目录执行，沿用项目现有 Python 虚拟环境。该实现没有新增第三方 Python 依赖。

```bash
set +x
source /root/env.sh
export FLASK_CONFIG=production

# 先备份数据库。迁移只新增 7 张通用任务／内容表。
venv/bin/python main.py db upgrade

# 幂等导入已有五期内容及来源快照；公开 ai_digests 记录和地址保持不变。
venv/bin/python generation.py init

# 将 content.env.example 复制到服务器私有配置位置并填写密钥。
# 通过导出环境变量或部署平台 secret 注入进程，不能提交密钥到仓库。
set -a
source /path/to/private/content.env
set +a

# 检查缺失项，只输出变量名，不输出密钥。
venv/bin/python generation.py check

# 确认 pm2.content.json 的 cwd / interpreter 路径符合服务器实际目录。
pm2 start pm2.content.json
pm2 save
```

新增 API 模块需要按现有部署方式重启 `blog-server`。部署前端后访问两个后台入口。服务器开机恢复 PM2 使用现有的系统服务配置；若尚未配置，参照 [PM2 startup](https://pm2.keymetrics.io/docs/usage/startup/)。

`CONTENT_ASSET_DIR` 应设置为部署版本目录之外的持久化目录，所有 worker 共享该目录。PM2 只负责进程保活，数据库中的任务配置决定实际执行时间。服务端需保持系统时钟同步。

### 执行开关

- scheduler / worker 要求 `CONTENT_JOBS_ENABLED=1`。
- `CONTENT_ENVIRONMENT` 必须与命令行 `--environment` 相同。
- 执行生产任务还要求 `FLASK_CONFIG=production`。
- `pm2.content.json` 已设置上述生产标识；任务本身默认仍停用。
- 开发 worker 只领取 development 任务，不领取 production 任务；自动发布只在 production 环境执行。
- 手动试运行允许在任务停用时进行，但需要来源、模型与凭据配置完整，结果始终为草稿或候选修订。

## 运维命令触发

在服务器加载数据库、七牛和模型密钥环境后执行。`run` 只负责入队，返回 JSON 中的 `job_id`，实际生成由常驻 worker 完成；默认保留草稿。

```bash
export FLASK_CONFIG=production
export CONTENT_ENVIRONMENT=production

# 触发任务 1 的当期草稿。相同任务、日期和 request-id 重复调用只创建一个 job。
venv/bin/python generation.py run --task-id 1 --request-id ops-20260916

# 为指定日期手动生成草稿（不能指定未来日期）。
venv/bin/python generation.py run --task-id 1 --date 2026-09-16 --request-id backfill-20260916

# 使用该期已保存的来源记录重写，生成候选修订；不会抓取今天的消息。
# --reuse-cover 从数据库复用该期封面，因此不需要图片模型或七牛上传凭据。
venv/bin/python generation.py run --task-id 1 --date 2026-09-12 --mode regenerate --reuse-cover --request-id regroup-20260912

# 没有常驻 worker 时执行一个队列任务。
export CONTENT_JOBS_ENABLED=1
venv/bin/python generation.py worker --environment production --once

# 查询执行进度；将 23 替换为实际返回的 job_id。
venv/bin/python generation.py status --job-id 23

# 从已经保存的阶段产物重试失败任务。
venv/bin/python generation.py retry --job-id 23

# 触发当期定时任务，遵守任务启用、执行窗口和发布策略；不会立即强制发布。
venv/bin/python generation.py run --task-id 1 --mode scheduled
```

成功退出码为 0，参数或配置错误为 2。成功表示已入队或已查到状态，不代表生成完成。`status` 是只读命令；`run` / `retry` 需要执行环境标识与目标任务一致。配置尚未完整时触发会返回明确错误，不创建空白文章。

历史重生成在入队时冻结来源修订和封面，保留来源原始日期精度；只使用该期之前的文章进行重复性比较。结果创建新修订，原已发布版保持可读，在内容管理中发布新版本后才替换。相同 request-id 不得改变封面复用选项。

已有管理员 HTTP 接口也可用于运维系统：`POST /api/generation/tasks/:id/run/`（请求包含 `edition`、`purpose=test|regenerate`、`request_id`，重生成可携带 `reuse_cover=true`），`GET /api/generation/jobs/:id/` 查询进度，`POST /api/generation/jobs/:id/` 携带 `{"action":"retry"}` 重试。需要有效的管理员鉴权，AI 聊天访问 Key 不具备这些权限。

本地使用 SSH 隧道时：先加载 `../env.sh` 与 `../env.local.sh`，确认 `DEV_DATABASE_URL` 已指向线上 `blog` 后，可在当前命令会话中 `export DATABASE_URL="$DEV_DATABASE_URL"`，再设置生产任务执行标识；不修改凭据文件。服务器正常部署直接使用自己的 `DATABASE_URL`。

## 故障处理

- **没有执行进程**：后台心跳显示缺失／过期，检查 PM2 日志和进程环境。仅打开后台页面不会启动 worker。
- **模型未配置／凭据无效**：补充模型配置和部署凭据；配置变更产生新版本，已经排队的 job 保留旧版本。需要使用新配置时新建试运行。
- **暂时性失败**：2、5、15 分钟退避，最多三次自动重试；重试仍受发布截止时间限制。
- **事实核对未通过**：执行详情的 `checkpoint.review` 保存问题；用新的 request-id 再次执行 regenerate，会将最近一次审核问题加入生成输入。重新生成会创建候选修订，不覆盖线上版本。
- **图片上传失败**：恢复七牛配置后重试，同一 job 复用已保存的图片文件。不要删除资产目录。
- **worker 崩溃**：120 秒租约到期后 scheduler 恢复任务；旧进程的迟到结果不能提交。
- **超过截止时间**：停止自动尝试，管理员可手动重新生成草稿或发布已有内容，不伪造实际发布时间。
- **停用任务**：取消该任务尚未完成的定时 job，并停止到期自动发布；手动试运行可在执行详情单独取消。
- **外部请求超时**：供应商可能已经生成并计费。记录客户端请求编号供排查；并非所有兼容接口支持查询或幂等请求，因此不承诺模型调用恰好一次。

邮件订阅投递没有在这次生成流程中启用，沿用现有独立订阅表，后续接入投递服务。

## 验证

```bash
venv/bin/python -m unittest discover -s tests -v
cd ../blog-next
npx tsc --noEmit
```

后端测试在隔离的 SQLite 数据库运行，外部模型和上传使用测试替身，不向线上写入测试文章。真实服务调用使用已登录的 Codex 或已配置的 API，以一次草稿试运行验证。

首次迁移数据库变更前快照：`blog-assets/ai-digest/generation-migration-20260916-133825.json`。已对五篇公开快讯整行数据进行前后 SHA-256 对比，内容、状态、日期与图片 URL 未改变。

## 2026-09-16 分区升级与历史重生成

任务 1 已保存 Codex 文字模型与两个阅读分区配置。9 月 12—16 日五期通过 `generation.py run --mode regenerate --reuse-cover` 重写，并通过独立事实核对后发布。9 月 12、14 日原始资料均偏技术，仅展示技术分区；其余三期展示两个分区。公开地址、期次与原发布时间保留，封面沿用数据库原记录。新修订对应 job 7、8、4、5、6；两次未通过事实核对的尝试保留在执行记录中，未发布。

重生成前备份：`blog-assets/ai-digest/grouped-regeneration-20260916-141945.json`。自动日更任务仍停用；部署自动生成还需配置 RSS 来源、图片服务及常驻进程。

## 国内动态与持续采集（2026-09-16）

- 实际来源保存在数据库：IT之家（标题关键词筛选）、量子位，以及 OpenAI、Google AI、GitHub 官方订阅。国内外按新闻价值选题，无地域配额。后台可调整标题关键词，留空表示不限主题。
- `content-collector` 每 15 分钟采集当前环境任务所选且已启用的来源，保存最近 7 天内的新闻；任务停用时也积累资料，但不生成或发布。短 RSS 列表滚动后，前一天的消息仍可在数据库中找到。
- 每次生成按来源轮流取候选，避免高频综合源挤占全部输入；新闻本身仍由编辑要求筛选、合并。采集失败按来源记录，其他来源继续运行。
- 当日期次补录与历史期次均不超过北京时间该期 09:00；提前生成以实际开始时间为上限。首次上线与后续发布会、重大更新分别判断新闻价值。
- 漏报修订可通过下面的指令补充已核实的数据库资料。补充资料需包含正文摘要、准确发布时间和原链接，并落在原采集窗口内；资料冻结进新 job，不改旧修订或原始发布时间。

```bash
venv/bin/python generation.py run --task-id 1 --date 2026-09-16 \
  --mode regenerate --reuse-cover --include-item-id 18 \
  --request-id domestic-supplement-20260916
```

重生成仍保存候选修订，检查通过后在内容管理发布。已有阅读量与旧图片保留。

### 显式使用服务器现有 Codex 服务

生成进程不加载个人 `config.toml`。如果服务账户通过自定义模型服务登录，需要在服务器私有环境中设置 `CONTENT_CODEX_BASE_URL`，沿用该账户已有的模型服务地址；程序将其作为 Codex 的 `openai_base_url` 显式传入，认证仍使用该账户已保存的登录。URL 不得包含密钥、用户名或密码，数据库和七牛凭据仍不会传给 Codex 子进程。

### 构建与部署

当前服务器内存约 3.7 GB 且承载 MySQL、API 与网站，生产构建放在开发机或 CI 完成。使用与线上一致的 Next.js 版本、`NEXT_PUBLIC_*` 和 `INTERNAL_API_BASE_URL`，设置 `NEXT_DIST_DIR=.next-release-<commit>`。上传去除 cache 的产物，校验 SHA256 后先在回环地址的临时端口检查页面和静态资源，再通过 `NEXT_DIST_DIR=... pm2 restart blog-next --update-env` 切换，并 `pm2 save`。保留上一版构建目录用于回退。
