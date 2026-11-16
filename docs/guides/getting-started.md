# 安装与首次运行

## 环境

本地工具链已验证 macOS Apple Silicon。准备 Docker Desktop 和 Python 3.12；`bootstrap.py` 目前下载 macOS arm64 的 Node，Windows/Linux 自动初始化尚未验证。

检查脚本顶部 `BOOTSTRAP_PYTHON`，默认 `/opt/homebrew/bin/python3.12`。项目使用自己的 `.tools`、`.venv` 和锁文件，不需要全局升级 Node/pnpm。

## 初始化

```bash
git clone https://github.com/JingleiCui/ScholarShip.git
cd ScholarShip
python3.12 bootstrap.py
.venv/bin/python dev.py
```

初始化创建 PostgreSQL、Qdrant、Redis，应用迁移并生成初始账号。已有配置和账号不会被重置；失败后保留日志再排查，不删除数据库强行重建。

- Web：http://127.0.0.1:15173
- API 文档：http://127.0.0.1:18080/docs
- 默认基础端口：PostgreSQL 15432、Qdrant 16333、Redis 16379。

## 登录与导入

首次运行由脚本自动生成 `.env`，**不要预先复制空的 `.env.example`**；初始化遇到已有 `.env` 会保留它。

账号 `researcher` 的密码在 `INITIAL_USER_PASSWORD`，隔离的只读评审角色账号 `reviewer` 的密码在 `INITIAL_REVIEWER_PASSWORD`。请在本地编辑器查看，不要把整份配置贴到 issue。首次克隆不附带开发者的文献、索引或模型配额。

进入“文献库”，导入有权处理的 PDF。文本型 PDF 可先本地预览，再解析文本结构；扫描件需要 OCR，不会因导入成功就自动拥有可用文本。查看原文或进行受控图表生成不需要云端模型 Key。

## 后续配置

- [模型与服务](models.md)：模型选择、Key 和执行准入。
- [容器运行](deployment.md)：本机与容器模式切换。
- [能力边界](capabilities.md)：如何理解结果和测试范围。
- [运行手册](../runbook.md)：恢复与历史故障处理。

停止本机应用运行 `.venv/bin/python stop.py`。它仅停止本项目已核验的本地启动器，保留基础容器和数据。
