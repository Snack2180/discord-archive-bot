import re


def search_results(store, query='', tags=None, page=1):
    page = max(1, page)
    items = store.search(query, tags or [], limit=5, offset=(page-1)*5)
    if not items:
        return '검색 결과가 없습니다.'
    lines = [f'검색 결과 · {page}페이지']
    for item in items:
        title = re.sub(r'[@`*<>\[\]]', '', item.title or item.body[:60] or '파일 자료')[:80]
        snippet = re.sub(r'[@`*<>\[\]]', '', item.text().replace('\n', ' '))[:130]
        url = item.archive_url or item.source_url
        lines.append(f'**{title}** · 자료 ID `{item.id}`\n{snippet}\n태그: {", ".join(item.tags)[:120]}\n{url}')
    return '\n\n'.join(lines)[:1950]
