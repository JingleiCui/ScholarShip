# 首次上传到 GitHub

初检时分支为 codex/bootstrap、没有 remote；整理期间已变为 main 并新增 origin（与目标地址一致）。本地已存在 Git 历史，不需要 git init，不删除 .git，不压成一条虚假的初始历史。当前整理后尚未推送，以下命令由维护者执行。

先在 GitHub 账号 JingleiCui 下创建名为 ScholarShip 的**空仓库**，不要让网站自动添加 README、.gitignore 或 LICENSE；本地已提供前两者，整体许可证尚待作者选择。使用本机已配置的 GitHub 登录、凭据管理器或有仓库写权限的 Token 完成 HTTPS 鉴权。不要把 Token 写进命令、仓库 URL 或聊天。

```bash
cd /Users/cuijinglei/PycharmProjects/ScholarShip
git status
git add -A
git diff --cached --stat
git commit -m "Prepare ScholarShip competition repository"
git push -u origin main
```

提交信息也可用 `init ScholarShip`，它只是本次提交的描述，不会清除已有历史。每行单独运行；前一步报错时先处理，不要继续盲目粘贴。

- `nothing to commit`：没有新修改，可继续检查分支和 remote。
- `remote origin already exists`：先 `git remote -v`，确认该地址后决定是否需要 `git remote set-url origin ...`，不要重复添加。
- repository not found / 403：检查仓库创建、账号和写权限；不是多次 commit 就能解决。
- non-fast-forward：远程已有提交，先 fetch/查看差异再合并；不要直接 force push 覆盖远程。
- 推送后 `git status -sb` 应显示本地 main 跟踪 origin/main；在 GitHub 页面确认 README、源码、锁文件和文档存在，.env/var/原论文不存在。

依据：[GitHub 官方上传说明](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github?platform=mac)。推送只是源码托管，不会自动部署 Web 服务，也不会替你向比赛平台提交。
