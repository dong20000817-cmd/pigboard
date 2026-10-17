# 让 iPhone 彻底不依赖 Mac：云端托管方案

## 先说结论

| 方案 | 手机能看 | 数据新鲜度 | 前提 |
|---|---|---|---|
| 现状（Mac 作服务器） | Mac 开机时 | 每天 07:45 | Mac 必须开机 |
| **+ 离线缓存** | **Mac 关机也能看** | 最后一次同步时的数据 | 开启 Tailscale HTTPS（1 次点击） |
| **+ 云端托管（本文）** | 随时 | **每天自动更新，不需要 Mac** | 一个 GitHub 账号 |

> 只要数据源还是从某台机器去抓，就一定需要一台「开着的机器」。
> 云端托管 = 把「开着的机器」换成 GitHub 的服务器，于是电脑关机也不影响。

## 部署步骤（约 10 分钟，一次性）

### 1. 建仓库并推送

在 <https://github.com/new> 新建一个仓库（建议 **Private**），然后在本机执行：

```bash
cd ~/PigBoard
git init
git add -A
git commit -m "init: 生猪市场看板"
git branch -M main
git remote add origin git@github.com:<你的用户名>/<仓库名>.git
git push -u origin main
```

> 若从未配置过 SSH key：`ssh-keygen -t ed25519 -C "你的邮箱"`，
> 再把 `~/.ssh/id_ed25519.pub` 的内容贴到 GitHub → Settings → SSH and GPG keys。

### 2. 开启 Pages

仓库 → **Settings → Pages → Build and deployment → Source** 选择 **GitHub Actions**。

### 3. 触发一次

仓库 → **Actions → 每日更新看板 → Run workflow**。

成功后地址形如：

```
https://<用户名>.github.io/<仓库名>/
```

### 4. iPhone 打开并添加到主屏幕

该地址是 **HTTPS**，因此：

- 可以直接「添加到主屏幕」当 App 用；
- **Service Worker 会生效** → 即使断网、飞行模式，也能打开最后一次同步的看板。

## 之后每天怎么跑

GitHub Actions 会按 `cron: 45 23 * * *`（北京时间 07:45）自动：

1. 运行 `pipeline.py` 采集五个模块；
2. 只有在数据/官方发布变化时才改写 `web/index.html`（无变化则提交为空，不会产生噪音 commit）；
3. 把 `web/` 发布到 Pages。

你的 Mac 可以完全关机。

## 关于「私有」

- GitHub Pages 对**公开仓库**免费；**私有仓库**的 Pages 需要 GitHub Pro（付费）。
- 看板内全部是公开市场数据，无个人信息。
- 若必须私有访问，推荐 **Cloudflare Pages + Cloudflare Access**（免费额度内，支持邮箱/Google 登录白名单），
  构建产物同样是 `web/` 目录，`pipeline.py` 不变。

## 注意事项

- 云端运行时，`data/heartbeat.json` 只在仓库里、不在站点根目录，因此页面右上角的
  「最后巡检」会显示为离线态；数据本身（`index.html` 内嵌快照）完全正常。
- 冻品库容率、淘汰母猪比价来自 `data/registry/*.json`（人工核验录入），
  这类更新仍需你本地改完并 `git push`，云端会随下一次运行一起发布。
