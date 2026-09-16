# 行业动态阅读量

- 阅读量存于 `ai_digests.read_times`，独立于正文与修订；重新生成、编辑、发布不会清零。
- 首页、历史列表、详情页及内容管理显示数据库中的阅读量。
- 公开详情页首次在当前页面访问中可见时，浏览器携带现有登录凭据调用 `POST /api/ai-digests/<id>/read/`。服务端原子递增，响应 `{read_times, counted}`。
- 每次打开或刷新详情页计一次访问；同一次挂载的重复 effect、切换标签页不会重复发送。该指标是页面阅读次数，不是独立读者数，也不做阅读完成率或反作弊统计。
- 管理员（站长）登录后不计入；携带失效或无法识别的凭据时也跳过，防止误当游客。未登录且没有凭据时无法识别站长身份。
- GET 请求、服务器渲染、页面元数据、链接预取、后台预览不增加阅读量；草稿、撤回及尚未到发布时间的内容拒绝统计。
- 新增字段迁移：`python main.py db upgrade`。既有动态从 0 开始；不推测或回填历史访问。

前端按 [React effect 生命周期](https://react.dev/reference/react/StrictMode#fixing-bugs-found-by-re-running-effects-in-development) 清理监听与异步回调，同一次访问复用统计请求。统计失败不影响阅读。
