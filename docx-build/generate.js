// 电商 AI Agent 安全评审清单 — DOCX 生成脚本
// 架构：R1 封面（DM-1 深青）+ 前置目录节（罗马页码）+ 正文节（阿拉伯页码从 1 起）
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, PageNumber, NumberFormat, AlignmentType, HeadingLevel,
  WidthType, BorderStyle, ShadingType, PageBreak, TableOfContents,
  SectionType, TableLayoutType,
} = require("docx");
const fs = require("fs");

const content = JSON.parse(fs.readFileSync(__dirname + "/content.json", "utf8"));

// ── 调色板 ──────────────────────────────────────────────
// 封面：DM-1 深青（AI/科技）；正文：Profile A 正式，纯黑正文
const COVER = {
  bg: "162235", accent: "37DCF2",
  titleColor: "FFFFFF", subtitleColor: "B0B8C0", metaColor: "90989F", footerColor: "687078",
};
const P = {
  primary: "0A1628", body: "000000", secondary: "5A6B7A",
  accent: "1B6B7A", innerLine: "C8DDE2", surface: "EDF3F5",
};

const NB = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
const noBorders = { top: NB, bottom: NB, left: NB, right: NB };
const allNoBorders = { top: NB, bottom: NB, left: NB, right: NB, insideHorizontal: NB, insideVertical: NB };

function safeText(v, ph) {
  if (v === undefined || v === null || v === "" || String(v) === "NaN" || String(v) === "undefined") {
    return ph || "\u3010\u8bf7\u586b\u5199\u3011";
  }
  return String(v);
}

// ── 封面标题布局助手（design-system.md 标准实现）──────────
function splitTitleLines(title, charsPerLine) {
  if (title.length <= charsPerLine) return [title];
  const breakAfter = new Set([..."\uff0c\u3002\u3001\uff1b\uff1a\uff01\uff1f", ..."\u7684\u4e0e\u548c\u53ca\u4e4b\u5728\u4e8e\u4e3a", ..."-_\u2014\u2013\u00b7/", ..." \t"]);
  const lines = [];
  let remaining = title;
  while (remaining.length > charsPerLine) {
    let breakAt = -1;
    for (let i = charsPerLine; i >= Math.floor(charsPerLine * 0.6); i--) {
      if (i < remaining.length && breakAfter.has(remaining[i - 1])) { breakAt = i; break; }
    }
    if (breakAt === -1) {
      const limit = Math.min(remaining.length, Math.ceil(charsPerLine * 1.3));
      for (let i = charsPerLine + 1; i < limit; i++) {
        if (breakAfter.has(remaining[i - 1])) { breakAt = i; break; }
      }
    }
    if (breakAt === -1) {
      breakAt = charsPerLine;
      const prevChar = remaining[breakAt - 1], nextChar = remaining[breakAt];
      if (prevChar && nextChar && !breakAfter.has(prevChar) && !breakAfter.has(nextChar) &&
          /[\u4e00-\u9fff]/.test(prevChar) && /[\u4e00-\u9fff]/.test(nextChar)) breakAt -= 1;
    }
    lines.push(remaining.slice(0, breakAt).trim());
    remaining = remaining.slice(breakAt).trim();
  }
  if (remaining) lines.push(remaining);
  if (lines.length > 1 && lines[lines.length - 1].length <= 2) {
    const last = lines.pop();
    lines[lines.length - 1] += last;
  }
  return lines;
}

function calcTitleLayout(title, maxWidthTwips, preferredPt = 40, minPt = 24) {
  const charsPerLine = (pt) => Math.floor(maxWidthTwips / (pt * 20));
  let titlePt = preferredPt, lines;
  while (titlePt >= minPt) {
    const cpl = charsPerLine(titlePt);
    if (cpl < 2) { titlePt -= 2; continue; }
    lines = splitTitleLines(title, cpl);
    if (lines.length <= 3) break;
    titlePt -= 2;
  }
  if (!lines || lines.length > 3) {
    lines = splitTitleLines(title, charsPerLine(minPt));
    titlePt = minPt;
  }
  return { titlePt, titleLines: lines };
}

function calcCoverSpacing(params) {
  const { titleLineCount = 1, titlePt = 36, hasSubtitle = false, hasEnglishLabel = false,
    metaLineCount = 0, fixedHeight = 800, pageHeight = 16838, marginTop = 0, marginBottom = 0 } = params;
  const SAFETY = 1200;
  const usableHeight = pageHeight - marginTop - marginBottom - SAFETY;
  const titleHeight = titleLineCount * (titlePt * 23 + 200);
  const subtitleHeight = hasSubtitle ? (12 * 23 + 600) : 0;
  const englishLabelHeight = hasEnglishLabel ? (9 * 23 + 600) : 0;
  const metaHeight = metaLineCount * (10 * 23 + 100);
  const implicitParaHeight = 3 * 300;
  const contentHeight = titleHeight + subtitleHeight + englishLabelHeight + metaHeight + fixedHeight + implicitParaHeight;
  const safeRemaining = Math.max(usableHeight - contentHeight, 400);
  const FOOTER_MIN = 800;
  const rawTop = Math.floor(safeRemaining * 0.45);
  const rawBottom = Math.floor(safeRemaining * 0.45);
  const bottomSpacing = Math.max(rawBottom, FOOTER_MIN);
  const topSpacing = Math.max(rawTop - Math.max(0, FOOTER_MIN - rawBottom), 400);
  const midSpacing = Math.max(safeRemaining - topSpacing - bottomSpacing, 0);
  return { topSpacing, midSpacing, bottomSpacing };
}

// ── R1 封面（纯段落左对齐，零嵌套表）────────────────────
function buildCoverR1(config) {
  const padL = 1200, padR = 800;
  const availableWidth = 11906 - padL - padR - 300;
  const { titlePt, titleLines } = calcTitleLayout(config.title, availableWidth, 40, 24);
  const titleSize = titlePt * 2;
  const spacing = calcCoverSpacing({
    titleLineCount: titleLines.length, titlePt,
    hasSubtitle: !!config.subtitle, hasEnglishLabel: !!config.englishLabel,
    metaLineCount: (config.metaLines || []).length, fixedHeight: 400,
  });
  const accentLeft = { style: BorderStyle.SINGLE, size: 8, color: COVER.accent, space: 12 };
  const children = [];

  children.push(new Paragraph({ spacing: { before: spacing.topSpacing } }));

  if (config.englishLabel) {
    children.push(new Paragraph({
      indent: { left: padL, right: padR }, spacing: { after: 500 },
      border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: COVER.accent, space: 8 } },
      children: [new TextRun({ text: config.englishLabel.split("").join("  "),
        size: 18, color: COVER.accent, font: { ascii: "Calibri", eastAsia: "SimHei" }, characterSpacing: 40 })],
    }));
  }

  for (let i = 0; i < titleLines.length; i++) {
    children.push(new Paragraph({
      indent: { left: padL },
      spacing: { after: i < titleLines.length - 1 ? 100 : 300, line: Math.ceil(titlePt * 23), lineRule: "atLeast" },
      children: [new TextRun({ text: titleLines[i], size: titleSize, bold: true,
        color: COVER.titleColor, font: { eastAsia: "SimHei", ascii: "Arial" } })],
    }));
  }

  if (config.subtitle) {
    children.push(new Paragraph({
      indent: { left: padL }, spacing: { after: 800 },
      children: [new TextRun({ text: config.subtitle, size: 24, color: COVER.subtitleColor,
        font: { eastAsia: "Microsoft YaHei", ascii: "Arial" } })],
    }));
  }

  for (const line of (config.metaLines || [])) {
    children.push(new Paragraph({
      indent: { left: padL + 200 }, spacing: { after: 80 },
      border: { left: accentLeft },
      children: [new TextRun({ text: safeText(line), size: 24, color: COVER.metaColor,
        font: { eastAsia: "Microsoft YaHei", ascii: "Arial" } })],
    }));
  }

  children.push(new Paragraph({ spacing: { before: spacing.bottomSpacing } }));

  children.push(new Paragraph({
    indent: { left: padL, right: padR },
    border: { top: { style: BorderStyle.SINGLE, size: 2, color: COVER.accent, space: 8 } },
    spacing: { before: 200 },
    children: [
      new TextRun({ text: config.footerLeft || "", size: 16, color: COVER.footerColor, font: { eastAsia: "Microsoft YaHei", ascii: "Arial" } }),
      new TextRun({ text: "                                        " }),
      new TextRun({ text: config.footerRight || "", size: 16, color: COVER.footerColor, font: { ascii: "Arial" } }),
    ],
  }));

  return [new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    layout: TableLayoutType.FIXED,
    borders: allNoBorders,
    rows: [new TableRow({
      height: { value: 16838, rule: "exact" },
      children: [new TableCell({
        shading: { type: ShadingType.CLEAR, fill: COVER.bg }, borders: noBorders,
        verticalAlign: "top",
        children,
      })],
    })],
  })];
}

// ── 正文构件 ────────────────────────────────────────────
const FONT_BODY = { ascii: "Times New Roman", eastAsia: "SimSun" };
const FONT_HEAD = { ascii: "Times New Roman", eastAsia: "SimHei" };

function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    alignment: AlignmentType.CENTER,
    spacing: { before: 360, after: 160, line: 312 },
    children: [new TextRun({ text, bold: true, size: 32, color: P.primary, font: FONT_HEAD })],
  });
}

function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 260, after: 120, line: 312 },
    children: [new TextRun({ text, bold: true, size: 30, color: P.primary, font: FONT_HEAD })],
  });
}

function bodyPara(block) {
  const runs = [];
  if (block.boldLead) runs.push(new TextRun({ text: block.boldLead, bold: true, size: 24, color: P.body, font: FONT_BODY }));
  runs.push(new TextRun({ text: safeText(block.text), size: 24, color: P.body, font: FONT_BODY }));
  return new Paragraph({
    alignment: AlignmentType.JUSTIFIED,
    indent: { firstLine: 480 },
    spacing: { line: 312, after: 60 },
    children: runs,
  });
}

function refPara(text) {
  return new Paragraph({
    indent: { left: 480 },
    spacing: { line: 312, after: 60 },
    border: { left: { style: BorderStyle.SINGLE, size: 6, color: P.accent, space: 10 } },
    children: [new TextRun({ text: safeText(text), size: 21, color: P.secondary, font: FONT_BODY })],
  });
}

function passPara(text) {
  return new Paragraph({
    alignment: AlignmentType.JUSTIFIED,
    indent: { firstLine: 480 },
    spacing: { line: 312, after: 60 },
    children: [
      new TextRun({ text: "\u901a\u8fc7\u6807\u51c6\uff1a", bold: true, size: 24, color: P.body, font: FONT_BODY }),
      new TextRun({ text: safeText(text), size: 24, color: P.body, font: FONT_BODY }),
    ],
  });
}

function contextPara(text) {
  return new Paragraph({
    alignment: AlignmentType.JUSTIFIED,
    indent: { firstLine: 480 },
    spacing: { line: 312, after: 100 },
    children: [new TextRun({ text: safeText(text), size: 24, color: P.secondary, font: FONT_BODY })],
  });
}

function checkItems(items) {
  return items.map(item => new Paragraph({
    indent: { left: 720, hanging: 360 },
    spacing: { line: 312, after: 40 },
    children: [
      new TextRun({ text: "\u25a1 ", size: 24, color: P.accent, font: { ascii: "SimSun", eastAsia: "SimSun" } }),
      new TextRun({ text: safeText(item), size: 24, color: P.body, font: FONT_BODY }),
    ],
  }));
}

function buildTable(block) {
  const elements = [];
  elements.push(new Paragraph({
    keepNext: true,
    spacing: { before: 120, after: 80, line: 312 },
    children: [new TextRun({ text: safeText(block.title), bold: true, size: 21, color: P.primary, font: FONT_HEAD })],
  }));
  const headerRow = new TableRow({
    tableHeader: true, cantSplit: true,
    children: block.header.map((text, i) => new TableCell({
      children: [new Paragraph({
        spacing: { line: 276 },
        children: [new TextRun({ text: safeText(text), bold: true, size: 21, color: "FFFFFF", font: FONT_HEAD })],
      })],
      shading: { type: ShadingType.CLEAR, fill: P.accent },
      margins: { top: 60, bottom: 60, left: 120, right: 120 },
      width: { size: block.widths[i], type: WidthType.PERCENTAGE },
    })),
  });
  const dataRows = block.rows.map(cells => new TableRow({
    cantSplit: true,
    children: cells.map((text, i) => new TableCell({
      children: [new Paragraph({
        spacing: { line: 276 },
        children: [new TextRun({ text: safeText(text), size: 21, color: P.body, font: FONT_BODY })],
      })],
      margins: { top: 60, bottom: 60, left: 120, right: 120 },
      width: { size: block.widths[i], type: WidthType.PERCENTAGE },
    })),
  }));
  elements.push(new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    borders: {
      top: { style: BorderStyle.SINGLE, size: 4, color: P.accent },
      bottom: { style: BorderStyle.SINGLE, size: 4, color: P.accent },
      left: NB, right: NB,
      insideHorizontal: { style: BorderStyle.SINGLE, size: 1, color: P.innerLine },
      insideVertical: NB,
    },
    rows: [headerRow, ...dataRows],
  }));
  elements.push(new Paragraph({ spacing: { after: 100 }, children: [] }));
  return elements;
}

function centerPara(text) {
  return new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 160, after: 60, line: 312 },
    children: [new TextRun({ text: safeText(text), size: 24, color: P.body, font: FONT_BODY })],
  });
}

function renderBlocks(blocks) {
  const out = [];
  for (const block of blocks) {
    if (block.type === "h2") out.push(h2(block.text));
    else if (block.type === "para") out.push(bodyPara(block));
    else if (block.type === "ref") out.push(refPara(block.text));
    else if (block.type === "pass") out.push(passPara(block.text));
    else if (block.type === "context") out.push(contextPara(block.text));
    else if (block.type === "checks") out.push(...checkItems(block.items));
    else if (block.type === "table") out.push(...buildTable(block));
    else if (block.type === "center") out.push(centerPara(block.text));
  }
  return out;
}

// ── 页眉页脚 ────────────────────────────────────────────
function pageNumFooter() {
  return new Footer({ children: [new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [new TextRun({ children: [PageNumber.CURRENT], size: 18, color: "808080", font: FONT_BODY })],
  })] });
}

function docHeader() {
  return new Header({ children: [new Paragraph({
    alignment: AlignmentType.CENTER,
    border: { bottom: { style: BorderStyle.SINGLE, size: 2, color: P.innerLine, space: 4 } },
    children: [new TextRun({ text: content.cover.title, size: 18, color: "808080", font: FONT_BODY })],
  })] });
}

// ── 组装 ────────────────────────────────────────────────
const pgSize = { width: 11906, height: 16838 };
const pgMargin = { top: 1440, bottom: 1440, left: 1701, right: 1417 };

const bodyChildren = [];
for (const chapter of content.chapters) {
  bodyChildren.push(h1(chapter.title));
  bodyChildren.push(...renderBlocks(chapter.blocks));
}

const frontMatter = [
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 480, after: 360 },
    children: [new TextRun({ text: "\u76ee  \u5f55", bold: true, size: 32, font: FONT_HEAD, color: P.primary })],
  }),
  new TableOfContents("Table of Contents", { hyperlink: true, headingStyleRange: "1-2" }),
  new Paragraph({
    spacing: { before: 200 },
    children: [new TextRun({
      text: "\u6ce8\uff1a\u672c\u76ee\u5f55\u7531\u57df\u4ee3\u7801\u751f\u6210\u3002\u7f16\u8f91\u6587\u6863\u540e\u8bf7\u53f3\u952e\u70b9\u51fb\u76ee\u5f55\u5e76\u9009\u62e9\u201c\u66f4\u65b0\u57df\u201d\u4ee5\u5237\u65b0\u9875\u7801\u3002",
      italics: true, size: 18, color: "888888", font: FONT_BODY,
    })],
  }),
];

const doc = new Document({
  creator: "Security Review",
  title: content.cover.title,
  styles: {
    default: {
      document: {
        run: { font: FONT_BODY, size: 24, color: P.body },
        paragraph: { spacing: { line: 312 } },
      },
      heading1: {
        run: { font: FONT_HEAD, size: 32, bold: true, color: P.primary },
        paragraph: { spacing: { before: 360, after: 160, line: 312 }, outlineLevel: 0 },
      },
      heading2: {
        run: { font: FONT_HEAD, size: 30, bold: true, color: P.primary },
        paragraph: { spacing: { before: 260, after: 120, line: 312 }, outlineLevel: 1 },
      },
    },
  },
  sections: [
    { // 第 1 节：封面 — 页边距为 0，无页眉页脚页码
      properties: { page: { size: pgSize, margin: { top: 0, bottom: 0, left: 0, right: 0 } } },
      children: buildCoverR1(content.cover),
    },
    { // 第 2 节：目录 — 罗马页码
      properties: {
        type: SectionType.NEXT_PAGE,
        page: { size: pgSize, margin: pgMargin, pageNumbers: { start: 1, formatType: NumberFormat.UPPER_ROMAN } },
      },
      footers: { default: pageNumFooter() },
      children: frontMatter,
    },
    { // 第 3 节：正文 — 阿拉伯页码从 1 重新计数
      properties: {
        type: SectionType.NEXT_PAGE,
        page: { size: pgSize, margin: pgMargin, pageNumbers: { start: 1, formatType: NumberFormat.DECIMAL } },
      },
      headers: { default: docHeader() },
      footers: { default: pageNumFooter() },
      children: bodyChildren,
    },
  ],
});

const OUT = "D:/Apps/\u7535\u5546/\u7535\u5546Agent\u5b89\u5168\u8bc4\u5ba1Checklist.docx";
Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync(OUT, buf);
  console.log("OK " + OUT + " (" + buf.length + " bytes)");
});
