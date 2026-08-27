from __future__ import annotations

from dataclasses import dataclass
from html import escape

from jinja2 import Environment, select_autoescape
from markupsafe import Markup

_WEB_ENV = Environment(autoescape=select_autoescape(default=True))
_WEB_ENV.filters["html_text"] = lambda value: Markup(escape(str(value), quote=False))
_PAGE_TEMPLATE = _WEB_ENV.from_string(
    """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<style>{{ stylesheet|safe }}</style>
</head>
<body>
<main class="shell">
{{ body|safe }}
</main>
</body>
</html>"""
)
_SECTION_TEMPLATE = _WEB_ENV.from_string(
    """<section class="panel">
<h2>{{ title }}</h2>
{{ body|safe }}
</section>"""
)
_METRIC_CARD_TEMPLATE = _WEB_ENV.from_string(
    """<article class="card">
<span>{{ label }}</span>
<strong>{{ value }}</strong>
</article>"""
)
_TABLE_TEMPLATE = _WEB_ENV.from_string(
    '<div class="table-wrap"><table><thead><tr>'
    "{% for header in headers %}<th>{{ header }}</th>{% endfor %}"
    "</tr></thead><tbody>{{ rows|join|safe }}</tbody></table></div>"
)
_TABLE_ROW_TEMPLATE = _WEB_ENV.from_string(
    "<tr>"
    "{% for cell in cells %}"
    '<td{% if cell.colspan != 1 %} colspan="{{ cell.colspan }}"{% endif %}>'
    "{% if cell.raw %}{{ cell.value|safe }}{% else %}{{ cell.value|html_text }}{% endif %}"
    "</td>"
    "{% endfor %}"
    "</tr>"
)
_HERO_TEMPLATE = _WEB_ENV.from_string(
    '<section class="hero">\n'
    "<div>\n"
    "<p>{{ eyebrow|html_text }}</p>\n"
    "<h1>{{ title|html_text }}</h1>\n"
    "{% if detail %}<span>{{ detail|html_text }}</span>{% endif %}\n"
    "</div>\n"
    '<div class="status">\n'
    "{% for link in links %}"
    '<a class="pill link" href="{{ link.href }}">{{ link.label|html_text }}</a>'
    "{% endfor %}\n"
    "{% for pill in pills %}"
    '<span class="pill {{ pill.class_name }}">'
    "{{ pill.label|html_text }}: {{ pill.value|html_text }}</span>"
    "{% endfor %}\n"
    "</div>\n"
    "</section>"
)
_PILL_TEMPLATE = _WEB_ENV.from_string(
    '<span class="pill {{ class_name }}">{{ value|html_text }}</span>'
)
_SPAN_TEMPLATE = _WEB_ENV.from_string(
    '<span class="{{ class_name }}">{{ value|html_text }}</span>'
)
_LINK_TEMPLATE = _WEB_ENV.from_string(
    '<a href="{{ href }}">{{ label|html_text }}</a>'
)
_DETAIL_LIST_TEMPLATE = _WEB_ENV.from_string(
    '<dl class="details">'
    "{% for label, value in items %}"
    "<dt>{{ label|html_text }}</dt><dd>{{ value|html_text }}</dd>"
    "{% endfor %}"
    "</dl>"
)


@dataclass(frozen=True, slots=True)
class _HeroLink:
    label: str
    href: str


@dataclass(frozen=True, slots=True)
class _HeroPill:
    label: str
    value: object
    class_name: str = "ok"


@dataclass(frozen=True, slots=True)
class _TableCell:
    value: object
    colspan: int = 1
    raw: bool = False


def _page(title: str, sections: tuple[str, ...], *, stylesheet: str | None = None) -> str:
    return _PAGE_TEMPLATE.render(
        title=title,
        stylesheet=_stylesheet() if stylesheet is None else stylesheet,
        body="\n".join(sections),
    )


def _section(title: str, body: str) -> str:
    return _SECTION_TEMPLATE.render(title=title, body=body)


def _metric_card(label: str, value: object) -> str:
    return _METRIC_CARD_TEMPLATE.render(label=label, value=value)


def _table(headers: tuple[str, ...], rows: tuple[str, ...]) -> str:
    return _TABLE_TEMPLATE.render(headers=headers, rows=rows)


def _table_row(cells: tuple[object | _TableCell, ...]) -> str:
    return _TABLE_ROW_TEMPLATE.render(
        cells=tuple(cell if isinstance(cell, _TableCell) else _TableCell(cell) for cell in cells)
    )


def _raw_table_cell(value: str) -> _TableCell:
    return _TableCell(value, raw=True)


def _empty_table_row(message: str, *, colspan: int) -> str:
    return _table_row((_TableCell(message, colspan=colspan),))


def _pill(value: object, *, class_name: str = "ok") -> str:
    return _PILL_TEMPLATE.render(value=value, class_name=class_name)


def _span(value: object, *, class_name: str) -> str:
    return _SPAN_TEMPLATE.render(value=value, class_name=class_name)


def _link(label: object, href: str) -> str:
    return _LINK_TEMPLATE.render(label=label, href=href)


def _detail_list(items: tuple[tuple[object, object], ...]) -> str:
    return _DETAIL_LIST_TEMPLATE.render(items=items)


def _hero_block(
    title: str,
    *,
    detail: str = "",
    links: tuple[_HeroLink, ...] = (),
    pills: tuple[_HeroPill, ...] = (),
    eyebrow: str = "Universal Agent Runtime",
) -> str:
    return _HERO_TEMPLATE.render(
        eyebrow=eyebrow,
        title=title,
        detail=detail,
        links=links,
        pills=pills,
    )


def _status_class(status: str) -> str:
    if status == "error":
        return "error"
    if status == "warn":
        return "warn"
    return "ok"


def _stylesheet() -> str:
    return """
:root {
  color-scheme: light;
  font-family:
    Inter,
    ui-sans-serif,
    system-ui,
    -apple-system,
    BlinkMacSystemFont,
    "Segoe UI",
    sans-serif;
  background: #f5f7fb;
  color: #172033;
}
* {
  box-sizing: border-box;
}
body {
  margin: 0;
}
.shell {
  width: min(1180px, calc(100vw - 40px));
  margin: 0 auto;
  padding: 28px 0 40px;
}
.hero, .panel, .card {
  background: #ffffff;
  border: 1px solid #d9e1ec;
  border-radius: 8px;
  box-shadow: 0 1px 2px rgba(23, 32, 51, 0.05);
}
.hero {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  align-items: flex-start;
  padding: 24px;
  border-top: 4px solid #0f766e;
}
.hero p, .hero h1 {
  margin: 0;
}
.hero p {
  color: #5d6b82;
  font-size: 13px;
  font-weight: 700;
  text-transform: uppercase;
}
.hero h1 {
  margin-top: 4px;
  font-size: 30px;
  line-height: 1.1;
}
.hero span {
  display: inline-block;
  margin-top: 10px;
  color: #5d6b82;
  font-size: 14px;
}
.status {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}
.pill {
  border-radius: 999px;
  padding: 7px 10px;
  font-size: 13px;
  font-weight: 700;
}
.severity {
  display: inline-block;
  min-width: 56px;
  border-radius: 999px;
  padding: 4px 8px;
  text-align: center;
  font-size: 12px;
  font-weight: 700;
}
.ok {
  background: #dcfce7;
  color: #166534;
}
.warn {
  background: #fff7ed;
  color: #9a3412;
}
.error {
  background: #fee2e2;
  color: #991b1b;
}
.grid {
  display: grid;
  gap: 12px;
}
.cards {
  grid-template-columns: repeat(6, minmax(0, 1fr));
  margin: 16px 0;
}
.card {
  min-height: 82px;
  padding: 16px;
}
.card span {
  display: block;
  color: #5d6b82;
  font-size: 13px;
}
.card strong {
  display: block;
  margin-top: 8px;
  font-size: 24px;
}
.panel {
  margin-top: 16px;
  padding: 20px;
}
.panel h2 {
  margin: 0 0 14px;
  font-size: 18px;
}
.table-wrap {
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
}
th, td {
  border-bottom: 1px solid #e6ebf2;
  padding: 10px 8px;
  text-align: left;
  vertical-align: top;
  font-size: 13px;
}
th {
  color: #5d6b82;
  font-size: 12px;
  text-transform: uppercase;
}
a {
  color: #0f766e;
  font-weight: 700;
  text-decoration: none;
}
.link {
  background: #ecfeff;
  color: #0f766e;
}
.details {
  display: grid;
  grid-template-columns: 170px minmax(0, 1fr);
  gap: 10px 14px;
  margin: 0;
}
.details dt {
  color: #5d6b82;
  font-weight: 700;
}
.details dd {
  margin: 0;
}
.empty {
  color: #5d6b82;
  margin: 0;
}
@media (max-width: 860px) {
  .shell {
    width: min(100vw - 24px, 1180px);
    padding-top: 16px;
  }
  .hero {
    display: block;
  }
  .status {
    justify-content: flex-start;
    margin-top: 16px;
  }
  .cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .details {
    grid-template-columns: 1fr;
  }
}
""".strip()
