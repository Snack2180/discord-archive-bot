def authorize(user_id: str, guild_id: str, owner_id: str, expected_guild: str):
    if str(user_id) != owner_id or str(guild_id) != expected_guild:
        raise PermissionError('소유자만 사용할 수 있습니다')


def approve(store, item_id: str, channel_id: str, tags: list[str], allowed: list[str]) -> bool:
    if channel_id not in allowed:
        raise ValueError('허용되지 않은 목적지')
    job = store.job(item_id)
    if not job or job['state'] != 'review_pending':
        return False
    if any(issue.startswith(('ALT:', 'ALT ')) for issue in store.get_item(item_id).issues):
        raise ValueError('ALT 전사문 수집이 완료되지 않았습니다. 재수집하거나 TXT를 별도 첨부해주세요')
    # Publication uncertainty must be explicitly reconciled; a second approve is not a retry.
    if job['receipt'] or job['error'].startswith('게시') or job['error'].startswith('보관'):
        raise ValueError('이미 게시됐을 수 있습니다. 목적지 확인 후 /재시도를 사용하세요')
    store.record_feedback(item_id, channel_id, tags)
    store.update_job(item_id, destination=channel_id, state='ready', error='')
    return True


def persistent_reviews(store) -> list[tuple[str, str]]:
    return [(job['id'], job['review_message']) for job in store.jobs()
            if job['review_message'] and job['state'] != 'done']
