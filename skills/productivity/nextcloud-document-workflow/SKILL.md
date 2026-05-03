---
name: nextcloud-document-workflow
description: "Find/extract documents from Nextcloud → analyze/summarize with LLM → create derivative documents (markdown summary, pptx presentation) → upload back to Nextcloud. For when a user asks to 'summarize the PDF on Nextcloud', 'make slides from the report', 'extract and analyze that document'."
tags: [nextcloud, workflow, document-processing, pdf, pptx, summary]
---

# Nextcloud Document Workflow

End-to-end pipeline for processing documents stored on Nextcloud.

## Trigger

Use this skill when the user asks to:
- Find a document/file on Nextcloud and summarize/analyze it
- Read a PDF/report/docx from Nextcloud and create derivative outputs (presentation, summary, notes)
- Extract content from a Nextcloud file and produce a deliverable (slides, report, markdown)
- "Make a presentation from the document on Nextcloud"
- "Summarize the PDF in my Nextcloud"
- Analyze **tabular data** (CSV files, zip archives with CSVs) from Nextcloud and create data-driven presentations with charts
- "Make a presentation from the data in that zip/CSV on Nextcloud"

## Pipeline Steps (in order)

### 1. Find the document on Nextcloud

**Preferred: List root directory or search by name/pattern**

```bash
# List files in root or a specific folder
curl -s -u "$NEXTCLOUD_USER:$NEXTCLOUD_PASSWORD" \
  -X PROPFIND -H "Depth: 1" \
  "$NEXTCLOUD_URL/remote.php/dav/files/$NEXTCLOUD_USER/" 2>&1

# List files in a subfolder
curl -s -u "$NEXTCLOUD_USER:$NEXTCLOUD_PASSWORD" \
  -X PROPFIND -H "Depth: 1" \
  "$NEXTCLOUD_URL/remote.php/dav/files/$NEXTCLOUD_USER/Documents/" 2>&1

# Search by name (case-insensitive grep on href)
curl -s -u "$NEXTCLOUD_USER:$NEXTCLOUD_PASSWORD" \
  -X PROPFIND -H "Depth: infinity" \
  "$NEXTCLOUD_URL/remote.php/dav/files/$NEXTCLOUD_USER/" 2>&1 | \
  grep -oP "Agenti"  # or your search term
```

**Also try ncx CLI (but note limitations):**
```bash
ncx files list           # works, lists root
ncx files list --path /  # list root explicitly
ncx files search "pdf"   # limited search by name/term
```

> ⚠️ **Known pitfall**: `ncx files list` does NOT support `--json` or a path argument directly. Use `-p` path but double-check syntax. `--recursive` flag also fails (exit code 2). For reliable XML parsing, use raw curl PROPFIND instead.
>
> ✅ **ncx files search works well** for quick pattern matching by filename. Try multiple search terms in sequence (e.g., "AI", "libro", "pdf", "presentazione") to locate the document if the first search returns nothing.

### 2. Extract the document content

Use `ncx files extract` — it downloads from Nextcloud and converts to Markdown using MarkItDown:

```bash
ncx files extract /Documents/mydocument.pdf
```

Supported formats: PDF, DOCX, XLSX, PPTX, images (OCR), HTML, CSV, JSON, XML, EPUB.

The output is JSON with the extracted text in the `content` field. Parse it:

```bash
ncx files extract /path/to/doc.pdf 2>&1 | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['content'])" > /tmp/doc-content.md
```

Or extract relevant portions using grep/jq.

> **Alternative (fallback)**: If ncx extract fails, download the file via curl WebDAV and use local tools:
> ```bash
> curl -s -u "$USER:$PASS" "$NC_URL/remote.php/dav/files/$USER/Documents/file.pdf" -o /tmp/file.pdf
> ```

### 3. Analyze/summarize with LLM

Once you have the text content in a local file or variable:

1. Read the extracted content (markdown text)
2. Use the LLM to produce:
   - A comprehensive summary (markdown)
   - Key insights, statistics, quotes
   - Structured notes for derivative documents

Write the summary to a local file:
```bash
cat > /tmp/summary.md << 'EOF'
# Summary: [Document Title]
...
EOF
```

### 3b. Variant: CSV / Tabular Data Analysis

If the source is CSV files (possibly inside a zip archive), the workflow shifts from LLM summarization to **quantitative data analysis with pandas**:

#### Download and extract the data

```bash
# Download zip from Nextcloud
ncx files download /export/Data.zip --local /tmp/Data.zip

# Extract with Python (unzip may not be installed)
python3 -c "import zipfile; z=zipfile.ZipFile('/tmp/Data.zip'); z.extractall('/tmp/Data')"
```

#### Inspect file contents first

```bash
# Quick peek at file structure and date formats
python3 -c "import zipfile; z=zipfile.ZipFile('/tmp/Data.zip'); z.printdir()"

# Read the Readme if it exists
cat /tmp/Data/Readme.md 2>/dev/null

# Preview CSV structure and check date format
head -5 /tmp/Data/*.csv
```

#### Analyze with pandas

```python
import pandas as pd

# Preview each CSV
df = pd.read_csv('/tmp/Data/orders.csv')
print(df.shape, list(df.columns))
print(df.head(3))

# Parse dates — check format first with head -5
# Common formats: '%d/%m/%Y %H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'
df['date_col'] = pd.to_datetime(df['date_col'], format='%d/%m/%Y %H:%M', errors='coerce')

# ⚠️ Always check date parsing results (min/max, any NaTs)
print("Date range:", df['date_col'].min(), "-", df['date_col'].max())
print("Null dates:", df['date_col'].isna().sum())

# Key aggregations (e-commerce example)
stats = {
    'total_orders': int(len(df_orders)),
    'total_customers': int(df_customers['customer_unique_id'].nunique()),
    'total_revenue': round(float(df_payments['payment_value'].sum()), 2),
    'avg_order_value': round(float(df.groupby('order_id')['payment_value'].sum().mean()), 2),
    'avg_review_score': round(float(df_reviews['review_score'].mean()), 2)
}

# Monthly trends
df['month'] = df['date_col'].dt.to_period('M').astype(str)
monthly = df.groupby('month').agg(
    revenue=('value_col','sum'),
    orders=('order_id','nunique')
).reset_index()
monthly['revenue'] = monthly['revenue'].round(2)

# Top categories / segments
top_n = df.groupby('category_col').agg(
    revenue=('value_col','sum'),
    count=('id_col','nunique')
).sort_values('revenue',ascending=False).head(15).reset_index()
top_n['revenue'] = top_n['revenue'].round(2)

# Distribution analysis
dist = df['score_col'].value_counts().sort_index().reset_index()
dist.columns = ['score','count']

# Cross-table merges (join orders → items → payments → reviews)
merged = orders.merge(items, on='order_id')
merged = merged.merge(payments.groupby('order_id')['payment_value'].sum().reset_index(), on='order_id')
merged = merged.merge(customers, on='customer_id')

# Delivery performance (date diff analysis)
df_orders['delivery_diff'] = (df_orders['delivered_date'] - df_orders['estimated_date']).dt.days
late_pct = float((df_orders['delivery_diff'] > 0).mean() * 100)
```

⚠️ **Avoid JSON serialization errors**: pandas int64/float64 types aren't JSON-serializable. Convert with `int()`, `float()`, and `round()` before writing to JSON.

#### Build chart-driven PPTX

Use pptxgenjs with actual chart data from the analysis. Key chart patterns:

```javascript
// Revenue trend bar chart (vertical bars, time series)
slide.addChart(pres.charts.BAR, [{
  name: "Revenue", labels: monthlyLabels, values: monthlyValues
}], {
  x: 0.5, y: 1.2, w: 9, h: 4.0, barDir: "col",
  chartColors: ["0D9488"],
  valGridLine: { color: "E2E8F0", size: 0.5 },
  catGridLine: { style: "none" },
  showLegend: false, showTitle: false
});

// Top categories bar chart (horizontal for category names)
slide.addChart(pres.charts.BAR, [{
  name: "Revenue", labels: catLabels, values: catValues
}], {
  x: 0.5, y: 1.2, w: 6.0, h: 4.2, barDir: "bar",
  chartColors: ["0D9488"],
  showValue: true, dataLabelPosition: "outEnd", showLegend: false
});

// Payment method pie chart with percentages
slide.addChart(pres.charts.PIE, [{
  name: "Payment", labels: payLabels, values: payValues
}], {
  x: 0.5, y: 1.2, w: 4.5, h: 4.0,
  showPercent: true, showLegend: true, showTitle: false
});

// Stat callout cards — use RECTANGLE with top accent bar + large number
const mkShadow = () => ({ type: "outer", color: "000000", blur: 8, offset: 2, angle: 135, opacity: 0.10 });
slide.addShape(pres.shapes.RECTANGLE, { x: cx, y: cy, w: 2.1, h: 2.0,
  fill: { color: "FFFFFF" }, shadow: mkShadow() });
slide.addShape(pres.shapes.RECTANGLE, { x: cx, y: cy, w: 2.1, h: 0.06, fill: { color: accentColor } });
slide.addText("64,438", { x: cx+0.15, y: cy+0.7, w: 1.8, h: 0.5,
  fontSize: 22, bold: true, color: "0F172A", margin: 0 });
slide.addText("Total Orders", { x: cx+0.15, y: cy+1.15, w: 1.8, h: 0.35,
  fontSize: 11, color: "64748B", margin: 0 });
slide.addText("Delivered orders", { x: cx+0.15, y: cy+1.5, w: 1.8, h: 0.3,
  fontSize: 9, color: "64748B", italic: true, margin: 0 });
```

**Suggested slide structure for a data-report deck:**
1. **Title slide** — dark background, key dates + headline stats in subtitle
2. **KPI overview** — 6-7 metric cards in 2 rows with accent color bars
3. **Revenue/order trend** — bar chart with insight callout below
4. **Top categories** — horizontal bar chart + top-3 highlight cards on right
5. **Payment methods** — pie chart + color-coded detail cards on right
6. **Geographic distribution** — bar chart + state dominance callout
7. **Customer satisfaction** — review score bar chart + positive/negative stat cards
8. **Delivery performance** — two large stat cards side-by-side (on-time vs late)
9. **Key takeaways** — dark background, numbered list with icons

**Color palette for e-commerce/business data:**
- Primary: Teal (`0D9488`), Navy (`1A2744`), White
- Accents: Gold (`F59E0B`), Orange (`F97316`), Red (`EF4444`), Green (`10B981`)
- Background: Off-white (`F0FDFA`), Light gray (`E2E8F0`)

### 4. Create derivative documents

#### Markdown summary
Write directly to a .md file on disk.

#### PPTX presentation
Follow the `pptx` skill (pptxgenjs via Node.js):

```bash
cat > /tmp/create-pptx.js << 'JSEOF'
const pptxgen = require("pptxgenjs");
const pres = new pptxgen();
pres.layout = "LAYOUT_16x9";
// ... build slides ...
pres.writeFile({ fileName: "/tmp/output.pptx" })
  .then(() => console.log("OK"))
  .catch(err => console.error("ERROR:", err));
JSEOF
# When running from /tmp, point to global node_modules:
NODE_PATH=/usr/local/lib/node_modules node /tmp/create-pptx.js
```

> ⚠️ **NODE_PATH pitfall**: If pptxgenjs, react-icons, react, sharp are installed globally (via `npm install -g`) but your script lives in /tmp or another non-node_module path, Node won't find them. Run with `NODE_PATH=$(npm root -g)` or `NODE_PATH=/usr/local/lib/node_modules`.

Design guidelines from the pptx skill:
- Pick a bold color palette relevant to the topic (not default blue)
- Dark/high-contrast theme for AI/tech topics
- Vary layouts across slides (cards, columns, callouts)
- Icons in colored circles, accent bars on cards
- Title: 28-44pt bold, body: 12-16pt
- **Never** use `#` in hex colors, **never** reuse option objects across calls

#### DOCX document
Follow the `docx` skill for Word documents.

### 5. Upload derivatives to Nextcloud

**Option A — Write local file via ncx:**
```bash
ncx files write /Riassunto-Documento.md --local /tmp/summary.md
ncx files write /Presentazione-Documento.pptx --local /tmp/output.pptx
```

**Option B — Direct curl WebDAV PUT (more reliable):**
```bash
curl -s -u "$NEXTCLOUD_USER:$NEXTCLOUD_PASSWORD" \
  -T /tmp/summary.md \
  -H "Content-Type: text/markdown" \
  "$NEXTCLOUD_URL/remote.php/dav/files/$NEXTCLOUD_USER/Riassunto-Documento.md"

curl -s -u "$NEXTCLOUD_USER:$NEXTCLOUD_PASSWORD" \
  -T /tmp/output.pptx \
  -H "Content-Type: application/vnd.openxmlformats-officedocument.presentationml.presentation" \
  "$NEXTCLOUD_URL/remote.php/dav/files/$NEXTCLOUD_USER/Presentazione-Documento.pptx"
```

**Option C — Write text content directly:**
```bash
ncx files write /nota.md --content "Testo del file"
```

### 6. Verify

```bash
# Check HTTP status (should be 200/201)
curl -s -o /dev/null -w "%{http_code}" -u "$USER:$PASS" \
  "$NC_URL/remote.php/dav/files/$USER/Riassunto-Documento.md"

# List root to confirm
curl -s -u "$USER:$PASS" -X PROPFIND -H "Depth: 1" \
  "$NC_URL/remote.php/dav/files/$USER/" 2>&1 | grep -oP 'href>[^<]+' | tail -5
```

## Pitfalls

- **ncx `--json` flag**: Not supported on `files list`. Use raw curl PROPFIND for programmatic listing.
- **ncx file paths**: Must be absolute from user root, e.g. `/Documents/file.pdf` not `Documents/file.pdf`. The leading `/` matters.
- **Extract output**: Contains both stdout progress messages AND the JSON. The JSON starts at the first `{`. Use `2>&1 | tail -1` or parse with jq to strip noise.
- **PPTX hex colors**: NEVER use `#` prefix — causes file corruption. `"06B6D4"` ✅, `"#06B6D4"` ❌.
- **PPTX object reuse**: PptxGenJS mutates option objects in-place. Create factory functions returning fresh objects for shadows, fills, etc.
- **Content-Type header**: Not strictly required for WebDAV PUT, but setting it correctly avoids MIME guess issues.
- **Memory**: The ncx extract output can be very large (100k+ chars for long PDFs). Consider truncating or summarizing sections in chunks if the content exceeds context limits. The JSON output contains the full text in the `content` field — you can read it directly in your response without writing to an intermediate file.
- **ncx files write --local**: Works well for uploading local files. Path must be absolute from user root, e.g. `/Riassunto.md` (leading `/` required).
- **Pandas date parsing**: Always inspect CSV date columns with `head -5` before parsing. Wrong format strings (`%Y` vs `%y`, missing `%H:%M`) silently produce all-NaN columns.
- **Pandas int64 JSON serialization**: Use `int()`, `float()`, and `round()` explicitly on pandas values before writing to JSON — they raise TypeError otherwise.
- **Node.js module resolution**: When running scripts from `/tmp`, `npm install` locally in `/tmp` or use `NODE_PATH=$(npm root -g)` to find globally installed packages like `pptxgenjs`, `sharp`, `react-icons`.
- **LibreOffice may not be installed** for PDF conversion. If `soffice` is unavailable, use `python -m markitdown output.pptx` for content QA instead of visual inspection.
