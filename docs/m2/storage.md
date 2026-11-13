# 本机与容器文件一致性方案

当前权威业务库为同一项目 PostgreSQL；文件统一使用宿主项目 `var/files`。Compose API/worker/Beat 共同绑定宿主 `var` 到 `/app/var`。入口 `deploy.py` 从 `storage_preflight.py` 顶部 `SHARED_DATA_DIR` 读取绝对路径，并向 Compose 注入 `SCHOLAR_SHARED_DATA_DIR`；不要直接绕过部署入口。

现有本机默认目录为 `/Users/cuijinglei/PycharmProjects/ScholarShip/var`。如修改目录，需同时更新本机 `SCHOLAR_DATA_DIR`，保持本机与部署配置一致；不能只改容器的路径。

构建/启动前执行只读迁移预检：读取 PG 所有 ready file_objects，在目标 files 目录核对路径边界、存在性、字节大小与 SHA-256。失败退出，不删除、修补或重写文件。该显式维护脚本使用管理员连接获取跨账号文件清单，运行进程不继承给应用容器，控制台只输出统计，不输出密钥或文件内容。

历史 `scholarship_file_data` 卷保留，不运行 down -v。若预检发现文件只存在旧卷，先停止本项目应用，备份 PG、现有目录和旧卷，再将缺失文件复制到目标目录并逐个复核哈希；有冲突不覆盖。未全部通过不可启动新挂载。当前本机 9 个 ready 文件全部通过，未发现需从旧卷迁移的 PG 文件；这不证明旧卷没有其他非业务文件。

本批只验证主机文件、拒绝分支和 Compose 配置，未重建或启动新应用容器；容器 UID 10001 的读写权限、Linux 宿主目录权限以及端到端挂载检查须在部署阶段实际验证。备份应同时包含 PostgreSQL、files、Qdrant profile/世代与快照，不能将更换挂载当作备份完成。
