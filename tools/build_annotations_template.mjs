import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(__dirname, "..");
const outputDir = path.join(projectRoot, "outputs", "annotations_template");

const colors = {
  navy: "#1F4E78",
  blue: "#DCEAF7",
  yellow: "#FFF2CC",
  green: "#E2F0D9",
  gray: "#D9E1F2",
  border: "#B7C9E2",
  text: "#1F1F1F",
  note: "#F7F7F7",
  white: "#FFFFFF",
};

function styleHeader(range) {
  range.format = {
    fill: colors.navy,
    font: { bold: true, color: colors.white, name: "Calibri", size: 11 },
    horizontalAlignment: "Center",
    verticalAlignment: "Center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: colors.border },
  };
}

function styleBody(range, fill = null) {
  range.format = {
    fill,
    font: { color: colors.text, name: "Calibri", size: 11 },
    verticalAlignment: "Center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: colors.border },
  };
}

function setColumnWidths(sheet, widths) {
  widths.forEach((width, index) => {
    sheet.getRangeByIndexes(0, index, 1, 1).format.columnWidth = width;
  });
}

function addSheetTitle(sheet, title, subtitle, lastCol) {
  const titleRange = sheet.getRange(`A1:${lastCol}1`);
  titleRange.merge();
  titleRange.values = [[title]];
  titleRange.format = {
    fill: colors.blue,
    font: { bold: true, color: colors.navy, name: "Calibri", size: 14 },
    horizontalAlignment: "Left",
    verticalAlignment: "Center",
  };
  const subRange = sheet.getRange(`A2:${lastCol}2`);
  subRange.merge();
  subRange.values = [[subtitle]];
  subRange.format = {
    fill: colors.note,
    font: { color: colors.text, name: "Calibri", size: 10 },
    wrapText: true,
    verticalAlignment: "Center",
  };
}

function addInstructionsSheet(workbook) {
  const sheet = workbook.worksheets.add("说明");
  sheet.showGridLines = false;
  addSheetTitle(
    sheet,
    "annotations.xlsx 填写说明",
    "平台提供此模板，用户下载填写后上传。不要改 sheet 名和表头；示例行上传前可删除。",
    "F",
  );

  sheet.getRange("A4:B11").values = [
    ["步骤", "说明"],
    ["1", "在正文初稿中使用 [Fig1]、[Table1] 这类对象占位符。"],
    ["2", "下载本模板，分别在 Figures、Tables、Links 中按 asset_id 填写。"],
    ["3", "图注和表注与对象占位符分离；asset_id 仅用于匹配，不直接显示在最终稿。"],
    ["4", "caption_body 推荐填写去编号前缀后的正文；caption_as_provided 可保留原样输入。"],
    ["5", "prefix_policy 只能填 template、keep_as_provided、review_required。"],
    ["6", "Links 里可直接粘贴 DOI 或完整 URL；系统只做规范化，不猜链接。"],
    ["7", "填写完可直接上传 xlsx，或与其他注释文件一起打成 annotations.zip。"],
  ];
  styleHeader(sheet.getRange("A4:B4"));
  styleBody(sheet.getRange("A5:B11"));
  setColumnWidths(sheet, [12, 92]);

  sheet.getRange("D4:F10").values = [
    ["字段", "含义", "填写提醒"],
    ["asset_id", "与 [Fig1]/[Table1] 精确对应", "区分 FigN 与 TableN，不允许猜配"],
    ["caption_body", "推荐填写的题注正文", "适合交给模板自动编号"],
    ["caption_as_provided", "用户原样输入", "如原文已带 Fig. 1 / Table 1 前缀，可填这里"],
    ["prefix_policy", "前缀处理策略", "使用下拉，不要自由发挥"],
    ["note/source/alt_text", "附加说明、来源、替代文本", "按期刊规则决定是否输出"],
    ["url_or_doi/link_text", "图表超链接", "支持 DOI 或 URL；link_text 可留空"],
  ];
  styleHeader(sheet.getRange("D4:F4"));
  styleBody(sheet.getRange("D5:F10"));
  setColumnWidths(sheet, [12, 92, 14, 18, 28, 28]);

  sheet.getRange("A12:F14").values = [
    ["错误处理", "模板校验会检查版本、sheet 名、字段名、asset_id 规则、prefix_policy 枚举和空值要求。", "", "", "", ""],
    ["不会自动做的事", "不会根据图片内容自动写图注，不会猜 DOI/URL，不会补造参考文献信息。", "", "", "", ""],
    ["版本", "template_version = annotations_template_v1", "", "", "", ""],
  ];
  sheet.getRange("A12:F14").merge(true);
  styleBody(sheet.getRange("A12:F14"), colors.green);

  sheet.freezePanes.freezeRows(3);
  return sheet;
}

function addAssetSheet(workbook, sheetName, assetPrefix) {
  const sheet = workbook.worksheets.add(sheetName);
  const inputEndRow = 40;
  sheet.showGridLines = false;
  addSheetTitle(
    sheet,
    `${sheetName} 录入表`,
    `仅填写 ${assetPrefix}N 资产。黄色区域为用户输入区，示例行上传前可删除。`,
    "G",
  );

  const header = [["asset_id", "caption_body", "caption_as_provided", "prefix_policy", "note", "source", "alt_text"]];
  const rows =
    sheetName === "Figures"
      ? [
          ["Fig1", "实验装置示意图", "", "template", "缩略语见正文。", "作者自制。", "实验装置整体布局图"],
          ["Fig2", "", "Fig. 2 Sample processing workflow", "keep_as_provided", "", "Data source: https://doi.org/10.1000/example", "样本处理流程图"],
        ]
      : [
          ["Table1", "样本基本特征", "", "template", "n = 120。", "作者整理。", "受试者基线特征表"],
          ["Table2", "", "Table 2. Ablation results", "review_required", "", "", "消融实验结果表"],
        ];

  sheet.getRange("A4:G4").values = header;
  sheet.getRange("A5:G6").values = rows;
  styleHeader(sheet.getRange("A4:G4"));
  styleBody(sheet.getRange(`A5:G${inputEndRow}`), colors.yellow);
  sheet.getRange("A5:G6").format.fill = colors.note;
  setColumnWidths(sheet, [16, 40, 40, 22, 28, 32, 28]);
  sheet.getRange(`A4:G${inputEndRow}`).format.rowHeight = 36;

  sheet.getRange(`D5:D${inputEndRow}`).dataValidation = {
    rule: { type: "list", values: ["template", "keep_as_provided", "review_required"] },
  };

  sheet.getRange("I4:J9").values = [
    ["校验规则", "说明"],
    ["asset_id", `只允许 ${assetPrefix}N，例如 ${assetPrefix}1`],
    ["caption_body", "推荐填写去编号前缀后的正文"],
    ["caption_as_provided", "若原文已写 Fig. 1 / Table 1，可原样保留"],
    ["prefix_policy", "template / keep_as_provided / review_required"],
    ["留空策略", "题注空缺则进入待补，不自动生成"],
  ];
  styleHeader(sheet.getRange("I4:J4"));
  styleBody(sheet.getRange("I5:J9"));
  setColumnWidths(sheet, [16, 40, 40, 22, 28, 32, 28, 4, 18, 40]);

  sheet.freezePanes.freezeRows(4);
  return sheet;
}

function addLinksSheet(workbook) {
  const sheet = workbook.worksheets.add("Links");
  const inputEndRow = 40;
  sheet.showGridLines = false;
  addSheetTitle(
    sheet,
    "Links 录入表",
    "用于给图表绑定 DOI 或 URL。asset_id 必须已经在 Figures 或 Tables 中存在。",
    "E",
  );

  sheet.getRange("A4:C4").values = [["asset_id", "url_or_doi", "link_text"]];
  sheet.getRange("A5:C6").values = [
    ["Fig1", "10.1000/example-doi", "Source article"],
    ["Table1", "https://example.org/dataset/table1", "Dataset page"],
  ];
  styleHeader(sheet.getRange("A4:C4"));
  styleBody(sheet.getRange(`A5:C${inputEndRow}`), colors.yellow);
  sheet.getRange("A5:C6").format.fill = colors.note;
  setColumnWidths(sheet, [16, 48, 28]);
  sheet.getRange(`A4:C${inputEndRow}`).format.rowHeight = 34;

  sheet.getRange("E4:F8").values = [
    ["规则", "说明"],
    ["url_or_doi", "可填写 DOI 或完整 URL；系统会把 DOI 规范化为 https://doi.org/..."],
    ["link_text", "可选；为空时可按规则用默认文字"],
    ["不会自动做的事", "不会按标题或图片内容猜链接"],
    ["推荐流程", "先确认资产匹配，再补链接"],
  ];
  styleHeader(sheet.getRange("E4:F4"));
  styleBody(sheet.getRange("E5:F8"));
  setColumnWidths(sheet, [16, 48, 28, 4, 18, 46]);

  sheet.freezePanes.freezeRows(4);
  return sheet;
}

async function saveRender(workbook, sheetName, filename) {
  const blob = await workbook.render({
    sheetName,
    autoCrop: "all",
    scale: 1,
    format: "png",
  });
  const bytes = new Uint8Array(await blob.arrayBuffer());
  await fs.writeFile(path.join(outputDir, filename), bytes);
}

async function main() {
  await fs.mkdir(outputDir, { recursive: true });

  const workbook = Workbook.create();
  addInstructionsSheet(workbook);
  addAssetSheet(workbook, "Figures", "Fig");
  addAssetSheet(workbook, "Tables", "Table");
  addLinksSheet(workbook);

  const inspect = await workbook.inspect({
    kind: "table",
    range: "A1:G12",
    sheetId: "说明",
    include: "values,formulas",
    tableMaxRows: 12,
    tableMaxCols: 7,
  });
  const errorScan = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 50 },
    summary: "formula error scan",
  });

  await fs.writeFile(path.join(outputDir, "inspect.txt"), inspect.ndjson);
  await fs.writeFile(path.join(outputDir, "formula_errors.txt"), errorScan.ndjson);

  await saveRender(workbook, "说明", "说明.png");
  await saveRender(workbook, "Figures", "Figures.png");
  await saveRender(workbook, "Tables", "Tables.png");
  await saveRender(workbook, "Links", "Links.png");

  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(path.join(outputDir, "annotations.xlsx"));
}

await main();
