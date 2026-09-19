# 互动通知

点赞（文章、AI 动态、生活动态、图片工具、生图分享）、留言、评论及其回复，统一通知站点管理员：邮件发给 `FLASK_ADMIN`，飞书通过现有 `lark-cli` 的机器人身份发给显式配置的 `LARK_BRIDGE_OWNER_OPEN_ID`。普通被回复者原有的站内／邮件提醒继续保留。管理员自己的操作不通知自己。

## 运行

先执行数据库迁移，再启动网站和独立 worker：

```sh
python main.py db upgrade
python notifications.py
```

worker 继承网站数据库及邮件环境，另外配置：

- `LARK_BRIDGE_CLI`：CLI 路径，默认 `lark-cli`。
- `LARK_BRIDGE_APP_ID`：固定应用 profile。
- `LARK_BRIDGE_OWNER_OPEN_ID`：固定收件人；不得从当前 CLI 登录态推断。
- `NOTIFICATION_SITE_URL`：可选公开站点地址，默认取网站 `DOMAIN`；支持路径前缀，不推断请求 Host 或硬编码域名。
- `HOME`、`PATH`：应允许 worker 读取已经登录的 CLI 凭据并运行 CLI。

通知 worker 只负责发送，不启用飞书管理指令监听或审核卡片处理。生产进程名称为 `interaction-notifications`，由 PM2 守护。配置中的秘密不得提交仓库。

## 投递规则

业务数据和邮件／飞书两条通知在同一数据库事务中提交，HTTP 请求不等待网络发送。每个渠道独立记录成功或失败，失败按指数退避自动重试，最长间隔 6 小时；单次投递最多 90 秒。worker 崩溃后租约 5 分钟到期可继续处理。

重复请求、取消点赞不触发通知。同一用户对同一内容取消后重新点赞也不会重复提醒。历史互动不补发。游客现有限频继续生效。

飞书使用稳定消息 UUID 去重。SMTP 在“服务商已接收、数据库尚未确认成功”时遇到崩溃，极端情况下可能重复邮件；无法保证跨 SMTP 与数据库的绝对单次投递。

生图分享通知跳转管理员任务页，不存储分享口令、签名图片地址或图片。用户文本用邮件 HTML 转义和飞书 plain_text 呈现；成功投递后清空队列正文。错误只保存异常类型，不保存服务商响应或凭据。

排查 `interaction_notifications` 的 `channel`、`attempts`、`available_at`、`sent_at`、`last_error`。`sent_at` 为空表示待发送；修复配置后可等自动重试，也可由运维将对应 `available_at` 调整为当前时间。
