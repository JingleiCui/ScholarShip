# README 演示素材

- `workspace-2026-11-18.jpg`：已接入真实前端的当前首页实渲染截图。使用用户确认的“书页之帆”Logo；首页纸面示意不代表科研结果。未展示凭据、原始 PDF 或私人正文。
- `scholarship-mark.svg`：与当前应用 `frontend/public/brand/scholarship-mark.svg` 图形完全一致，路径原样来自用户确认的品牌横幅。

- `demo.mp4`：2026-11-18 真实操作录制的 90 秒功能 Demo，1920×1080 / 25 fps / H.264，含中文步骤字幕。按新建空间、PDF 导入、本地解析、索引与问答、引用核对、双语阅读、文献发现与图谱、写作、CSV 绘图、下载导出和成果回看排列。所有等待、调试及重复回看已剪除。新任务在独立空间执行，模拟结果保留标记；真实问答和译文为历史只读回看。无新增云端推理、OCR 或全文上传；实际进行了 arXiv/Crossref 公开元数据检索，arXiv 网络失败、Crossref 成功。入口位于中英文 README。
- `demo-poster.jpg` / `demo.srt` / `demo-chapters.json`：该视频实际帧、逐段字幕与章节时间；`demo/page-text-counts.csv` 是演示中使用的 PDF 页级文本字符数，不是模型性能指标。导出图表已在同一环境运行 `plot.py` 复现，图片哈希一致。
- `demo.gif`：由上述真实操作视频以 2.5 倍速转换的 36 秒、1000 像素宽、6 fps 循环动图，供中英文 README 直接播放；内容与高清视频顺序一致，已剪除全部等待。点击动图打开同长度的 1080p `demo-preview.mp4`；90 秒版作为详细操作入口保留。
- `evidence.jpg`：历史问答验收中的原文引用定位截图，展示 Transformer 论文第 7 页。保留当时界面，不将旧界面截图标为当前 UI。
- `scholarship.svg`：项目品牌横幅，2026-11-19 重做为 1600×450 宽幅版（SVG 画布 1672×538 含投影留白，viewBox 完整）：左侧 ScholarShip/学者之舟/品牌语分层，右侧书页之帆插画与证据锚点。文字已转曲，无需外部字体；由 Ardot 设计稿导出，中英文 README 首行引用。
- `workspace.jpg`：先前的实际工作台截图，保留供文档引用。
- `architecture/`：2026-11-18 五类独立技术图，含固定版式 SVG、2,400 像素宽 PNG 和 Mermaid 简化结构源码；详见[架构图集](../engineering/architecture-gallery.md)。仅保留模块、分区与连接关系，没有页眉、页脚或合成总览。由本地脚本绘制，未上传参考 PDF 或调用模型。
- `diagrams/`：保留的早期工程架构、业务流程与证据版本图稿。

GIF 只证明页面可浏览；科研结果的验证范围以正文与验收记录为准。仓库不分发原始论文和用户数据库。
