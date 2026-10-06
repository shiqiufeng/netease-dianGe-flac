# 网易云音乐点歌-flac

AstrBot 网易云音乐点歌-flac 插件：搜索网易云音乐，配合你自部署的 [UnblockNeteaseMusic-utils](https://github.com/NeteaseCloudMusicApiEnhanced/UnblockNeteaseMusic-utils) 解锁服务获取可播放直链，支持 QQ 音乐卡片 / 音乐文件 / 语音 / 文本链接四种发送方式。

> 插件不含任何解锁服务器地址，`API 地址` 由使用者自行部署、自行填写，部署教程见下文。

## 功能

- `点歌 <歌名>`：搜索网易云音乐，回复序号选歌（或配置为自动点第一首）
- `点歌 <序号>`：直接选择上一次搜索结果的第 N 首
- `解锁 <歌曲ID或分享链接>`：直接解锁指定歌曲，支持 `https://music.163.com/song?id=xxx` 格式的链接
- `点歌模式 [卡片|文件|文本]`：查看或切换发送方式（保存进配置，重启保留）
- `帮助`：查看使用帮助与当前模式
- **卡片模式**：QQ 音乐卡片，自定义卡片被拒时自动尝试网易云官方 163 卡片，再回退文本链接
- **文件模式**：下载音乐按「歌名 - 歌手.flac/mp3」命名发送，发送后本地文件默认 60 秒自动删除；自动内嵌 LRC 歌词、专辑封面与标题/歌手/专辑标签
- **语音模式**：以 QQ 语音消息发送，临时文件发送后立即清理
- 四个定向命令 `点歌卡片/点歌文件/点歌语音/点歌消息`：单次指定发送方式，不改默认配置
- 收到命令时在用户消息上贴表情回应（👍 可自定义，NapCat/Lagrange 群聊有效）
- 搜索结果列表发送 60 秒后自动撤回（可配置/关闭）
- 免前缀触发：所有命令加不加唤醒前缀 `/` 都能用（可配置强制前缀）
- 受限 / VIP 歌曲通过解锁服务获取直链；非 aiocqhttp 平台（如 Telegram）自动降级为文本 + 直链

## 指令

| 指令           | 说明                         |
| ------------ | -------------------------- |
| `点歌 <歌名>`    | 搜索歌曲，列出结果后回复序号选择，回复 `0` 取消 |
| `点歌 <序号>`    | 直接选择上一次搜索结果中的第 N 首         |
| `点歌卡片 <歌名>` | 本次以 QQ 音乐卡片发送             |
| `点歌文件 <歌名>` | 本次以音乐文件发送（按歌名命名）         |
| `点歌语音 <歌名>` | 本次以语音发送                  |
| `点歌消息 <歌名>` | 本次以文本链接发送                |
| `解锁 <ID/链接>` | 按歌曲 ID 或分享链接直接解锁           |
| `点歌模式 [卡片\|文件\|语音\|文本]` | 查看/切换默认发送方式，无参数显示当前模式 |
| `帮助`         | 查看使用帮助、当前模式与作者信息           |

以上所有命令默认**直接发即可（无需 `/` 前缀）**，带 `/` 也照常可用；如想强制要求前缀，在插件配置里开启「命令必须加 / 前缀」。

## 配置

| 配置项          | 默认值                       | 说明                                                 |
| ------------ | ------------------------- | -------------------------------------------------- |
| `API 地址`（unlock_api） | 空 | 你部署的 UnblockNeteaseMusic-utils 地址，末尾不带 `/`。**项目地址：https://github.com/NeteaseCloudMusicApiEnhanced/UnblockNeteaseMusic-utils ，部署教程见下文** |
| `代理`（proxy）     | 空                         | 访问解锁服务走的代理，如 `http://127.0.0.1:7897`。仅解锁走代理，搜索仍直连 |
| `音源优先级`（source）     | `byfuns,ddyr,auto`         | 逗号分隔按顺序尝试。byfuns/ddyr 默认请求无损；`auto`=服务端自动（bugpk 兜底） |
| `校验音频直链`（verify_audio） | `true`                   | HEAD 探测直链过滤 VIP 占位 HTML 假链接 |
| `自动点第一首`（auto_pick）  | `false`                   | 开启后点歌直接发送第一首结果，不再列序号                               |
| `搜索结果数量`（limit）      | `10`                      | 单次搜索返回数量                                             |
| `请求超时`（timeout）    | `15.0`                    | API 请求超时（秒）                                         |
| `歌曲发送模式`（send_mode） | `card`                        | `card` 卡片 / `file` 文件 / `text` 文本，可用「点歌模式」命令切换 |
| `文件删除延迟`（delete_file_seconds） | `60`                | 文件模式发送后本地文件删除延迟（秒），`0` 保留 |
| `列表自动撤回`（retract_list_seconds） | `60`              | 搜索结果列表 N 秒后自动撤回，`0` 关闭 |
| `命令表情回应`（react_emoji） | `319`（比心）                    | 命令贴表情的 ID/Unicode 码点（319=比心、128077=👍、49=强），留空关闭 |
| `命令必须加 / 前缀`（require_prefix） | `false`               | 开启 = 必须 `/点歌`；关闭 = 直接发 `点歌` |

## 部署解锁服务教程

插件需要一个 [UnblockNeteaseMusic-utils](https://github.com/NeteaseCloudMusicApiEnhanced/UnblockNeteaseMusic-utils) 服务作为音源后端，任选一种方式部署：

### 方式一：Vercel 部署（推荐，免费免服务器）

1. 打开 https://github.com/NeteaseCloudMusicApiEnhanced/UnblockNeteaseMusic-utils ，点右上角 **Fork** 到自己账号；
2. 打开 https://vercel.com 并用 GitHub 账号登录，**Add New → Project**，导入刚 Fork 的仓库，保持默认直接 **Deploy**；
3. 部署完成后会得到 `xxx.vercel.app` 地址（大陆直连可能不通），建议在 Vercel 项目的 **域名** 设置里绑定自己的域名（Cloudflare 托管的域名加一条 CNAME 指向 Vercel 即可）；
4. 浏览器打开你的域名，看到「UnblockNeteaseMusic Utils」状态页即部署成功；
5. 把该地址填进插件配置的 `API 地址`。

### 方式二：服务器 / 本机源码运行

需要 Node.js 18+，和 AstrBot 同机部署最安全（不必暴露公网）：

```bash
git clone https://github.com/NeteaseCloudMusicApiEnhanced/UnblockNeteaseMusic-utils.git
cd UnblockNeteaseMusic-utils
npm install
npm start        # 默认 3000 端口，可用 PORT=4000 npm start 换端口
```

验证：`curl "http://127.0.0.1:3000/inner/version"` 返回版本 JSON 即成功。此时 `API 地址` 填 `http://127.0.0.1:3000`。

### 部署后验证

```bash
curl "<你的API地址>/inner/version"      # 应返回 {"code":200,"data":{"version":"..."}}
curl "<你的API地址>/match?id=1498523311" # 应返回包含直链的 JSON
```

## 安装插件

1. 在本仓库 **Releases** 下载最新的附件（`网易云音乐点歌-flac`）；
2. AstrBot WebUI → 插件管理 → 从文件安装，选择该 zip；
3. 在插件配置里把 `API 地址` 填成你的解锁服务地址，重载插件即可使用。

## 常见问题

- **提示"解锁失败"**：先在**运行 AstrBot 的机器**上确认解锁服务可达：`curl "<你的API地址>/inner/version"`，正常应返回版本 JSON。若本机网络对该域名 TLS 握手被重置（部分网络对非常见后缀域名有干扰），可在插件配置 `代理` 里填本机代理（如 Clash 的 `http://127.0.0.1:7897`），只有解锁请求走代理，搜索仍直连。
- **卡片能发出来但无法播放**：多为音源直链失效，可把 `音源优先级` 固定为其他音源试试。
- **VIP 歌曲提示"解锁失败"**：当前各镜像音源没有 VIP 曲库。要解锁 VIP 歌需再部署 [NeteaseCloudMusicApi api-enhanced](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced) 并配置 VIP 账号 Cookie 的 `song/url/v1` 接口。
- **音乐卡片 / 表情回应仅 aiocqhttp 平台支持**（NapCat / Lagrange / go-cqhttp），卡片失败会自动回退；私聊贴表情在部分协议端不生效。

## 作者

1. 流水
2. 听雨的蛙
3. 落雪
4. GLM-5.3-Flash

## 致谢

- [UnblockNeteaseMusic-utils](https://github.com/NeteaseCloudMusicApiEnhanced/UnblockNeteaseMusic-utils) — 音源匹配 / 解锁服务
- [astrbot\_plugin\_ncm\_directlink](https://github.com/monbed/astrbot_plugin_ncm_directlink) — 交互流程参考
- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
