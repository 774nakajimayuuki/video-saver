"""Fetch public NHK page scripts without a cookie jar or account credentials."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit, urljoin
import hashlib
import json
import re
import urllib.request

HERE = Path(__file__).resolve().parent / 'nhk-auth-trial'
PAGE = 'https://www.web.nhk/tv/an/hokkaidodo/pl/series-tep-2J211716Z5/ep/Q4YVTYHXB2'


def get(url, limit):
    parsed = urlsplit(url)
    assert parsed.scheme == 'https' and parsed.netloc == 'www.web.nhk'
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20) as response:
        assert urlsplit(response.url).netloc == 'www.web.nhk'
        data = response.read(limit + 1)
        assert len(data) <= limit
        return data


def main():
    HERE.mkdir(exist_ok=True)
    source = HERE / 'public-scripts'
    source.mkdir(exist_ok=True)
    html = get(PAGE, 4 * 1024 * 1024).decode('utf-8')
    urls = list(dict.fromkeys(urljoin(PAGE, u) for u in re.findall(r'<script[^>]+src=["\']([^"\']+)', html)))
    assert 0 < len(urls) <= 45
    assert all(urlsplit(u).path.startswith('/_next/static/chunks/') for u in urls)
    def save(url):
        target = source / Path(urlsplit(url).path).name
        data = target.read_bytes() if target.exists() else get(url, 3 * 1024 * 1024)
        if not target.exists():
            target.write_bytes(data)
        text = data.decode('utf-8')
        terms = ['widevine', 'licence', 'Authorization', 'authorization', 'Bearer', 'accessToken', 'withCredentials', 'credentials', 'FetchStream']
        return {'url': url, 'file': target.name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                'identifiers': {term: text.count(term) for term in terms if term in text}}
    with ThreadPoolExecutor(max_workers=4) as pool:
        files = list(pool.map(save, urls))
    result = {'scope': 'Unauthenticated public NHK static JavaScript; no account data sent', 'page': PAGE, 'files': files}
    (HERE / 'public-scripts-provenance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'scripts': len(files), 'bytes': sum(f['bytes'] for f in files), 'relevant': [f for f in files if f['identifiers']]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
