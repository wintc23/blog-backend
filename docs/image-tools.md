# 图片工具

## 已实现范围

统一 Image 2.5 基础能力、后台系统模板、前台独立工具。初始工具为卡通画生成、老照片修复、自由生图。后续新增工具通过后台配置，无需新增前台路由。前台不出现“模板”。

- `/tools` 工具列表，作品页同时展示已发布工具。
- `/tools/{slug}` 独立工具和分享入口；提示词只保存在后台。
- `/tools/tasks` 同账号历史任务；`/tools/tasks/{id}` 私有草稿、生成、对比、版本和下载。
- `/tools/upload#token` 手机/其他设备上传；token 放片段中，不进入网页请求或 Referrer。
- `/tools/share/{token}` 主动选择结果的七天分享，不包含原图、prompt 或账号信息；可撤销。
- `/manage/image-tools` 系统模板配置、发布、版本和最近任务。

桌面支持本地文件选择、拖拽、粘贴，以及扫码上传。识别设备时结合 UA、触控和主要指针，不用窗口宽度决定设备类型。手机使用相册入口；平板使用“从其他设备上传”。二维码点击后生成，15 分钟有效，关闭对话框、重新生成二维码或提交任务均使旧上传入口失效。扫码端不能查询任务私有内容、删除原图或提交生成。

## 数据与执行

`image_tools` 保存当前配置，`image_tool_tasks` 保存提交时配置快照和工具版本，`image_tool_assets` 保存私有图片，`image_tool_items` 独立执行每张图片并保留重新生成版本。提交请求幂等，重试按张计入限频。生成队列与 Web 请求分开，页面关闭不会取消任务。

采用已存在的 CPA HTTP 服务，只调用 `gpt-image-2.5-flare`。其他环境模型设置不能覆盖本模块模型，也不回退到其他版本。纯文本使用 generations，带图片使用 edits。服务器配置 `CONTENT_CPA_BASE_URL`、`CONTENT_CPA_API_KEY`，沿用现有受限本机连接策略；公开网站域名完全独立。

后台可配置画幅：自动适配、1:1、3:2、2:3。分别使用 1024×1024、1536×1024、1024×1536；自动根据源图选择接近的比例，不承诺任意原图比例无损保持。卡通/修复逐张处理最多 10 张；自由生图最多 4 张结果和 1 张参考图。HEIC 尚不支持，用户需要先导出 JPG。

每张上传最大 20 MB、4000 万像素，接受静态 JPEG/PNG/WebP，校正 EXIF 方向。原文件私有留存；交给模型的是剥离元数据的无损 PNG 副本，结果也以 PNG 下载。图库公开上传接口不复用于这些私人照片。

图片存储由 `IMAGE_TOOLS_STORAGE` 指定，默认 Flask instance 目录的 image-tools 子目录。Web 和 worker 必须使用同一存储卷和数据库，目录不得配置成公开静态目录。签名图片 URL 15 分钟有效；所有 URL 从配置的 API 地址、当前页面 origin 或相对路径构造，不写死网站域名。

原图和结果默认保留，用户删除后立即撤销访问并进入清理队列。七天未修改草稿和已删除任务由 cleanup 清理；孤立文件一天后回收。分享七天后失效，任务仍由原用户保留。ZIP 包含当前任务所有成功版本，超过 300 MB 时提示逐张下载。

## 本地安装与运行（不部署）

先按项目现有方式加载本地环境，再执行：

```bash
cd blog-backend
venv/bin/python main.py db upgrade
venv/bin/python image_tools.py seed
venv/bin/python image_tools.py worker
```

seed 只补充缺失的默认工具，不覆盖管理员配置。worker 为独立进程；建议首版运行一个 worker，使 Image 2.5 串行调用。多进程通过条件更新抢占单个子任务，不重复领取。调用超时或进程崩溃后的任务标记“结果待确认”，不会自动重发模型请求，避免重复消耗；用户可明确重试。

后台“限频设置”默认全站每分钟启动 10 张、每个普通账号每小时最多 10 张，均可实时配置（1–10000）。管理员跳过两项限额，且不消耗普通用户全站额度。批量按张计算，重新生成也计数。使用滚动 60 秒 / 3600 秒窗口，不按整分钟/整点清零。

提交和重试在账号行锁内检查额度；worker 在共享设置行锁内预留启动名额，防止多进程并发绕过全站限制。账号在执行前还会再次检查每小时实际启动数，避免较早排队的任务集中启动。达到全站阈值的任务保持排队；账号提交超限返回 HTTP 429、Retry-After、剩余额度和重试时间。启动计数在请求服务之前提交，即使超时或中断也不自动退还，避免通过故意制造失败绕过限频。

提交新任务时，总排队子任务容量上限为 1000；上传仍有账号级频率、格式、文件大小和像素限制。游客可自动登录后创建任务，并额外按网络共享额度；跨设备继续任务需使用同一正式账号，或通过限时手机上传入口补充图片。删除任务立即撤销图片访问；计数记录保留到提交和执行的限频窗口均已结束，防止通过删除再清理恢复额度。

每天执行：

```bash
venv/bin/python image_tools.py cleanup
```

前端遵循生产构建流程：`npm run dev`，不启动 `next dev`。配置的后端必须已经完成 migration/seed；如生成服务未配置，前台允许准备草稿但不能开始生成。

## 验证

```bash
venv/bin/python -m unittest discover -s tests -p test_image_tools.py
```

测试使用隔离 SQLite 和私有临时目录，图片服务替身不会发起外部付费请求。上线前需另外接入真实 CPA 做文字生成、卡通转换、照片修复各一次效果验收，确认其 Image 2.5 路由与画幅支持。不得将测试替身输出当成模型生成结果。

## 接口

公开：GET `/api/image-tools/`、`/api/image-tools/{slug}/`。
登录：GET/POST `/api/image-tasks/`；GET/PATCH/DELETE `/api/image-tasks/{id}/`。
任务动作：POST `assets/`、`submit/`、`items/{item}/retry/`、`clone/`、`handoff/`、`share/`；GET `download/`。
撤销：DELETE `handoff/`、`share/`；删除输入 DELETE `assets/{asset}/`。
跨设备：GET/POST `/api/image-upload-session/`，携带 `X-Image-Upload-Token`。
分享：GET `/api/image-shares/{token}/`；图片经 `/api/image-assets/{id}/?ticket=...` 受限访问。
后台：GET/POST `/api/image-tools/admin/templates/`、GET `/api/image-tools/admin/jobs/`；GET/PUT `/api/image-tools/admin/limits/`。

Lark 等调用方可复用这些 HTTP 接口及已有认证。生成结果不会自动发布成网站动态；公开发布应另外走原有审核流程。


### 简洁前台与后台配置

工具前台默认只展示输入和结果，进入工具即显示工作台，首次上传/手机上传/生成时自动创建任务。补充提示词、可选画幅和画风放在默认折叠的高级设置中，后端只接受模板允许范围内的参数。数量由后台固定配置（default_count）控制，逐张处理始终是一张原图对应一张结果（并非强制正方形）。逐张处理模式每张输入对应一张输出，文字生图使用后台设定的数量。任务继续保存创建时的配置快照。后台提供封面上传、可视化配置、发布状态、限频设置和任务记录。默认封面使用前端 /images/tools/ 下的 WebP 资源，不依赖部署域名。

本地 `start.sh` 会读取 `../env.local.sh`，准备数据库连接及图片队列。若配置 `IMAGE_TOOLS_CPA_SSH_HOST`，会按 `CONTENT_CPA_BASE_URL` 中的本地端口建立 SSH 转发；目标端口由 `IMAGE_TOOLS_CPA_REMOTE_PORT` 指定（默认 8317）。所有连接配置都来自环境，不绑定网站域名。密钥只保存在未提交的本地环境文件。

游客上传自动建立身份，允许创建任务、上传和提交。常规账号与全局限频保持生效；游客额外按 IP 共享每小时图片额度（沿用后台每账号每小时配置），批量和重试按实际图片张数预占，提交失败回滚。同一 IP 的游客创建任务/复制任务 30 次每小时、上传 100 次每小时。手机上传令牌依旧只有上传权限。管理员保持不限。
