# 部署你自己的岗位看板（Fork 指南）

这个项目把"找什么岗位"做成了**一份配置文件**（`config/profile.json`）。你不用改代码：选一个预设、改几个关键词，
GitHub Actions 会自动抓取、筛选、生成一个公开的静态网站（GitHub Pages）。

> **先说清楚更新频率：自动更新，但 GitHub 不保证准点。**
> 工作流里的定时写的是"每小时"，但 GitHub 的定时触发是尽力而为：什么时候真正启动由 GitHub 决定。
> 我们在一个真实仓库上量过，相邻两次定时运行近期平均隔 **约 6 小时**（最短 2.4、最长 8 小时）。
> 所以不要把它当"每小时"的承诺。想立刻刷新：**Actions → update → Run workflow**。页面右上角会显示"X 小时前更新"。
> 另外，GitHub 会在仓库 **60 天没有任何活动**后暂停定时任务（本项目有每月一次的保活工作流，但仍请偶尔看一眼 Actions 页面）。
> 想要真正的每小时：可以用外部定时器（如 cron-job.org、Cloudflare Workers 定时）调用 GitHub 的 `workflow_dispatch` 接口，
> 这需要一个只有 Actions 写权限的令牌，属于可选项，默认不开。

## 先选一条路

| | 做法 | 适合 | 代价 |
|---|---|---|---|
| **A. 专属站，自己抓取** | Fork 本仓库，改 `config/profile.json` | 想要最灵活（连来源、Telegram 频道、GitHub 仓库清单都能改） | 每个站都去抓一遍源站，运行约 8–15 分钟 |
| **B. 专属站，读聚合站的数据** | 用 `templates/profile-repo` 建一个只有配置和工作流的小仓库 | 想要最省事；岗位在聚合站覆盖的范围内 | 只能在聚合站已收集的岗位里筛选 |
| **C. 你自己的聚合站 + 一组方向站** | `scripts/deploy_org.sh <组织名>` | 想做一个像"前端 / 后端 / 移动端……"这样的站群 | 要先在 GitHub 网页上建一个组织 |

## A. Fork 后自己抓取

1. 点右上角 **Fork**（仓库需要公开，GitHub Pages 免费版只支持公开仓库）。
2. 克隆到本地，选一个预设并生成配置：
   ```bash
   python3 -m jobboard.cli presets                           # 看有哪些预设
   python3 -m jobboard.cli init --preset backend --force     # 写入 config/profile.json
   ```
3. 编辑 `config/profile.json` 的关键词（见下面"关键词怎么写"），然后**先在本地试**：
   ```bash
   python3 -m jobboard.cli profile --try "Senior Go Engineer" "Staff Frontend Engineer" "Sales Manager"
   ```
   输出里 `KEEP` 表示会进入"推荐"，`skip` 表示不会。
4. （可选）改 `config/telegram_channels.txt`（公开频道）和 `config/github_job_repos.txt`（GitHub 招聘仓库），换成你领域的。
5. 提交并推送。到仓库的 **Settings → Pages → Build and deployment → Source** 选 **GitHub Actions**。
6. 到 **Actions** 页面：fork 出来的仓库**默认不运行定时任务**，需要点一下 "I understand my workflows, go ahead and enable them"，
   再进入 **update** → **Run workflow** 手动跑第一次。首次运行约 8–15 分钟（要读取全部公开频道、很多公司的招聘页）。
7. 跑完后，站点在 `https://<你的用户名>.github.io/<仓库名>/`。之后 GitHub 会自己按它的节奏定时更新。

项目只用 Python 标准库，工作流里**没有任何依赖需要安装**。

## B. 小仓库读聚合站数据

```bash
python3 -m jobboard.cli org-render --org <你的账号或组织> --out /tmp/out --presets backend   # 渲染出 /tmp/out/backend
```
把 `/tmp/out/backend` 推送成一个新仓库、Pages 选 GitHub Actions、手动跑一次 `update` 即可。
它不抓取任何源站，只读聚合站（默认 `https://<组织>.github.io/jobboard/data`）并用自己的 `profile.json` 重新筛选打分，几十秒就跑完。

## C. 你自己的聚合站 + 站群

1. 在 github.com 创建一个组织（**GitHub 不允许用命令行或 API 创建组织**，这一步只能手动），免费版即可。
2. `gh auth login`（需要 `repo`、`workflow` 权限，且你是该组织的 owner）。
3. 预演，然后正式部署：
   ```bash
   scripts/deploy_org.sh <组织名> --dry-run
   scripts/deploy_org.sh <组织名>                           # 引擎仓库默认叫 jobboard
   scripts/deploy_org.sh <组织名> --engine-repo <仓库名>   # 或者用组织里已有的仓库（比如和组织同名的那个）
   ```
   会创建：`<组织>/jobboard`（聚合站 + 数据源）、每个方向一个仓库（frontend、backend、mobile、fullstack、data-ai、devops、qa、security、web3、game、product、design、management）、
   以及 `<组织>.github.io`（首页，链到所有站点）。可以重复运行，已存在的仓库会被更新。

## 关键词怎么写

`config/profile.json`：

```json
{
  "site": {"title": "后端岗位看板", "description": "……"},
  "roles": [
    {"key": "go", "label": "Go", "keywords": ["golang", "go developer", "(go)", "go 开发"], "weight": 8}
  ],
  "specific_roles": ["go"],
  "exclude_title": ["front-?end", "sales", "销售"],
  "search_terms": 6
}
```

- **keywords**：英文按**整词**匹配（`java` 不会匹配 `javascript`，`ios` 不会匹配 `bios`），中文按子串匹配；不区分大小写。
  每个关键词至少 2 个字符（单个字母会匹配太多）。短词有歧义时用上下文写法，比如 Go 语言用 `golang`、`go developer`、`(go)`，而不是单独的 `go`。
- **weight**（1–10）：这个方向的重要度。分数 ≥ 5 才算"推荐"；同时命中多个方向、标题里带"远程"会再加几分。
- **exclude_title**：正则，标题里出现这些词的岗位不会被"泛称标题"（如 Software Engineer）带进来。
- **specific_roles**：来源自带的标签（例如 Himalayas 的 `flutter`）命中这些方向时，可以把泛称标题救回来。
- **levels**：页面上"推荐 / 严格"两个按钮的分数线，默认 `{"recommended": 5, "strict": 10}`。方向很多的站点（比如聚合站）分数普遍偏高，要调高，聚合预设用的是 12 / 14。
- **search_terms**：带搜索框的来源（欧盟 EURES、瑞典等）一次最多查多少个英文关键词；方向多的站点可以调大（最大 40）。
- 配置写错时，`profile` / 抓取命令会用一句话告诉你哪里错了（比如关键词太短、权重超范围、正则无效）。

## 概览页和订阅

每个公开站的首屏是**概览**：在招岗位数、24 小时新增、远程占比、今日新岗位精选、方向分布、近 14 天新增走势、来源构成，以及**愿意担保签证的雇主**。
右上的"岗位列表"标签才是完整的筛选列表。统计在构建时算好存成 `data/stats.json`，页面只负责画，不依赖任何图表库。
统计只反映本站收集到的来源，页面上也这样写明了（热门公司之类会受"我们扫描了哪些公司"影响，所以没有做公司排行）。

每个站点还有 **RSS（`feed.xml`）和 JSON Feed（`feed.json`）**，内容是最新的 100 个"推荐"岗位。要让订阅里的链接是绝对地址，
需要告诉站点它自己的网址：写在 `config/site.json`（`{"site_url": "https://你的域名/路径/", "sites_base": "https://组织名.github.io"}`），
或者用环境变量 `JOBBOARD_SITE_URL`。用 `deploy_org.sh` 部署的站点会自动配好。

## 公开站的隐私规则

公开站**只显示摘要（≤600 字）和原帖链接**，不显示联系方式（邮箱、`@用户名`、`t.me` 链接），导出时有自检，发现就会让构建失败。
只有 `jobboard/mode.py` 里的白名单来源会进入公开站；LinkedIn、需要个人密钥的接口、你自己登录 Telegram 读到的私有群，永远不会被发布。
Telegram 只能读到**开了网页预览**的公开频道（`https://t.me/s/<名称>` 能打开的）。

## 常见问题

- **站点打开是空的 / 很旧**：先看页面右上角的更新时间；再看 Actions 里最近一次 `update` 是否成功（失败会有邮件）。定时没触发就手动 Run workflow。
- **岗位太少 / 太杂**：用 `profile --try` 拿几个真实标题试；太少就补关键词，太杂就提高 `weight` 门槛或补 `exclude_title`。
- **想加别的来源**：在 `jobboard/sources/` 里加一个函数并登记到 `SOURCES`，见 `adapters.py` 顶部的说明。
