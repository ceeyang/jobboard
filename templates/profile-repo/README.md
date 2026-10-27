# __TITLE__

__DESCRIPTION__

这个站点是**自动生成**的：它不抓取任何招聘网站，只读取聚合站（__AGGREGATE_URL__）每次更新后公开的数据，
再用本仓库的 [`config/profile.json`](config/profile.json)（预设 `__PRESET__`）重新筛选和打分。

## 它多久更新一次？

**自动更新，但不准点。** 工作流里写的是"每小时"，但 GitHub 的定时触发是尽力而为：它什么时候真正启动由 GitHub 决定，
在不常有提交的仓库上，近期通常每 4–8 小时才跑一次，有时更久。想立刻刷新：打开仓库的 **Actions → update → Run workflow**。
数据本身的新旧还取决于聚合站，页面右上角显示的是上次更新时间。

## 想改成自己的关键词？

1. 编辑 [`config/profile.json`](config/profile.json)：`roles` 里每个岗位方向有 `keywords`（关键词，中英文都可以）和 `weight`（重要度 1–10）。
   `exclude_title` 里的词出现在标题里，这个岗位就不会被"泛称标题"（如 Software Engineer）捞回来。
2. 提交后到 **Actions → update → Run workflow**，一两分钟后站点就更新了。
3. 想先在本地试关键词：`python -m jobboard.cli profile --path config/profile.json --try "Senior Go Engineer" "Sales Manager"`
   （需要先 clone 引擎仓库 `__ENGINE_REPO__`）。

可用的预设：`frontend-mobile`、`frontend`、`mobile`、`fullstack`、`backend`、`data-ai`、`devops`、`qa`、`security`、`web3`、`game`、
`product`、`design`、`management`、`all-tech`。换预设：`python -m jobboard.cli init --preset backend --force --out config/profile.json`。

> 注意：这个站点只能在聚合站已收集的岗位里筛选。关键词在聚合站完全没收集到的领域，需要用引擎仓库自己抓取（见 `__ENGINE_REPO__` 的 README）。

## 隐私

公开站只显示摘要和原帖链接，不显示联系方式（邮箱、`@用户名`），导出时有自检，发现就会让构建失败。
