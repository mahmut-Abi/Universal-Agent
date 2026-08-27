from __future__ import annotations

from html import escape

from jinja2 import Environment, select_autoescape

_WEB_ENV = Environment(autoescape=select_autoescape(default=True))
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


def _page(title: str, sections: tuple[str, ...], *, stylesheet: str | None = None) -> str:
    return _PAGE_TEMPLATE.render(
        title=title,
        stylesheet=_stylesheet() if stylesheet is None else stylesheet,
        body="\n".join(sections),
    )


def _section(title: str, body: str) -> str:
    return "\n".join(
        (
            '<section class="panel">',
            f"<h2>{_html(title)}</h2>",
            body,
            "</section>",
        )
    )


def _metric_card(label: str, value: object) -> str:
    return "\n".join(
        (
            '<article class="card">',
            f"<span>{_html(label)}</span>",
            f"<strong>{_html(value)}</strong>",
            "</article>",
        )
    )


def _table(headers: tuple[str, ...], rows: tuple[str, ...]) -> str:
    header = "".join(f"<th>{_html(item)}</th>" for item in headers)
    return (
        '<div class="table-wrap"><table><thead><tr>'
        + header
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _html(value: object) -> str:
    return escape(str(value), quote=False)


def _attr(value: object) -> str:
    return escape(str(value), quote=True)


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
