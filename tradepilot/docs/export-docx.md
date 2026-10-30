# 上架包 DOCX 导出（零依赖实现）

模块：`app/export_docx.py` · 入口：`GET /api/missions/{mission_id}/export?format=docx`
测试：`tests/test_export_docx.py`（19 个用例，只用标准库拆包断言）

作品 MVP 模块⑥「上架包导出」原先只有 `md` / `json` 两种形态。评委与卖家真正需要的是
**能直接打开、能打印、能交给运营的 Word 文件**，所以补齐 `docx`。

## 一、为什么零新增依赖

`.docx` 不是二进制私有格式，它就是**一个 zip 包 + 若干 XML 部件**（WordprocessingML）。
因此不需要 `python-docx`（它自己也要装 `lxml`），用标准库 `zipfile` + 手写 XML 即可：

| 部件 | 作用 | 必需 |
|---|---|---|
| `[Content_Types].xml` | 声明每个部件的 MIME 类型；**必须是 zip 里的第一个条目** | ✅ |
| `_rels/.rels` | 包级关系：指向正文 `word/document.xml` | ✅ |
| `word/document.xml` | 正文（段落 `<w:p>`、表格 `<w:tbl>`） | ✅ |
| `word/_rels/document.xml.rels` | 正文到样式的部件关系 | 本模块附带 |
| `word/styles.xml` | `Normal` / `Title` / `Heading1` / `Heading2` / `Note` 样式 | 本模块附带 |

实现要点（`app/export_docx.py`）：

- `build_docx(report: dict) -> bytes`：纯函数，返回 docx 字节流，不落盘、不联网、无全局状态；
  路由层直接把字节塞进 `Response`。
- zip 条目固定 `date_time=(2026,1,1,0,0,0)`：同一份报告两次导出**字节级一致**，便于做证据归档与 diff。
- **XML 转义**：文本统一走 `_esc()`（`& < > " '` 全部转义），并剔除 XML 1.0 禁止的控制字符
  （抓回来的商品标题里可能混入 `\x00`/`\x07`，不剔除会让 Word 报「文件已损坏」）。
- **真表格**：商品清单用 `<w:tbl>` + `<w:tblGrid>` + `<w:tr>/<w:tc>`，不是把 `|` 塞进段落；
  `tblPr` 带完整 `tblBorders`，表头行 `tblHeader` + 底纹，跨页时会重复表头。
- 项目符号用 `• ` 字符 + `w:ind/w:hanging`，不引入 `numbering.xml`：少一个部件 = 少一处兼容坑。
- 脏报告不抛异常：`report` 非 dict、`results` 缺失、`items` 不是列表等情况统一降级为
  「如实留空 / 注明未生成」，导出接口不会 500。

## 二、章节结构（与 Markdown 导出同序同口径）

| # | 章节 | 数据来源（`report.results.*.data`） | 渲染形态 |
|---|---|---|---|
| — | 标题与任务元信息 | `mission_id / goal / started_at / elapsed_s / plan_engine / autonomy` | Title + 段落 |
| 一 | 真实市场数据（每条带来源） | `market_scan`：`items[] / price_band / currency / source_url / scan_provenance_id` | **表格**（#/商品/价格/单品页/存证）+ 价格带 + 来源页 |
| 二 | 定价沙盘 | `pricing_sandbox`：`fx_rate / cost_basis_* / landed_cost / cost_breakdown / floor_price / band_source / suggestion` | 项目符号 |
| 三 | 合规体检 | `compliance_check`：`checks[]`（四态）+ `basis` | 项目符号（状态码内联，见下） |
| 四 | 关键词层 | `keyword_plan`：`buckets / competition_proxy / search_terms(_bytes)` | 项目符号 + 段落 |
| 五 | Listing 文案 | `listing_draft`：`copy.title / bullets / description / generated_by` | 段落 + 项目符号 |
| 六 | 文案合规审计 | `listing_audit`：`score / grade / verdict / counts / issues[] / basis` | 段落 + 项目符号 |
| 七 | 决策实录 | `report.decisions[]`：`n / tool / attempt / status / summary / reason` | 编号段落 + 理由缩进 |
| 尾 | 溯源声明 | 固定文案 | Note 段落 |

溯源要求落地在两处：商品表每行都带 `存证#<provenance_id>` 与**单品页 URL**
（不是搜索结果页——评委点存证号必须落到那件商品上），文末固定写
「数字均来自真实抓取与确定性计算」。

## 三、合规四态的诚实红线

`checks[].verification` 四态在 DOCX 里各自独立成段，**文案与 `app/store.py` 第三节的
`marks` 表完全一致**，并额外把原始状态码写进括号，方便机器复核：

| `verification` | 渲染文案 | 证据含义 |
|---|---|---|
| `clause_matched` | ✅ 已定位官方条款原文 | 唯一算「已验证」的状态 |
| `page_only` | ⚠ 已取得官方正文但未定位到条款原文 | 页面真实可读，但没定位到条款原文 |
| `blocked` | ⛔ HTTP 200 但内容不可核验（拦截页/SPA 空壳/软 404） | 状态码 200 不等于内容可核验 |
| `offline` | ⚠ 网络不可达（引用标注） | 网络不可达，仅作引用标注 |

`blocked` / `offline` **绝不出现**「已定位官方条款原文」或 ✅，且无原文时补一句
「未取得可核验正文，不作为合规依据」。测试 `test_compliance_four_states` 对这四个状态逐个断言，
任何人把它「美化」成已验证都会当场变红。

## 四、如何在没有 Word 的情况下校验产物

`.docx` 的合法性完全可以用命令行复核，不需要装 Office，也不需要 `python-docx`。

### 1. 从接口导出一份

```bash
# 服务已在跑（默认 8080）
curl -sS -o mission.docx \
  "http://127.0.0.1:8080/api/missions/mission-20260101-101010/export?format=docx"

# 或不起服务，直接用模块生成（PowerShell 用 -Encoding Byte 写二进制见下一行说明）
python -c "import json,sys; from app.export_docx import build_docx; open('mission.docx','wb').write(build_docx({'mission_id':'mission-20260101-101010','goal':'狗床出海'}))"
```

### 2. 校验包结构与 XML 合法性（一条命令搞定）

```bash
python - <<'PY'
import zipfile, xml.etree.ElementTree as ET
z = zipfile.ZipFile("mission.docx")
print("条目：", z.namelist())
print("CRC 全部正确：", z.testzip() is None)
for need in ("[Content_Types].xml", "_rels/.rels", "word/document.xml"):
    assert need in z.namelist(), f"缺少 {need}"
for name in z.namelist():
    ET.fromstring(z.read(name))           # 任何部件不是合法 XML 都会在这里抛错
print("所有部件均为合法 XML：OK")
print("首个条目是 [Content_Types].xml：", z.namelist()[0] == "[Content_Types].xml")
PY
```

Windows PowerShell 里 heredoc 不可用，等价写法：

```powershell
python -c "import zipfile,xml.etree.ElementTree as ET; z=zipfile.ZipFile('mission.docx'); print(z.namelist()); print('CRC ok', z.testzip() is None); [ET.fromstring(z.read(n)) for n in z.namelist()]; print('XML ok')"
```

### 3. 拆包看正文（不装 Word 也能读内容）

```bash
unzip -p mission.docx word/document.xml | head -c 2000          # macOS / Linux
# 把 XML 里的文字抽成纯文本（验证标题、数字、存证号真的写进去了）
python -c "import sys,zipfile,xml.etree.ElementTree as ET; W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'; r=ET.fromstring(zipfile.ZipFile('mission.docx').read('word/document.xml')); print('\n'.join(''.join(t.text or '' for t in p.iter(W+'t')) for p in r.iter(W+'p')))"
# 数表格：有没有真的 <w:tbl>（而不是把竖线塞进段落）
python -c "import zipfile,re; x=zipfile.ZipFile('mission.docx').read('word/document.xml').decode(); print('tbl=',x.count('<w:tbl>'),'tr=',x.count('<w:tr>'),'tc=',x.count('<w:tc>'),'伪表格竖线=',x.count('|'))"
```

期望：`tbl` ≥ 1、`tc` = 列数 × 行数、`伪表格竖线` = 0。

### 4. 有 Office/WPS 时的人工确认

双击打开，检查三件事：中文不乱码、章节标题有层级（导航窗格可见 Heading 1）、
商品表是表格且表头有底纹。这三项对应上面三条机器断言，机器过了人工通常也过。

## 五、测试覆盖（`tests/test_export_docx.py`）

| 用例类 | 断言内容 |
|---|---|
| `TestDocxPackage` | 返回 bytes、是合法 zip、三个必需部件齐全、`[Content_Types]` 含 main document、`document.xml` 可被 `xml.etree` 解析且有 `sectPr`、`testzip()` 无 CRC 错 |
| `TestDocxContent` | 七个章节齐全且顺序正确、关键数字/存证号/单品页 URL/价格带都在正文、`<w:tbl>` 是真表格（1 表头 + N 行 × 5 列）、正文无 `|`、合规四态逐个校验、`blocked`/`offline` 不得出现「已定位官方条款原文」 |
| `TestDocxEscaping` | `& < > " '` 全部转义后仍是合法 XML、控制字符被剔除、转义后纯文本内容与原文一致 |
| `TestDocxDegenerateReports` | `{}` / `None` / `"junk"` / `0` / `[]` / 字段类型错误 均不抛异常且产物合法 |
| `TestExportRoute` | `format=docx` 返回 200 + 正确的 `media_type` 与 `Content-Disposition`、`md`/`json` 分支行为未变、非法 format → 400 |

跑一遍：

```bash
python -m unittest tests.test_export_docx -v          # 仅 DOCX
python -m unittest discover -s tests -t .             # 全量
```
