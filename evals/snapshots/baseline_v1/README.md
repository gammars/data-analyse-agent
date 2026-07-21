# baseline-v1 工作区快照

- 基础提交：`0e28896664da4847d98262175bd32545a85f0345`
- 分支：`main`
- 冻结时工作区：dirty
- 冻结时 Git 状态项：18
- 快照文件：`worktree_changes.zip`
- 文件大小：`2302329` bytes
- SHA-256：`0a0aeb7c25b3e1e91f69cd58ee8a4409780cc4652a431ad6769e4a238be2c22c`

ZIP 保存了创建实验协议之前，所有 Git 已修改和未跟踪状态项的完整文件内容及相对目录。
它不包含 `.git`、被 Git 忽略的 `.env`、API Key、缓存或运行期存储目录。

恢复方法：

1. 检出上述基础提交到新的工作目录。
2. 将 `worktree_changes.zip` 解压到仓库根目录并允许覆盖。
3. 使用本目录记录的 SHA-256 验证 ZIP 未发生变化。

实验协议、基线配置和本 README 是快照创建后新增的冻结元数据，因此不包含在 ZIP 内。
