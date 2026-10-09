# 网易云音乐点歌-flac

AstrBot 点歌插件：搜网易云音乐，直链由你自己部署的 [UnblockNeteaseMusic-utils](https://github.com/NeteaseCloudMusicApiEnhanced/UnblockNeteaseMusic-utils) 音源服务提供，支持卡片 / 文件 / 语音 / 文本四种发送方式。

> 插件不含任何音源地址，`API 地址` 由使用者自行部署、自行填写（见下文教程）。
> ⚠️ 仅供学习交流，请在体验后支持正版音乐。

## 特点

- **搜歌 + 回序号点歌**：`点歌 歌名` 出列表，回 `3` 发第 3 首；列表 **60 秒内可反复回**，还能连号 `1~3`、多选 `1-2-4-9-10`
- **优先原唱**：搜索前排常被翻唱占据，插件按原唱标注/发行时间把原唱换回来；想点翻唱就在关键词里写明（如 `点歌 稻香 深情版`）
- **直链只走音源服务**：插件**不会**去连 QQ音乐/酷狗/酷我，只问 `API 地址` 那个服务要直链；**所有音源都取不到就直接报错**，不拿翻唱版充数，换源静默无提示
- **音质由音源顺序决定**：按 `音源优先级` 取**第一个可用直链**就算数，音质看排前面的音源上游给什么（`byfuns`=无损、`ddyr`=Hi-Res，默认即无损）
- **四种发送方式**：卡片 / 文件（自动内嵌歌词与封面）/ 语音 / 文本，可用 `点歌模式` 随时切换
- 所有命令触发时贴表情回执；插件发出的文字消息默认 60 秒后自动撤回（仅 aiocqhttp）

## 指令

| 指令 | 说明 |
| --- | --- |
| `点歌 <歌名>` | 搜索并列结果，回序号选歌，回 `0` 取消 |
| `点歌 <序号>` | 选上一次列表的第 N 首，支持 `点歌 1~3`、`点歌 1-2-4-9-10` |
| `点歌卡片/文件/语音/消息 <歌名>` | 本次按指定方式发送 |
| `直链 <ID/链接>` | 按网易云 ID 或分享链接取直链 |
| `歌词 <歌名\|ID>` | 歌词 |
| `排行 [榜单名]` | 官方排行榜，回序号看曲目 |
| `歌手 <名字>` / `专辑 <名字>` / `歌单 <关键词>` | 列表，回序号点歌 |
| `评论 <歌名\|ID>` | 热评 |
| `新歌 [地区]` | 新歌速递：华语/欧美/日本/韩国/全部 |
| `连播 [首数\|序号]` | 语音连播当前列表（最多 10 首） |
| `来首歌` | 随机一首 |
| `点歌模式 [卡片\|文件\|语音\|文本]` | 查看/切换默认发送方式 |
| `帮助` | 使用帮助 |

以上命令**直接发即可，无需 `/` 前缀**（带 `/` 也行）。

## 序号怎么回

`点歌` / `排行` / `歌手` / `专辑` / `歌单` / `新歌` 出列表后直接回序号即可：

| 回复 | 实际发送 |
| --- | --- |
| `3` | 第 3 首 |
| `1~3` | 第 1、2、3 首（`~` `～` `到` 都行） |
| `1-2-4-9-10` | 第 1、2、4、9、10 首 |
| `1,3,5` | 第 1、3、5 首（逗号、顿号、空格、斜杠都行） |
| `0` | 取消 |

- 列表 **60 秒内有效**，可连着回多次，每次回复重新计时；发 `0` 或超时才失效。
- 单条最多点 **10 首**；序号超范围会明确提示，其余照发。
- 序号只对**发命令的本人**生效，群聊不串台。

## 配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `API 地址`（unlock_api） | 空 | **必填**。填你部署的 UnblockNeteaseMusic-utils 地址（末尾不带 `/`）。插件先探 `/inner/modules` 认后端并拿音源清单，再走 `/match?id=&source=` 取直链；旧 api-enhanced 也兼容 |
| `音源请求代理`（proxy） | 空 | 访问音源服务走的代理，如 `http://127.0.0.1:7897`。仅音源请求走代理，搜索仍直连 |
| `音源优先级`（source） | `byfuns,ddyr,auto` | 逗号分隔，按顺序用**音源服务上的哪个音源**，**顺序同时决定音质**（取第一个可用就发，把请求无损的音源排前面）。可选 `byfuns` `ddyr` `msls` `oi` `qijieya` `gdmusic` `unm`；`auto` = 服务端遍历全部。写错的名字自动跳过 |
| `校验音频直链`（verify_audio） | `true` | HEAD 探测直链，过滤 VIP 占位 HTML 假链接 |
| `自动点第一首`（auto_pick） | `false` | 开启后点歌直接发第一首，不再列序号 |
| `优先原唱`（prefer_original） | `true` | 把搜索前排的翻唱换成网易云标注的原唱 |
| `搜索结果数量`（limit） | `10` | 单次搜索返回数量 |
| `请求超时`（timeout） | `15.0` | API 请求超时（秒） |
| `歌曲发送模式`（send_mode） | `card` | `card`/`file`/`voice`/`text`，可用 `点歌模式` 命令切换 |
| `文件模式本地文件删除延迟`（delete_file_seconds） | `60` | 文件发送后 N 秒删本地文件，`0` 保留 |
| `文件内嵌歌词与封面`（embed_lyrics） | `true` | 文件模式写入歌词、封面与标签 |
| `文字消息自动撤回`（retract_list_seconds） | `60` | 插件发出的所有文字消息 N 秒后撤回，`0` 关闭 |
| `命令表情回应`（react_emoji） | `319` | 表情 ID/Unicode（319=比心、128077=👍、49=强），留空关闭 |
| `命令必须加 / 前缀`（require_prefix） | `false` | 开启 = 必须 `/点歌`；关闭 = 直接发 `点歌` |

## 直链是怎么来的

插件只做两件事：**① 找歌**（向网易云公开接口搜索、按原唱标注重排）；**② 要直链**（向 `API 地址` 发一次 `/match?id=<歌曲ID>&source=<音源名>`）。

```
API 地址 音源服务（unlock_api） → 取不到就报错
```

直链来源**只有这一条**。所以能覆盖哪些平台、音质高低，取决于你部署的服务里装了哪些音源、上游是否可用——想换更强的音源，改服务端 `modules/` 即可，插件侧无需改动。

## 部署后端服务

推荐 [UnblockNeteaseMusic-utils](https://github.com/NeteaseCloudMusicApiEnhanced/UnblockNeteaseMusic-utils)（纯解灰服务，接口极简：`/match?id=&source=`）。

**方式一：本机 / 服务器源码运行（推荐，最稳）**

```bash
git clone https://github.com/NeteaseCloudMusicApiEnhanced/UnblockNeteaseMusic-utils.git
cd UnblockNeteaseMusic-utils
npm i        # 或 pnpm i
npm start    # 默认 3000 端口
```

`API 地址` 填 `http://127.0.0.1:3000`（与 AstrBot 同机最省事）。

**方式二：Vercel 部署（免费，注意选香港节点）**

1. Fork 上面仓库，Vercel **Add New → Project** 导入，直接 Deploy
2. **函数区域务必设为香港**：保留 `"regions": ["hkg1"]`，或在 Settings → Functions 选 Hong Kong
3. 绑定域名，填进插件 `API 地址`

> ⚠️ 区域必须选香港/就近节点。解灰要出去连各家音源站，默认美国节点常连不上，表现为「没有可用音源或不可达」。

**内置音源**（`modules/` 目录，删掉 `.js` 即下线）：

| 音源 | 走哪里 |
| --- | --- |
| `byfuns` | `api.byfuns.top`，默认请求无损 |
| `ddyr` | `yy.zddyr.top/lx/api`，默认请求 Hi-Res |
| `msls` / `oi` / `qijieya` | `api.msls1441.com` / `oiapi.net` / `api.qijieya.cn` |
| `gdmusic` | `music-api.gdstudio.xyz` |
| `unm` | `@unblockneteasemusic/server`（pyncmd/bodian/qq）——**唯一跨平台找别家的，周杰伦这类下架原唱靠它** |

**部署后验证**：

```bash
curl "<你的API地址>/inner/modules"    # 返回模块列表
curl "<你的API地址>/match?id=347230"  # 能返回 data.url 才算正常
```

## 安装插件

1. 在 Releases 下载最新 zip；
2. AstrBot WebUI → 插件管理 → 从文件安装；
3. 填好 `API 地址`，重载插件即可。

## 常见问题

- **提示「获取直链失败」**：错误里会带上 API 地址、该服务实际音源与排查提示。依次查：① `curl "<API>/inner/modules"` 看服务活没活；② `curl "<API>/match?id=<歌曲ID>&source=<音源名>"` 看是不是这首歌上游本来就没有（换个 ID 对比）；③ 本机对该域名 TLS 被重置时，在 `音源请求代理` 填本机代理。
- **周杰伦这类歌取不到**：网易云自己就不给放，只能靠服务里能跨平台找的 `unm`；它连不上别家（Vercel 美国节点常见）就失败。逐个音源实测后决定 `音源优先级` 怎么填。
- **点歌给了翻唱**：搜索接口本身就这么排，插件默认开启「优先原唱」会换回原唱；仍不对就 `直链 <原唱歌曲ID>` 精确点歌。
- **卡片发出来不能播**：多为直链失效或上游限流，换 `音源优先级` 里的音源再试。
- **音乐卡片 / 表情回应仅 aiocqhttp 平台支持**（NapCat / Lagrange / go-cqhttp），卡片失败自动回退文本。

## 作者

流水 · 听雨的蛙 · 落雪 · GLM-5.3-Flash

## 致谢

- [UnblockNeteaseMusic-utils](https://github.com/NeteaseCloudMusicApiEnhanced/UnblockNeteaseMusic-utils)
- [astrbot_plugin_ncm_directlink](https://github.com/monbed/astrbot_plugin_ncm_directlink)
- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
