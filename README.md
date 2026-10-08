# 网易云音乐点歌-flac

AstrBot 网易云音乐点歌-flac 插件：搜索网易云音乐，直链**优先交给你自己的洛雪音乐（LX Music）音源脚本**，取不到再回落到自部署的 [UnblockNeteaseMusic-utils](https://github.com/NeteaseCloudMusicApiEnhanced/UnblockNeteaseMusic-utils) 音源服务。支持 QQ 音乐卡片 / 音乐文件 / 语音 / 文本链接四种发送方式。

> ⚠️ 本插件仅供学习交流使用，请在体验后支持正版音乐。

> 插件不含任何音源服务地址与音源脚本，`洛雪音源脚本` 与 `API 地址` 都由使用者自行提供，配置见下文。

## 功能

- `点歌 <歌名>`：搜索网易云音乐，回复序号选歌（或配置为自动点第一首）
- `点歌 <序号>`：直接选择上一次搜索结果的第 N 首
- **优先原唱**：搜索结果前排常被翻唱/改编版占据（搜「稻香」第一条是「稻香(深情版)」，周杰伦原唱甚至不进前 10；搜「告白气球」前 50 条有 45 条翻唱，原版被压到第 6 页）。插件会按相关度重排、读原唱标注 `originSongSimpleData` 直接换成原唱，必要时翻页深扫按「专辑发行时间最早」把原版捞回来；实在找不到就明确提示是翻唱版。想点翻唱版就在关键词里写明（如 `点歌 稻香 深情版`），可在配置里关闭
- **洛雪音源优先（你自己的第三方音源）**：版权下架的歌（周杰伦等）网易云自己都不给放，插件会先按「歌名 + 歌手」查出**酷我歌曲 ID**，再交给你配置的**洛雪音乐（LX Music）音源脚本**换直链——音源由你的脚本出，插件不自己连任何音乐平台。取不到才回落到 `API 地址` 音源服务兜底，两边都没有才报错，**静默换源、不发任何提示文字**
- `排行` / `歌手` / `专辑` / `歌单` / `新歌` 等列表同样可以直接**回复序号点歌**，无需重打歌名
- `直链 <歌曲ID或分享链接>`：按歌曲 ID 或分享链接获取直链，支持 `https://music.163.com/song?id=xxx` 格式的链接
- `点歌模式 [卡片|文件|文本]`：查看或切换发送方式（保存进配置，重启保留）
- `帮助`：查看使用帮助与当前模式
- **卡片模式**：QQ 音乐卡片，自定义卡片被拒时自动尝试网易云官方 163 卡片，再回退文本链接
- **文件模式**：下载音乐按「歌名.flac/mp3」命名发送，发送后本地文件默认 60 秒自动删除；自动内嵌 LRC 歌词、专辑封面与标题/歌手/专辑标签
- **语音模式**：以 QQ 语音消息发送，临时文件发送后立即清理
- 四个定向命令 `点歌卡片/点歌文件/点歌语音/点歌消息`：单次指定发送方式，不改默认配置
- **每个命令**触发时都会在用户消息上贴表情回执（👍 可自定义，NapCat/Lagrange 群聊有效）
- 插件发出的**所有文字消息**（列表/歌词/提示/报错）发送 60 秒后自动撤回（可配置/关闭，仅 aiocqhttp）
- 免前缀触发：所有命令加不加唤醒前缀 `/` 都能用（可配置强制前缀）
- 受限 / VIP 歌曲通过你的洛雪音源脚本（或音源服务）获取直链；非 aiocqhttp 平台（如 Telegram）自动降级为文本 + 直链

## 指令

| 指令           | 说明                         |
| ------------ | -------------------------- |
| `点歌 <歌名>`    | 搜索歌曲，列出结果后回复序号选择，回复 `0` 取消 |
| `点歌 <序号>`    | 直接选择上一次搜索结果中的第 N 首         |
| `点歌卡片 <歌名>` | 本次以 QQ 音乐卡片发送             |
| `点歌文件 <歌名>` | 本次以音乐文件发送（按歌名命名）         |
| `点歌语音 <歌名>` | 本次以语音发送                  |
| `点歌消息 <歌名>` | 本次以文本链接发送                |
| `直链 <ID/链接>` | 按歌曲 ID 或分享链接获取直链           |
| `点歌模式 [卡片\|文件\|语音\|文本]` | 查看/切换默认发送方式，无参数显示当前模式 |
| `帮助`         | 查看使用帮助、当前模式与作者信息           |

以上所有命令默认**直接发即可（无需 `/` 前缀）**，带 `/` 也照常可用；如想强制要求前缀，在插件配置里开启「命令必须加 / 前缀」。

## 配置

| 配置项          | 默认值                       | 说明                                                 |
| ------------ | ------------------------- | -------------------------------------------------- |
| `洛雪音源脚本`（lx_source） | 空 | **优先用它取直链**。你那套洛雪音乐（LX Music）音源 `.js` 的路径：填目录（自动取里面的 `.js`）或具体文件，多个用逗号分隔。插件用 node 加载脚本，按「歌名+歌手」查酷我 ID 后交给音源换直链——**音源由你的脚本出，插件不自己连音乐平台** |
| `node 可执行文件`（lx_node） | `node` | 跑音源脚本用的 node，默认走 PATH；找不到就填绝对路径，如 `C:\Program Files\nodejs\node.exe` |
| `音源请求音质`（lx_quality） | `320k` | 向音源脚本请求的音质：`128k` / `320k` / `flac` / `flac24bit` / `hires`，看音源支持哪些 |
| `API 地址`（unlock_api） | 空 | **兜底用**：洛雪音源取不到时才回落到这里。填你部署的 **[UnblockNeteaseMusic-utils](https://github.com/NeteaseCloudMusicApiEnhanced/UnblockNeteaseMusic-utils)** 地址（末尾不带 `/`，如 `https://wy.example.com`）。插件会先请求它的 `/inner/modules` 认出后端并拿到实际音源清单，再走 `/match?id=&source=` 取直链；旧的 api-enhanced（`/song/url/match`）也仍然兼容。部署教程见下文 |
| `代理`（proxy）     | 空                         | 访问音源服务走的代理，如 `http://127.0.0.1:7897`。仅音源请求走代理，搜索仍直连 |
| `音源优先级`（source）     | `byfuns,ddyr,auto`         | 逗号分隔，按顺序用**音源服务上的哪个音源**（插件自己不去连任何平台）。可选：`byfuns`、`ddyr`、`msls`、`oi`、`qijieya`、`gdmusic`、`unm`；`auto` = 让服务端遍历它自己的全部音源。**写错/服务上没有的名字会被自动跳过**（日志里会写当前服务实际有哪些）。`unm` 是唯一跨平台去找别家的音源，想放周杰伦这类版权下架原唱得靠它 |
| `校验音频直链`（verify_audio） | `true`                   | HEAD 探测直链过滤 VIP 占位 HTML 假链接 |
| `自动点第一首`（auto_pick）  | `false`                   | 开启后点歌直接发送第一首结果，不再列序号                               |
| `优先原唱`（prefer_original） | `true`                 | 自动把搜索前排的翻唱替换成网易云标注的原唱；关键词里写明版本（如 `稻香 深情版`）时不干预 |
| `搜索结果数量`（limit）      | `10`                      | 单次搜索返回数量                                             |
| `请求超时`（timeout）    | `15.0`                    | API 请求超时（秒）                                         |
| `歌曲发送模式`（send_mode） | `card`                        | `card` 卡片 / `file` 文件 / `text` 文本，可用「点歌模式」命令切换 |
| `文件删除延迟`（delete_file_seconds） | `60`                | 文件模式发送后本地文件删除延迟（秒），`0` 保留 |
| `文字消息自动撤回`（retract_list_seconds） | `60` | 插件发出的**所有文字消息**（列表/歌词/提示/报错）N 秒后自动撤回，`0` 关闭 |
| `命令表情回应`（react_emoji） | `319`（比心）                    | 命令贴表情的 ID/Unicode 码点（319=比心、128077=👍、49=强），留空关闭 |
| `命令必须加 / 前缀`（require_prefix） | `false`               | 开启 = 必须 `/点歌`；关闭 = 直接发 `点歌` |

## 洛雪音源（推荐，直链的第一来源）

插件**不会**自己去连 QQ音乐 / 酷狗 / 酷我。直链的获取顺序是：

```
你的洛雪音源脚本（lx_source）  →  API 地址 音源服务（unlock_api）  →  报错
```

### 工作方式

1. 插件先按「歌名 + 歌手」在**酷我**查出这首歌的歌曲 ID（只查 ID，不取直链）；
2. 把该 ID 交给 `lx_source` 里的音源脚本（一次 `node lx_runtime.js <音源.js> <payload>`），
   由脚本用它自己的后端换成可播放直链；
3. 脚本返回的不是音频（`_is_audio` 探测）就换下一个脚本；全部失败才回落到 `API 地址`。

搜索由插件负责（洛雪音源脚本只实现 `musicUrl`/`lyric`/`pic`，不含搜索），所以整套流程
**不需要你在服务器上额外跑什么服务**，只要有 `node` 和音源 `.js` 即可。

### 怎么配

1. 服务器上装 `node`（`node -v` 能看到版本即可）；不在 PATH 的话把 `node 可执行文件` 填成绝对路径；
2. 把音源 `.js` 放到服务器任意目录，比如 `/root/音源/`；
3. 插件配置里 `洛雪音源脚本` 填该目录（`/root/音源`，自动取里面的 `.js`）或具体文件路径，
   多个用逗号分隔；想用无损就把 `音源请求音质` 改成 `flac`。

> 音质看音源支持：常见 `128k` / `320k` / `flac` / `flac24bit` / `hires`；不支持时会由脚本降级或报错。

## 部署后端服务教程

插件需要一个网易云后端，推荐 **[UnblockNeteaseMusic-utils](https://github.com/NeteaseCloudMusicApiEnhanced/UnblockNeteaseMusic-utils)**（纯解灰服务，接口极简：`/match?id=&source=`）。插件会先探它的 `/inner/modules` 认出来后端并拿到真实音源清单，再走 `/match` 取直链，写错的音源名会被自动跳过并写进日志。

### 方式一：服务器 / 本机源码运行（推荐）

和 AstrBot 同机（或任意近大陆网络的机器）运行最稳，不必暴露公网：

```bash
git clone https://github.com/NeteaseCloudMusicApiEnhanced/UnblockNeteaseMusic-utils.git
cd UnblockNeteaseMusic-utils
npm i               # 或 pnpm i
npm start           # 默认 3000 端口
npx . --port 8080   # 换端口
```

此时 `API 地址` 填 `http://127.0.0.1:3000`。

### 方式二：Vercel 部署（免费，但有坑）

1. Fork https://github.com/NeteaseCloudMusicApiEnhanced/UnblockNeteaseMusic-utils ，在 Vercel **Add New → Project** 导入，直接 Deploy
2. **务必把函数区域改成香港**：保留/加上 `"regions": ["hkg1"]`，或在 Vercel 项目 Settings → Functions → Function Region 选 Hong Kong
3. 绑定自己的域名（Cloudflare 加 CNAME 指向 Vercel）
4. 把域名填进插件配置的 `API 地址`

仓库自带的 `vercel.json` 已经写好 `"regions": ["hkg1"]`，直接导入即可。

> ⚠️ **区域必须选香港/就近节点**。解灰要出去连各家音源站，默认美国区域（`x-vercel-id: ...iad1...`）常常连不上，表现为插件报「没有可用音源或不可达」。

### 内置音源（就是 `modules/` 目录，删掉 `.js` 即下线该音源）

| 音源 | 走哪里 |
| ---- | ------ |
| `byfuns` | `api.byfuns.top`，默认请求无损（`DISABLE_FLAC=true` 时降到 `exhigh`） |
| `ddyr` | `yy.zddyr.top/lx/api`，默认请求 `hires` |
| `msls` | `api.msls1441.com` |
| `oi` | `oiapi.net` |
| `qijieya` | `api.qijieya.cn/meting` |
| `gdmusic` | `music-api.gdstudio.xyz` |
| `unm` | `@unblockneteasemusic/server`（pyncmd / bodian / qq）——**唯一去别家平台找的音源，周杰伦这类版权下架原唱只能靠它** |

### 部署后验证

```bash
curl "<你的API地址>/inner/modules"      # {"code":200,"data":{"modules":["byfuns","ddyr",...]}}
curl "<你的API地址>/inner/version"      # {"code":200,"data":{"version":"0.4.5"}}
curl "<你的API地址>/match?id=347230"    # 能返回 {"code":200,...,"data":{"url":"http..."}} 才算解灰正常
```

- 返回 `{"code":500,"message":"No available source found"}`：所有音源都没取到这首歌。先换个 ID 试试——不同音源覆盖的曲库不一样，某首歌上游确实没有是正常的；若**任意歌**都失败，多半是部署在境外连不上音源站。
- 返回 `{"code":404,"message":"Module xxx not found"}`：`source` 名字写错了。**区分大小写**，要用 `modules/` 下的文件名（全小写），例如 `byfuns` 而不是 `Byfuns`。

## 安装插件

1. 在本仓库 **Releases** 下载最新的附件（`网易云音乐点歌-flac`）；
2. AstrBot WebUI → 插件管理 → 从文件安装，选择该 zip；
3. 在插件配置里把 `洛雪音源脚本` 填成你的音源目录/文件（推荐），`API 地址` 可选填作兜底，重载插件即可使用。

## 常见问题

- **提示"获取直链失败"**：错误消息里会带上 API 地址、**该服务实际有哪些音源**与排查提示。按这个顺序查：
  1. 在**运行 AstrBot 的机器**上确认服务活着：`curl "<你的API地址>/inner/modules"`（应返回模块列表）。
  2. 直接在服务上试你要点的那首歌：`curl "<你的API地址>/match?id=<网易云歌曲ID>&source=<音源名>"`，看是不是这首歌上游本来就没有。
  3. 本机网络对该域名 TLS 握手被重置时（部分网络对非常见后缀域名有干扰），在插件配置 `代理` 里填本机代理（如 Clash 的 `http://127.0.0.1:7897`），只有音源请求走代理，搜索仍直连。
  **换后端不用改插件**：插件会探 `/inner/modules` 认出 UnblockNeteaseMusic-utils 走 `/match`，认不出就按通用后端（`/match` → `/song/url/match` → `/song/url/v1`）依次试。
- **周杰伦这类歌一直取不到直链**：**先分清是"哪台机器连不上"**。① 网易云自己就不给放这些歌（`/song/url/v1` 返回 `url: null`、`cannotListenReason: 1`），必须换别的音源；② 后端里的解灰音源若连不上别家平台（典型：Vercel 默认美国节点，`x-vercel-id: ...iad1...`），也会失败。**v2.5 起直链优先走你自己的洛雪音源脚本**，绕开后端的网络限制；配好 `洛雪音源脚本` 后周杰伦这类原唱照常能放（实测稻香/晴天/告白气球均取到可播直链）。若没配音源脚本、只靠 UMN-utils 后端，则只有 `unm` 这个音源可能覆盖到——先确认它是否可用：`curl "<你的API地址>/match?id=347230&source=unm"`。
- **点歌给了翻唱而不是原唱**：搜索接口本身就这么排（搜「稻香」前排全是深情版/女声版，周杰伦原唱不在前 10），插件 v2.1 起默认开启「优先原唱」，读 `originSongSimpleData` 把翻唱换回原唱，必要时翻页深扫；若仍不对，直接发 `直链 <原唱歌曲ID>` 精确点歌。
- **列表发完回复数字没反应**：v2.1 起 `排行/歌手/专辑/歌单/新歌` 列表都会登记序号缓存，回复数字即可点歌；序号有效期 60 秒，且只对发命令的本人有效（群聊里别人回复数字不会串台）。
- **卡片能发出来但无法播放**：多为音源直链失效。先确认音源脚本本身能出这首歌的直链（`node lx_runtime.js <音源.js> '<payload>'`）；洛雪音源都失败时会自动回落 `API 地址` 音源服务，可把 `音源优先级` 固定为其他音源试试。
- **取不到原唱时**：v2.5 起**不再退而求其次发网易云的翻唱版**——洛雪音源 → `API 地址` 音源服务 → 都不行就报错（错误里带 API 地址与排查提示），不会让你误以为拿到的是原唱。
- **音乐卡片 / 表情回应仅 aiocqhttp 平台支持**（NapCat / Lagrange / go-cqhttp），卡片失败会自动回退；私聊贴表情在部分协议端不生效。

## 作者

1. 流水
2. 听雨的蛙
3. 落雪
4. GLM-5.3-Flash

## 致谢

- [LX Music（洛雪音乐助手）](https://github.com/lyswhut/lx-music-desktop) — 自定义音源脚本规范与运行时
- [UnblockNeteaseMusic-utils](https://github.com/NeteaseCloudMusicApiEnhanced/UnblockNeteaseMusic-utils) — 音源匹配服务
- [astrbot\_plugin\_ncm\_directlink](https://github.com/monbed/astrbot_plugin_ncm_directlink) — 交互流程参考
- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
