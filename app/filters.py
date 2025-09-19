from flask import url_for

def imgsrc(path: str | None) -> str:
    if not path:
        return url_for('static', filename='img/placeholder.png')
    p = str(path).strip()
    if p.startswith(('http://', 'https://', '//')):
        return p
    return url_for('static', filename=p.lstrip('/'))
