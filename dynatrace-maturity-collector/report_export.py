"""CSV and Excel exports using only Python's standard library."""
import csv
import json
import math
import re
import posixpath
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED
from xml.etree import ElementTree


def cell_value(value):
    if value is None:
        return "unknown"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def columns_for(rows, defaults=()):
    return list(dict.fromkeys([*defaults, *(key for row in rows for key in row)]))


def write_csv(path, rows, defaults=()):
    columns = columns_for(rows, defaults)
    with Path(path).open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, columns)
        writer.writeheader()
        for row in rows:
            values = {k: cell_value(row.get(k)) for k in columns}
            # Keep untrusted source text from becoming a spreadsheet formula.
            writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@')) else v for k, v in values.items()})


def write_xlsx(path, tables, report=None):
    """Write typed numeric cells and literal inline strings, never formulas."""
    tables=dict(tables)
    if report is not None:
        payload=json.dumps(report,ensure_ascii=False,separators=(',',':'))
        tables['Report Data']=([{'chunk':i//30000,'json':payload[i:i+30000]} for i in range(0,len(payload),30000)],['chunk','json'])
    def letter(index):
        result = ''
        while index:
            index, digit = divmod(index - 1, 26)
            result = chr(65 + digit) + result
        return result

    names = list(tables)
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    with ZipFile(path, 'w', ZIP_DEFLATED) as z:
        overrides = ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(names)+1))
        z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'+overrides+'</Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        sheets = ''.join(f'<sheet name="{escape(name[:31])}" sheetId="{i}" r:id="rId{i}"'+(' state="hidden"' if name=='Report Data' else '')+'/>' for i, name in enumerate(names, 1))
        z.writestr('xl/workbook.xml', f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{sheets}</sheets></workbook>')
        rels = ''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,len(names)+1))
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+rels+'</Relationships>')
        for i, name in enumerate(names, 1):
            rows, defaults = tables[name]
            cols = columns_for(rows, defaults)
            content = []
            for row_index, values in enumerate([cols, *[[cell_value(r.get(c)) for c in cols] for r in rows]], 1):
                cells = []
                for column, value in enumerate(values, 1):
                    ref = f'{letter(column)}{row_index}'
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                        cells.append(f'<c r="{ref}"><v>{value}</v></c>')
                    else:
                        clean = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))[:32767]
                        cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(clean)}</t></is></c>')
                content.append(f'<row r="{row_index}">'+''.join(cells)+'</row>')
            z.writestr(f'xl/worksheets/sheet{i}.xml', f'<worksheet xmlns="{ns}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" state="frozen"/></sheetView></sheetViews><sheetData>'+''.join(content)+f'</sheetData><autoFilter ref="A1:{letter(max(1,len(cols)))}{max(1,len(rows)+1)}"/></worksheet>')


def read_report(path):
    """Read a JSON snapshot or the lossless payload embedded in our workbook."""
    path=Path(path)
    if path.suffix.lower()!='.xlsx':return json.loads(path.read_text())
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with ZipFile(path) as z:
        sheets=ElementTree.fromstring(z.read('xl/workbook.xml')).find('s:sheets',ns)
        sheet=next((sheet for sheet in sheets if sheet.get('name')=='Report Data'),None)
        if sheet is None:raise ValueError('Workbook has no embedded report snapshot; use the original JSON or regenerate it with the current collector')
        relation_id=sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        relations=ElementTree.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        target=next(r.get('Target') for r in relations if r.get('Id')==relation_id)
        target=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
        root=ElementTree.fromstring(z.read(target))
        shared=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            shared=[''.join(item.itertext()) for item in ElementTree.fromstring(z.read('xl/sharedStrings.xml'))]
        def text(cell):
            value=''.join(cell.itertext())
            return shared[int(value)] if cell.get('t')=='s' else value
        chunks=[]
        for row in list(root.find('s:sheetData',ns))[1:]:
            cells=list(row)
            chunks.append((int(text(cells[0])),text(cells[1])))
        chunks.sort()
        if [i for i,_ in chunks]!=list(range(len(chunks))):raise ValueError('Embedded report chunks are missing or duplicated')
        return json.loads(''.join(text for _,text in chunks))
