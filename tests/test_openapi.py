import json
from unittest.mock import patch

from aiohttp.test_utils import AioHTTPTestCase

from dasovbot.dashboard.openapi import OPENAPI
from dasovbot.dashboard.server import create_app
from tests.helpers import make_state, make_config

API_TOKEN = 'test-api-token'
ADMIN_TOKEN = 'test-admin-token'


def registered_api_routes(app) -> set[tuple[str, str]]:
    routes = set()
    for route in app.router.routes():
        canonical = route.resource.canonical
        if canonical.startswith('/api/') and route.method in ('GET', 'POST'):
            routes.add((canonical, route.method.lower()))
    return routes


def documented_routes() -> set[tuple[str, str]]:
    return {(path, method) for path, methods in OPENAPI['paths'].items() for method in methods}


def collect_refs(node, found: set):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == '$ref':
                found.add(value)
            else:
                collect_refs(value, found)
    elif isinstance(node, list):
        for item in node:
            collect_refs(item, found)
    return found


class TestOpenApiDocument(AioHTTPTestCase):
    def setUp(self):
        self._env_patcher = patch.dict('os.environ', {'API_TOKEN': API_TOKEN, 'ADMIN_TOKEN': ADMIN_TOKEN})
        self._env_patcher.start()
        self.addCleanup(self._env_patcher.stop)
        super().setUp()

    async def get_application(self):
        self.state = make_state(config=make_config(), migration_progress={'status': 'skipped', 'tables': {}, 'elapsed': 0.0})
        return create_app(self.state)

    def test_every_api_route_is_documented(self):
        registered = registered_api_routes(self.app)
        self.assertTrue(registered)
        self.assertEqual(registered - documented_routes(), set(), 'routes missing from openapi.py')

    def test_no_documented_route_without_a_handler(self):
        self.assertEqual(documented_routes() - registered_api_routes(self.app), set(), 'stale paths in openapi.py')

    def test_refs_resolve_and_every_schema_is_used(self):
        refs = collect_refs(OPENAPI['paths'], set()) | collect_refs(OPENAPI['components']['schemas'], set())
        names = {ref.rsplit('/', 1)[1] for ref in refs}
        schemas = set(OPENAPI['components']['schemas'])
        self.assertTrue(all(ref.startswith('#/components/schemas/') for ref in refs))
        self.assertEqual(names - schemas, set(), 'dangling $ref')
        self.assertEqual(schemas - names, set(), 'unused schema')

    def test_every_operation_declares_security_and_a_401(self):
        for path, methods in OPENAPI['paths'].items():
            for method, operation in methods.items():
                self.assertIn('security', operation, f'{method} {path}')
                self.assertIn('401', operation['responses'], f'{method} {path}')
                self.assertIn('200', operation['responses'], f'{method} {path}')
                self.assertTrue(operation.get('summary'), f'{method} {path}')

    def test_read_only_routes_accept_both_tokens_and_others_only_admin(self):
        for path, methods in OPENAPI['paths'].items():
            for method, operation in methods.items():
                schemes = {name for entry in operation['security'] for name in entry}
                if path in ('/api/videos', '/api/videos/{video_id}', '/api/openapi.json'):
                    self.assertEqual(schemes, {'adminToken', 'apiToken'}, f'{method} {path}')
                else:
                    self.assertEqual(schemes, {'adminToken'}, f'{method} {path}')

    def test_document_is_json_serializable(self):
        body = json.loads(json.dumps(OPENAPI))
        self.assertEqual(body['openapi'], '3.1.0')

    async def test_served_to_both_tokens(self):
        for token in (API_TOKEN, ADMIN_TOKEN):
            resp = await self.client.get('/api/openapi.json', headers={'Authorization': f'Bearer {token}'})
            self.assertEqual(resp.status, 200, token)
            body = await resp.json()
            self.assertEqual(body['openapi'], '3.1.0')
            self.assertIn('/api/status', body['paths'])

    async def test_refused_without_a_token(self):
        resp = await self.client.get('/api/openapi.json')
        self.assertEqual(resp.status, 401)
