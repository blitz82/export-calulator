"""Safe, descriptive attachment names with ASCII and RFC 5987 fallbacks."""
import re
from urllib.parse import quote

def attachment(filename, fallback):
    safe=re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', filename).strip(' .')[:180]
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(safe)}'
