# 画册与手机发布

执行 `python main.py db upgrade` 后再重启 API 和图片清理 worker。迁移只新建 albums、album_photos、album_items、device_logins 四张表，不移动图片。

画册仅管理员可以创建、编辑、上传和加入图片；普通账号与游客只能浏览公开画册。素材库和我的画册接口同样检查管理员权限。画册默认 private，仅创建它的管理员可读写；public 进入公开列表。画册与素材多对多关联，跨画册重复添加不复制云对象。删除画册或移出照片保留素材库、动态及其它画册。画册元数据和排序用 version 防止覆盖并发编辑。

素材仅允许通过来源 ID 引用自己的生图资产、自己的已上传图片、已发布动态图片或自己的素材库。新画册上传经浏览器直传七牛私有空间，后端在内存校验并去除元数据，不持久化文件或签名地址。保留任务图片的素材会阻止任务清理删除对应七牛对象；公开媒体引用由 MediaReference 统一计数。

`GET /albums/` 支持 scope=public|mine、page；单页 24 本。每账号最多 100 本，每本最多 500 张，每次添加最多 100 张。`/album-sources/` 支持 photo、generated、moment、media。`/album-uploads/` 授权每账号每小时最多 30 次，输入最大 5 MB，浏览器先转换与优化。

图片展示使用 10 分钟的应用访问票据，再重定向至短时七牛地址。公开访问票据包含画册版本；改私密或移除照片后旧应用票据不再可用。已经签发的七牛地址最多继续有效 60 秒；已经下载的公开图片无法撤回。应用票据、七牛签名、文件名、图像内容不进入业务埋点。

手机登录二维码有效 5 分钟，URL fragment 仅携带扫描秘密，不携带账号 JWT。手机生成自己的随机领取凭证，扫描后显示确认码；电脑登录用户核对并批准，只有领取设备可以兑换一次 24 小时登录令牌。关闭/重新生成二维码取消未完成请求，账号 auth_version 变更使旧请求失效。不硬编码部署域名。

服务端 StatEvent 记录 album.create、album.upload、album.add_photos、album.visibility、album.delete，仅记录数量/可见范围等业务字段。测试使用独立 SQLite 与模拟七牛：`venv/bin/python -m unittest discover -s tests -p 'test_albums.py'`。
