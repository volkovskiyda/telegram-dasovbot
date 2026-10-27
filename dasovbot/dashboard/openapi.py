"""Hand-written OpenAPI 3.1 description of every `/api` route, served at `/api/openapi.json`.

`tests/test_openapi.py` keeps it honest: every `/api` route the app registers
must be documented here and vice versa. The README stays the narrative; the
descriptions here are one sentence each.
"""
from __future__ import annotations

from aiohttp import web

ADMIN = [{'adminToken': []}]
READ_ONLY = [{'adminToken': []}, {'apiToken': []}]


def _ref(name: str) -> dict:
    return {'$ref': f'#/components/schemas/{name}'}


def _error(description: str) -> dict:
    return {'description': description, 'content': {'application/json': {'schema': _ref('Error')}}}


def _ok(schema: dict, description: str = 'OK') -> dict:
    return {'description': description, 'content': {'application/json': {'schema': schema}}}


def _array(schema: dict) -> dict:
    return {'type': 'array', 'items': schema}


def _query(name: str, description: str, schema: dict | None = None, required: bool = False) -> dict:
    return {'name': name, 'in': 'query', 'required': required, 'description': description,
            'schema': schema or {'type': 'string'}}


def _body(properties: dict, required: list[str] | None = None) -> dict:
    schema = {'type': 'object', 'properties': properties}
    if required:
        schema['required'] = required
    return {'required': bool(required), 'content': {'application/json': {'schema': schema}}}


_NULLABLE_STRING = {'type': ['string', 'null']}
_NULLABLE_INT = {'type': ['integer', 'null']}
_BOOL_QUERY = {'type': 'string', 'enum': ['true', 'false']}

_CHAT_ID = {'type': ['string', 'integer'],
            'description': 'Numeric chat id or @username; defaults to DEVELOPER_CHAT_ID.'}
_DRY_RUN = {'type': 'boolean', 'description': 'Resolve everything and report, write nothing.'}
_FORCE = {'type': 'boolean', 'description': 'Accept a numeric chat_id the bot has never seen.'}

UNAUTHORIZED = _error('Missing or wrong bearer token.')
PASSIVE = _error('This node is passive; `active` carries the peer URL to retry against.')

SCHEMAS = {
    'Error': {
        'type': 'object',
        'required': ['error'],
        'properties': {
            'error': {'type': 'string'},
            'detail': {'type': 'string'},
            'candidates': {**_array(_ref('User')), 'description': 'Closest known users, on unknown/ambiguous ids.'},
            'active': {**_NULLABLE_STRING, 'description': 'The active node URL, on a passive-node 409.'},
            'playlists': {**_array(_ref('Playlist')), 'description': 'The choices, on an ambiguous subscribe url.'},
        },
    },
    'User': {
        'type': 'object',
        'properties': {
            'id': {'type': 'string'},
            'name': {'type': 'string'},
            'username': _NULLABLE_STRING,
            'banned': {'type': 'boolean'},
            'banned_at': _NULLABLE_STRING,
            'requests': {'type': 'integer'},
            'last_at': _NULLABLE_STRING,
        },
    },
    'Subscriber': {
        'type': 'object',
        'properties': {'id': {'type': 'string'}, 'name': {'type': 'string'}},
    },
    'Subscription': {
        'type': 'object',
        'properties': {
            'url': {'type': 'string', 'description': 'The stored key.'},
            'title': {'type': 'string'},
            'uploader': {'type': 'string'},
            'uploader_videos': {'type': 'string'},
            'chat_ids': _array(_ref('Subscriber')),
        },
    },
    'SubscriptionWrite': {
        'type': 'object',
        'description': 'A subscription as a write reports it (plain chat ids).',
        'properties': {
            'url': {'type': 'string'},
            'title': {'type': 'string'},
            'uploader': {'type': 'string'},
            'uploader_videos': {'type': 'string'},
            'chat_ids': _array({'type': 'string'}),
        },
    },
    'Intent': {
        'type': 'object',
        'properties': {
            'url': {'type': 'string'},
            'status': {'type': 'string', 'enum': ['pending', 'retrying', 'ignored']},
            'priority': {'type': 'integer'},
            'retries': {'type': 'integer'},
            'source': _NULLABLE_STRING,
            'title': _NULLABLE_STRING,
            'upload_date': _NULLABLE_STRING,
            'chat_ids': _array({'type': 'string'}),
            'inline_count': {'type': 'integer'},
            'message_count': {'type': 'integer'},
            'retry_in_sec': {'type': 'integer'},
        },
    },
    'IntentLookup': {
        'type': 'object',
        'properties': {
            'url': {'type': 'string'},
            'status': {'type': 'string', 'enum': ['pending', 'retrying', 'ignored', 'delivered', 'absent']},
            'intent': {'oneOf': [_ref('Intent'), {'type': 'null'}]},
        },
    },
    'Alert': {
        'type': 'object',
        'properties': {
            'id': {'type': 'string'},
            'level': {'type': 'string'},
            'message': {'type': 'string'},
            'since': _NULLABLE_STRING,
        },
    },
    'Vpn': {
        'type': 'object',
        'properties': {
            'ip': _NULLABLE_STRING,
            'country': _NULLABLE_STRING,
            'city': _NULLABLE_STRING,
            'status': _NULLABLE_STRING,
            'last_change_at': _NULLABLE_STRING,
            'changes_since_start': {'type': 'integer'},
            'last_poll_ok': {'type': ['boolean', 'null']},
            'last_poll_at': _NULLABLE_STRING,
        },
    },
    'Status': {
        'type': 'object',
        'description': 'The /health fields plus counts, task last-runs, backup age and the VPN block.',
        'properties': {
            'role': {'type': 'string', 'enum': ['active', 'passive', 'draining']},
            'node': {'type': 'string'},
            'lease_holder': _NULLABLE_STRING,
            'last_sync_rev': _NULLABLE_INT,
            'last_sync_at': _NULLABLE_STRING,
            'ready': {'type': 'boolean'},
            'peer': _NULLABLE_STRING,
            'peer_url': {'type': 'string'},
            'node_role': {'type': 'string'},
            'enabled': {'type': 'boolean'},
            'rev': {'type': 'integer'},
            'manual_hold': {'type': 'boolean'},
            'handback_requested': {'type': 'boolean'},
            'drained': {'type': 'boolean'},
            'counts': {
                'type': 'object',
                'properties': {
                    'users': {'type': 'integer'},
                    'banned': {'type': 'integer'},
                    'videos': {'type': 'integer', 'description': 'Delivered videos, deduplicated as /api/videos serves them.'},
                    'videos_rows': {'type': 'integer'},
                    'subscriptions': {'type': 'integer'},
                    'subscribers': {'type': 'integer'},
                    'intents': {
                        'type': 'object',
                        'properties': {name: {'type': 'integer'} for name in ('pending', 'retrying', 'ignored', 'total')},
                    },
                    'inline_queries': {'type': 'integer'},
                    'alerts': {'type': 'integer'},
                },
            },
            'tasks': {'type': 'object', 'additionalProperties': _NULLABLE_STRING,
                      'description': 'Background task name to its last run, null when it never ran.'},
            'backup': {'oneOf': [
                {'type': 'object', 'properties': {'dir': {'type': 'string'}, 'newest_age_sec': _NULLABLE_INT}},
                {'type': 'null'},
            ]},
            'migration': {'type': 'object'},
            'queue_size': {'type': 'integer'},
            'vpn': {'oneOf': [_ref('Vpn'), {'type': 'null'}]},
            'node_time': {'type': 'string'},
        },
    },
    'Playlist': {
        'type': 'object',
        'properties': {'title': {'type': 'string'}, 'url': {'type': 'string'}},
    },
    'PlaylistDiscovery': {
        'type': 'object',
        'properties': {
            'uploader': _NULLABLE_STRING,
            'uploader_url': {'type': 'string'},
            'uploader_videos': {'type': 'string'},
            'playlists': _array(_ref('Playlist')),
            'direct': {'type': 'boolean', 'description': 'The url itself is one of the playlists.'},
            'fallback_videos': {'type': 'boolean', 'description': 'No playlists tab; only the Videos feed.'},
        },
    },
    'Video': {
        'type': 'object',
        'description': 'A library-index entry (camelCase, as a sidecar index spells it).',
        'properties': {
            'id': {'type': 'string'},
            'title': {'type': 'string'},
            'channel': _NULLABLE_STRING,
            'channelId': _NULLABLE_STRING,
            'duration': {'type': 'integer'},
            'uploadDate': _NULLABLE_STRING,
            'uploadTimestamp': _NULLABLE_INT,
            'tags': _array({'type': 'string'}),
            'categories': _array({'type': 'string'}),
            'description': {'type': 'string'},
            'thumbnail': _NULLABLE_STRING,
            'chapters': {'type': ['array', 'null'],
                         'items': {'type': 'object', 'properties': {'start': {'type': 'number'}, 'title': {'type': 'string'}}}},
            'fetchedAt': _NULLABLE_INT,
            'webpageUrl': _NULLABLE_STRING,
            'exported': {'type': 'boolean'},
        },
    },
    'BanResult': {
        'type': 'object',
        'properties': {
            'dry_run': {'type': 'boolean'},
            'action': {'type': 'string', 'enum': ['ban', 'unban']},
            'user': _ref('User'),
            'changed': {'type': 'boolean', 'description': 'Absent on a dry run.'},
        },
    },
    'SubscribeResult': {
        'type': 'object',
        'properties': {
            'dry_run': {'type': 'boolean'},
            'status': {'type': 'string', 'enum': ['already_subscribed'], 'description': 'Only when nothing was to do.'},
            'action': {'type': 'string', 'enum': ['subscribe', 'add_subscriber']},
            'url': {'type': 'string', 'description': 'The stored key; a channel URL resolves to its Videos feed.'},
            'subscription': _ref('SubscriptionWrite'),
            'populate': {'type': 'string', 'enum': ['started', 'skipped']},
        },
    },
    'UnsubscribeResult': {
        'type': 'object',
        'properties': {
            'dry_run': {'type': 'boolean'},
            'action': {'type': 'string', 'enum': ['remove_subscriber']},
            'url': {'type': 'string'},
            'chat_id': {'type': 'string'},
            'last_subscriber': {'type': 'boolean', 'description': 'Dry run only.'},
            'removed_subscription': {'type': 'boolean', 'description': 'Write only.'},
        },
    },
    'SendResult': {
        'type': 'object',
        'properties': {
            'dry_run': {'type': 'boolean'},
            'action': {'type': 'string', 'enum': ['queue']},
            'url': {'type': 'string'},
            'chat_id': {'type': 'string'},
            'cached': {'type': 'boolean', 'description': 'A file_id exists, delivery needs no download.'},
            'existing_intent': {'type': 'boolean', 'description': 'Dry run only.'},
            'intent': _ref('Intent'),
        },
    },
    'PopulateResult': {
        'type': 'object',
        'properties': {
            'status': {'type': 'string', 'enum': ['started', 'already_running']},
            'scope': {'type': 'string', 'description': '"all" or the subscription key.'},
        },
    },
}

PATHS = {
    '/api/openapi.json': {
        'get': {
            'summary': 'This document.',
            'security': READ_ONLY,
            'responses': {'200': _ok({'type': 'object'}), '401': UNAUTHORIZED},
        },
    },
    '/api/videos': {
        'get': {
            'summary': 'Every delivered video, deduplicated by YouTube id, newest upload first.',
            'security': READ_ONLY,
            'parameters': [_query('exported', 'Only videos moved (or not) to the export folder.', _BOOL_QUERY)],
            'responses': {'200': _ok(_array(_ref('Video'))), '304': {'description': 'ETag matched If-None-Match.'},
                          '401': UNAUTHORIZED},
        },
    },
    '/api/videos/{video_id}': {
        'get': {
            'summary': 'One video by YouTube id.',
            'security': READ_ONLY,
            'parameters': [{'name': 'video_id', 'in': 'path', 'required': True, 'schema': {'type': 'string'}}],
            'responses': {'200': _ok(_ref('Video')), '401': UNAUTHORIZED, '404': _error('Unknown id.')},
        },
    },
    '/api/status': {
        'get': {
            'summary': 'Health, counts, task last-runs, backup age and VPN exit of this node.',
            'security': ADMIN,
            'responses': {'200': _ok(_ref('Status')), '401': UNAUTHORIZED},
        },
    },
    '/api/users': {
        'get': {
            'summary': 'Known users, most requests first.',
            'security': ADMIN,
            'parameters': [
                _query('search', 'Case-insensitive match on id, name or username (with or without @).'),
                _query('banned', 'Only banned (true) or not banned (false) users.', _BOOL_QUERY),
            ],
            'responses': {'200': _ok(_array(_ref('User'))), '401': UNAUTHORIZED},
        },
    },
    '/api/users/{user_id}/ban': {
        'post': {
            'summary': 'Ban a user; posts an audit line to the developer chat.',
            'security': ADMIN,
            'parameters': [{'name': 'user_id', 'in': 'path', 'required': True, 'schema': {'type': 'string'},
                            'description': 'Numeric id (never-seen ids are allowed) or @username.'}],
            'requestBody': _body({'dry_run': _DRY_RUN}),
            'responses': {'200': _ok(_ref('BanResult')), '400': _error('Not an id or @username, or a bad body.'),
                          '401': UNAUTHORIZED, '404': _error('Unknown @username; `candidates` lists near misses.'),
                          '409': _error('Ambiguous @username (`candidates`), or passive node (`active`).')},
        },
    },
    '/api/users/{user_id}/unban': {
        'post': {
            'summary': 'Unban a user; posts an audit line to the developer chat.',
            'security': ADMIN,
            'parameters': [{'name': 'user_id', 'in': 'path', 'required': True, 'schema': {'type': 'string'},
                            'description': 'Numeric id or @username.'}],
            'requestBody': _body({'dry_run': _DRY_RUN}),
            'responses': {'200': _ok(_ref('BanResult')), '400': _error('Not an id or @username, or a bad body.'),
                          '401': UNAUTHORIZED, '404': _error('Unknown @username; `candidates` lists near misses.'),
                          '409': _error('Ambiguous @username (`candidates`), or passive node (`active`).')},
        },
    },
    '/api/subscriptions': {
        'get': {
            'summary': 'Subscriptions with their subscribers, by title.',
            'security': ADMIN,
            'parameters': [_query('chat_id', 'Only subscriptions this chat is subscribed to.')],
            'responses': {'200': _ok(_array(_ref('Subscription'))), '401': UNAUTHORIZED},
        },
        'post': {
            'summary': 'Subscribe a chat to a channel feed or playlist (no audit line).',
            'security': ADMIN,
            'requestBody': _body({
                'url': {'type': 'string', 'description': 'Channel (its Videos feed), one of its feeds/playlists, or any playlist URL.'},
                'chat_id': _CHAT_ID,
                'populate': {'type': 'boolean', 'description': 'Queue the feed\'s latest videos for that chat now.'},
                'dry_run': _DRY_RUN,
                'force': _FORCE,
            }, required=['url']),
            'responses': {'200': _ok(_ref('SubscribeResult')), '400': _error('Bad url, body or chat_id.'),
                          '401': UNAUTHORIZED, '404': _error('Unknown chat_id (`candidates`); pass force.'),
                          '409': _error('A video URL (`playlists` lists the choices), ambiguous @username, or passive node.'),
                          '502': _error('yt-dlp extraction failed.')},
        },
    },
    '/api/subscriptions/remove': {
        'post': {
            'summary': 'Remove one chat from a subscription; posts an audit line.',
            'security': ADMIN,
            'requestBody': _body({
                'url': {'type': 'string', 'description': 'A stored subscription key.'},
                'chat_id': _CHAT_ID,
                'dry_run': _DRY_RUN,
                'force': _FORCE,
            }, required=['url']),
            'responses': {'200': _ok(_ref('UnsubscribeResult')), '400': _error('Bad body or chat_id.'),
                          '401': UNAUTHORIZED, '404': _error('Not a subscription, or the chat is not a subscriber.'),
                          '409': PASSIVE},
        },
    },
    '/api/intents': {
        'get': {
            'summary': 'Queued downloads by priority, or one URL\'s delivery state.',
            'security': ADMIN,
            'parameters': [
                _query('ignored', 'Only ignored (true) or live (false) intents.', _BOOL_QUERY),
                _query('url', 'Look one URL up instead: pending/retrying/ignored while queued, delivered or absent otherwise.'),
            ],
            'responses': {'200': _ok({'oneOf': [_array(_ref('Intent')), _ref('IntentLookup')]}), '401': UNAUTHORIZED},
        },
        'post': {
            'summary': 'Queue a download for a chat through the intent system (no audit line).',
            'security': ADMIN,
            'requestBody': _body({
                'url': {'type': 'string', 'description': 'The raw video URL; it is the intent key.'},
                'chat_id': _CHAT_ID,
                'dry_run': _DRY_RUN,
                'force': _FORCE,
            }, required=['url']),
            'responses': {'200': _ok(_ref('SendResult')), '400': _error('Bad url, body or chat_id.'),
                          '401': UNAUTHORIZED, '403': _error('The chat is banned.'),
                          '404': _error('Unknown chat_id (`candidates`); pass force.'), '409': PASSIVE},
        },
    },
    '/api/alerts': {
        'get': {
            'summary': 'Active health alerts (the dashboard banner).',
            'security': ADMIN,
            'responses': {'200': _ok(_array(_ref('Alert'))), '401': UNAUTHORIZED},
        },
    },
    '/api/playlists': {
        'get': {
            'summary': 'What /subscribe would offer for a video, playlist or channel URL.',
            'security': ADMIN,
            'parameters': [_query('url', 'An http(s) URL.', required=True)],
            'responses': {'200': _ok(_ref('PlaylistDiscovery')), '400': _error('Not an http(s) URL, or no uploader.'),
                          '401': UNAUTHORIZED, '502': _error('yt-dlp extraction failed.')},
        },
    },
    '/api/populate': {
        'post': {
            'summary': 'Run the subscription poll now, for every subscription or one key.',
            'security': ADMIN,
            'requestBody': _body({'url': {'type': 'string', 'description': 'A stored subscription key; omit for all.'}}),
            'responses': {'200': _ok(_ref('PopulateResult')), '400': _error('Bad body.'), '401': UNAUTHORIZED,
                          '404': _error('Not a subscription.'), '409': PASSIVE},
        },
    },
}

OPENAPI = {
    'openapi': '3.1.0',
    'info': {
        'title': 'dasovbot admin API',
        'version': '1',
        'description': 'Bearer-token JSON API of the dasovbot dashboard. Writes run on the ACTIVE node only; '
                       'a passive node answers them with 409 and the active node URL.',
    },
    'components': {
        'securitySchemes': {
            'adminToken': {'type': 'http', 'scheme': 'bearer', 'description': 'ADMIN_TOKEN: every /api route.'},
            'apiToken': {'type': 'http', 'scheme': 'bearer',
                         'description': 'API_TOKEN (read-only): /api/videos, /api/videos/{video_id}, /api/openapi.json.'},
        },
        'schemas': SCHEMAS,
    },
    'paths': PATHS,
}


async def api_openapi(request: web.Request) -> web.Response:
    return web.json_response(OPENAPI)
