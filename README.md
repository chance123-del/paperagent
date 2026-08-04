# PaperFormat Agent

将已经写好的论文转换为符合目标规范的 LaTeX 项目，并交付编译 PDF、源码包和格式审计报告。

## 支持的源稿

- Word：`.docx`，提取标题、标题层级、段落、表格和嵌入图片。
- PDF：`.pdf`，提取可选择的文字和可读取的图片；扫描型 PDF 需先 OCR。
- Markdown：`.md` / `.markdown`，提取标题、段落、简单表格和本地图片。
- 已有 LaTeX：`.tex` 或 `.zip` 工程，直接执行格式检查和修复。

## 交付内容

- `source.tex`：修复后的主 LaTeX 文件。
- `latex_source.zip`：可继续编辑、包含图片资源的 LaTeX 工程。
- `format_report.md`：发现的问题、自动修复记录和格式评分。
- `main.pdf`：选择编译且 LaTeX 环境资源完整时生成。
- `compile.log`：始终保留编译失败的具体原因。

## 启动界面

```powershell
python -m pip install -r requirements.txt
python app.py
```

界面可以上传论文，也可以填写本地论文路径。`输出目录`是可编辑的，支持改为任意本地文件夹。目标格式区还可上传期刊或学校提供的 PDF / Word 指南；系统会复制原件并仅应用其中能明确识别的页边距、行距与参考文献样式。系统不会补写、润色或改写作者提供的正文、数据和结论；缺失的摘要、关键词等内容会记录在格式报告中。选择已有规则档，填写目标期刊或学校名称，然后点击“转换并排版”。

## 期刊格式

`rules/` 中的 JSON 是格式规范的确定性来源。当前提供基础中英文论文规则。要支持某一具体期刊，应将该期刊 Author Guidelines 或官方 LaTeX 模板整理成规则档并放入 `rules/`，再从界面选择它。这样生成的格式修改可复核，而不是依赖不稳定的网页猜测。

界面中的 `Match journal` 会优先使用 Crossref 公开元数据识别期刊与出版社，并匹配 IEEE、ACM、Elsevier、Springer Nature、Nature Portfolio 和 APA 的内置规则包。未收录期刊可上传官方指南，或上传一篇公开的 PDF/Word 参考论文；系统会从中提取可观察到的页面、图表标注和引文线索，但不会将不确定的细节伪装成官方规则。

若希望一键匹配参考文献，请在界面上传 `.bib` 文件。系统会按选中的期刊规则写入 `bibliographystyle`、引用宏包和 `references.bib` 链接。

## 公式合集与交付审计

在 DOCX 或 Markdown 正文中写入 `[Eq1]`、`[Equation1]` 或 `[公式1]` 作为公式位置标记，再上传 JSON 或包含 `formulas.json` 的 ZIP。每个条目提供经过作者确认的 `latex`，可选 `tag` 会生成右侧公式编号。系统拒绝文件读写、宏定义等危险 LaTeX 命令；缺失、重复或未经确认的公式会阻止正式交付，不会根据图片猜写内容。模板见 `templates/formulas.template.json`，示例见 `samples/formulas.example.json`。

每次处理会生成 `agent_trace.json`，记录稿件解析、规则决策、修复、素材匹配、预览验证和交付闸门的状态与证据。该轨迹同时写入 LaTeX 源码包，正式导出后会更新为最终交付状态。

对于包含公式区域或无法可靠恢复表格行列结构的 PDF，系统保留原始 PDF 作为保真预览并阻止错误重排；继续可编辑交付时应提供 DOCX、LaTeX 或表格源文件。

## 命令行

```powershell
python -m paperformat_agent.cli repair `
  --input .\my-paper.docx `
  --rules .\rules\thesis_basic.json `
  --output .\outputs\my-paper.tex `
  --project-zip .\outputs\my-paper.zip `
  --report .\outputs\my-paper-report.md `
  --compile
```

首次使用 Tectonic 编译时，缺失的宏包可能需要联网下载；网络不可用时，源代码和报告仍会正常生成，失败原因写入编译日志。
