# 生活动态

- 动态列表 `/moments`，独立详情 `/moments/<UUID>`，后台 `/manage/moments`。
- 正文为纯文本（最多 2000 字），统一放在图片上方；图片最多 9 张，按保存顺序展示，每张可填写 200 字描述。没有图片也可发布。
- 图片上传沿用受管图片接口：静态 JPG / PNG / WebP，单张 5 MB；描述使用普通文本渲染，不执行 HTML。
- 后台按北京时间填写时间，API `occurred_at` 使用包含时区的 ISO 8601，数据库保存 UTC。`date` 为北京时间对应日期，避免访客时区改变分组。
- 原有仅日期的动态保留 `occurred_at = NULL`，不虚构时分；原单图及描述迁入 `images_json`。
- `GET /api/life-moments/?group_by=date&page=1&per_page=7` 按有动态的日期分页，同一天不跨页。返回 `groups`、动态总数 `total`、日期数 `total_dates`、实际 `page`、每页日期数 `per_page`。前台每页 7 个日期，倒序排列；同日按发生时间、创建时间、ID 倒序排列。页码超界收敛到最后一页。
- 未传 `group_by` 时仍按条数分页，用于首页最新 3 条和后台每页 12 条。无动态时首页不显示动态模块。
- `GET /api/life-moments/<UUID>/` 公开访问；发布、修改、删除仍需管理员权限。
- `images_json` 与兼容旧客户端的首图 `image_url` 都参与媒体引用跟踪。移除图片或删除动态时，只有本站受管图片失去最后一个引用才删除；外站图片不会被删除。
- 数据库迁移：`20260917_moment_gallery`，前置 `20260917_rich_comments`。先升级数据库，再重启后端和更新前端。
