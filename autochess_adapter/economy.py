"""Live Cedar account/binding context. Never accept these identities from a client."""
from cedar_backend.errors import _McpError


FIELDS = {
    'profile': set(), 'catalog': set(), 'wheel': set(),
    'buy': {'cosmeticId', 'requestId'}, 'equip': {'cosmeticId', 'requestId'},
    'claim': {'giftId', 'requestId'}, 'gift': {'cosmeticId', 'note', 'requestId'},
    'subsidize': {'to', 'coins', 'requestId'}, 'wheel_action': {'command'},
}


def context(db_connect, account, slot):
    if not account:
        return None
    ident = int(account['id'])
    with db_connect() as conn:
        if account.get('is_ai'):
            ais = [ident]
        else:
            ais = [r[0] for r in conn.execute('SELECT ai_user_id FROM user_bindings WHERE human_user_id=?', (ident,))]
        people = {ident: {'playerId': str(ident), 'name': account['username'], 'actorType': 'ai' if account.get('is_ai') else 'human'}}
        for ai_id in ais:
            ai = conn.execute('SELECT id,username FROM toy_users WHERE id=? AND is_ai=1 AND deleted_at IS NULL', (ai_id,)).fetchone()
            if not ai:
                continue
            owners = conn.execute('''SELECT u.id,u.username FROM user_bindings b JOIN toy_users u ON u.id=b.human_user_id
                                    WHERE b.ai_user_id=? AND u.is_ai=0 AND u.deleted_at IS NULL''', (ai_id,)).fetchall()
            people[ai_id] = {'playerId': str(ai_id), 'name': ai['username'], 'actorType': 'ai', 'owners': [str(r['id']) for r in owners]}
            for row in owners:
                people[row['id']] = {'playerId': str(row['id']), 'name': row['username'], 'actorType': 'human'}
    suffix = '' if slot == 1 else ':' + str(slot)
    for person in people.values():
        person['playerId'] += suffix
        if 'owners' in person:
            person['owners'] = [p + suffix for p in person['owners']]
    return list(people.values())


def params(body):
    op = body.get('op')
    if op not in FIELDS:
        raise _McpError(-32602, '未知局外操作')
    return {'op': op, 'params': {key: body[key] for key in FIELDS[op] if key in body}}
