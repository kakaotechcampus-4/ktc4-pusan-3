# MkDocs 훅 — 사이트에는 docs/ 만 올라가는데, 문서는 저장소 상대 경로로 코드 · CONTRIBUTING.md · .github/ 를 가리킨다.
# ① docs/ 밖이나 사이트에서 뺀 파일(README.md · .txt)을 가리키는 상대 링크 · 이미지를 GitHub develop 주소로 바꾼다.
#    ../../docs/x.md 처럼 docs/ 를 나갔다 들어오는 링크는 docs 안 상대 경로로 고친다.
# ② 문단 바로 아래 붙은 목록 · 표 앞에 빈 줄을 넣는다 — GitHub 은 목록 · 표로 보여 주지만 Python-Markdown 은 글자로 붙인다.
# 빌드할 때만 바꾸고 문서 원문은 고치지 않는다. 코드 블록(``` 펜스 · 인라인 `code`) 안은 건드리지 않는다.
import logging
import os
import posixpath
import re
from urllib.parse import quote, unquote

log = logging.getLogger("mkdocs.hooks.docs_hooks")
BASE = "https://github.com/kakaotechcampus-4/ktc4-pusan-3"
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
LIST = re.compile(r"^([-*+]|1[.)]) ")  # GitHub 에서 문단을 끊고 시작하는 목록
TABLE_SEP = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
NOT_PARA = re.compile(r"^(\s|#|>|\||<|[-*+] |\d+[.)] )")  # 이런 줄 뒤에는 빈 줄을 넣지 않는다
TOKEN = re.compile(  # 인라인 코드를 먼저 잡아 그대로 두고, [글](대상) · ![글](대상) 의 대상만 본다
    r"(?P<code>(`+).+?\2)|(?P<head>!?\[(?:[^\[\]]|\[[^\[\]]*\])*\]\()(?P<url><[^>]+>|[^)\s]+)"
)
COUNT = [0, 0]  # GitHub 주소로 바꾼 것 · docs 안 상대 경로로 고친 것


def _github(url, page, files, root):
    raw = url[1:-1] if url.startswith("<") else url
    rest, hash_, frag = raw.partition("#")
    path, q, query = rest.partition("?")
    if not path or re.match(r"[a-zA-Z][\w+.-]*:|/", path):
        return None  # http(s) · mailto · #앵커만 · 절대 경로는 그대로
    page_dir = posixpath.dirname(page.file.src_uri)
    target = posixpath.normpath(posixpath.join("docs", page_dir, unquote(path)))
    disk = os.path.join(root, target)
    if target.startswith("docs/"):
        f = files.get_file_from_path(target[len("docs/"):])
        if f is not None and not f.inclusion.is_excluded():
            if not posixpath.normpath(posixpath.join(page_dir, unquote(path))).startswith(".."):
                return None  # 사이트에 있는 파일
            # ../../docs/api/x.md 처럼 docs/ 를 나갔다 다시 들어오는 링크 — MkDocs 는 docs/ 밖으로 본다
            return quote(posixpath.relpath(target[len("docs/"):], page_dir or ".")) + q + query + hash_ + frag
        if not os.path.exists(disk):
            return None  # 없는 문서 — MkDocs 가 깨진 링크로 알린다
        # 여기 남은 것: 뺀 파일(README.md 는 files 에서 아예 빠진다 · .txt 는 EXCLUDED) · 폴더
    elif target.startswith(".."):
        return None  # 저장소 밖 — 그대로 두고 MkDocs 경고로 드러낸다
    if not os.path.exists(disk):
        log.info("GitHub 링크의 대상이 저장소에 없다: %s (%s)", target, page.file.src_uri)
    kind = "tree" if os.path.isdir(disk) else "blob"
    return f"{BASE}/{kind}/develop/{quote(target)}{q}{query}{hash_}{frag}"


def _swap(t, page, files, root):
    new = None if t.group("code") else _github(t.group("url"), page, files, root)
    if new is None:
        return t.group(0)
    COUNT[0 if new.startswith(BASE) else 1] += 1
    return t.group("head") + new


def on_pre_build(config):
    COUNT[:] = [0, 0]


def on_page_markdown(markdown, page, config, files):
    root = os.path.dirname(config.config_file_path)
    lines, out, fence = markdown.split("\n"), [], None
    for i, line in enumerate(lines):
        m = FENCE.match(line)
        if fence:  # 여는 펜스와 같은 글자로 같은 길이 이상이면 닫힌다
            s = line.strip()
            if m and s[0] == fence[0] and len(s) >= len(fence) and not s.strip(fence[0]):
                fence = None
        elif m:
            fence = m.group(1)
        else:
            prev = out[-1] if out else ""
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            starts = LIST.match(line) or (line.startswith("|") and TABLE_SEP.match(nxt))
            if starts and prev.strip() and not NOT_PARA.match(prev):
                out.append("")
            line = TOKEN.sub(lambda t: _swap(t, page, files, root), line)
        out.append(line)
    return "\n".join(out)


def on_post_build(config):
    log.info("GitHub 주소로 바꾼 링크 %d개 · docs 안 경로로 고친 링크 %d개", *COUNT)
