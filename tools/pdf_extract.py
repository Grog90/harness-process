import re, sys, zlib, base64

path = sys.argv[1]
data = open(path, 'rb').read()

objs = {}
for m in re.finditer(rb'(\d+)\s+0\s+obj(.*?)endobj', data, re.S):
    objs[int(m.group(1))] = m.group(2)

def decode_stream(body):
    m = re.search(rb'stream\r?\n', body)
    if not m:
        return None
    head = body[:m.start()]
    start = m.end()
    end = body.find(b'endstream', start)
    raw = body[start:end]
    fm = re.search(rb'/Filter\s*\[(.*?)\]', head, re.S)
    if fm:
        filters = re.findall(rb'/(\w+)', fm.group(1))
    else:
        fm = re.search(rb'/Filter\s*/([A-Za-z0-9]+)', head)
        filters = [fm.group(1)] if fm else []
    for f in filters:
        if f in (b'ASCII85Decode', b'A85'):
            s = raw.strip()
            if s.startswith(b'<~'):
                s = s[2:]
            if s.endswith(b'~>'):
                s = s[:-2]
            s = re.sub(rb'\s', b'', s)
            try:
                raw = base64.a85decode(s)
            except Exception:
                return None
        elif f in (b'FlateDecode', b'Fl'):
            try:
                raw = zlib.decompress(raw)
            except Exception:
                try:
                    raw = zlib.decompressobj().decompress(raw)
                except Exception:
                    return None
    return raw

def parse_cmap(raw):
    txt = raw.decode('latin-1')
    mp = {}
    for blk in re.finditer(r'beginbfchar(.*?)endbfchar', txt, re.S):
        for a, b in re.findall(r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', blk.group(1)):
            mp[int(a, 16)] = ''.join(chr(int(b[i:i+4], 16)) for i in range(0, len(b), 4))
    for blk in re.finditer(r'beginbfrange(.*?)endbfrange', txt, re.S):
        for a, b, c in re.findall(r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', blk.group(1)):
            lo, hi, start = int(a, 16), int(b, 16), int(c, 16)
            for i in range(hi - lo + 1):
                mp[lo + i] = chr(start + i)
    return mp

font_cache = {}
def font_cmap(fobj):
    if fobj in font_cache:
        return font_cache[fobj]
    cm = {}
    body = objs.get(fobj)
    if body:
        tu = re.search(rb'/ToUnicode\s*(\d+)\s+0\s+R', body)
        if tu:
            st = decode_stream(objs[int(tu.group(1))])
            if st:
                cm = parse_cmap(st)
    font_cache[fobj] = cm
    return cm

def resource_fonts(page_body):
    m = re.search(rb'/Resources\s*<<(.*?)>>\s*/', page_body, re.S)
    res = None
    if m:
        res = m.group(1)
    else:
        m = re.search(rb'/Resources\s+(\d+)\s+0\s+R', page_body)
        if m:
            res = objs.get(int(m.group(1)), b'')
    if not res:
        return {}
    fm = re.search(rb'/Font\s*<<(.*?)>>', res, re.S)
    body = None
    if fm:
        body = fm.group(1)
    else:
        fm = re.search(rb'/Font\s+(\d+)\s+0\s+R', res)
        if fm:
            body = objs.get(int(fm.group(1)), b'')
    if not body:
        return {}
    out = {}
    for nm, num in re.findall(rb'/([A-Za-z0-9+]+)\s+(\d+)\s+0\s+R', body):
        out[nm.decode('latin-1')] = font_cmap(int(num))
    return out

def unescape(s):
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == '\\' and i + 1 < len(s):
            n = s[i+1]
            mp = {'n':'\n','r':'\r','t':'\t','b':'\b','f':'\f','(':'(',')':')','\\':'\\'}
            if n in mp:
                out.append(mp[n]); i += 2; continue
            if n in '01234567':
                mm = re.match(r'[0-7]{1,3}', s[i+1:i+4])
                out.append(chr(int(mm.group(0), 8))); i += 1 + len(mm.group(0)); continue
            if n == '\n':
                i += 2; continue
            out.append(n); i += 2; continue
        out.append(c); i += 1
    return ''.join(out)

def decode_str(b, cmap):
    s = unescape(b.decode('latin-1'))
    if not cmap:
        return s
    return ''.join(cmap.get(ord(ch), ch) for ch in s)

def extract(content, rfonts):
    out = []
    cur = {}
    pat = re.compile(rb'/([A-Za-z0-9+]+)\s+[\d.]+\s+Tf|\((?:\\.|[^\\()])*\)\s*Tj|\[(?:[^\[\]]|\\.)*\]\s*TJ|T\*|TD|Td|ET', re.S)
    for m in pat.finditer(content):
        tok = m.group(0)
        if tok.endswith(b'Tf'):
            cur = rfonts.get(m.group(1).decode('latin-1'), {})
        elif tok.endswith(b'Tj'):
            s = re.match(rb'\((.*)\)\s*Tj', tok, re.S).group(1)
            out.append(decode_str(s, cur))
        elif tok.endswith(b'TJ'):
            arr = re.match(rb'\[(.*)\]\s*TJ', tok, re.S).group(1)
            parts = []
            for mm in re.finditer(rb'\((?:\\.|[^\\()])*\)|-?\d+\.?\d*', arr, re.S):
                t = mm.group(0)
                if t.startswith(b'('):
                    parts.append(decode_str(t[1:-1], cur))
                else:
                    try:
                        if float(t) <= -150:
                            parts.append(' ')
                    except Exception:
                        pass
            out.append(''.join(parts))
        elif tok in (b'T*', b'TD', b'Td', b'ET'):
            out.append('\n')
    return ''.join(out)

pages = []
for num, body in objs.items():
    norm = re.sub(rb'\s', b'', body)
    if b'/Type/Page' in norm and b'/Type/Pages' not in norm:
        cm = re.search(rb'/Contents\s*(\d+)\s+0\s+R', body)
        if cm:
            pages.append((num, int(cm.group(1)), body))
pages.sort(key=lambda t: t[0])

res = []
for i, (pnum, cnum, body) in enumerate(pages, 1):
    st = decode_stream(objs[cnum])
    res.append('\n\n===== PAGE %d =====\n' % i)
    if st is None:
        continue
    res.append(extract(st, resource_fonts(body)))

text = ''.join(res)
text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
text = re.sub(r'\n{3,}', '\n\n', text)
text = re.sub(r'[ \t]+\n', '\n', text)
open(sys.argv[2], 'w', encoding='utf-8').write(text)
sys.stderr.write('pages: %d chars: %d\n' % (len(pages), len(text)))
