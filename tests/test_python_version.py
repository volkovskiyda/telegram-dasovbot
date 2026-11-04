"""The image, the CI test job and the devcontainers run the same Python.

Dependabot bumps the Dockerfile's base image on its own (the 3.14-slim and
3.15-slim bumps touched nothing else), which leaves CI testing a different
interpreter than the one the image ships. This fails such a bump until the
rest follows; the docs (README.md, docs/testing.md) are not checked.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / 'Dockerfile'
WORKFLOW = ROOT / '.github' / 'workflows' / 'docker.yml'
DEVCONTAINERS = sorted((ROOT / '.devcontainer').glob('*/devcontainer.json'))


def image_python() -> str:
    match = re.search(r'^FROM python:(\d+\.\d+)-', DOCKERFILE.read_text(), re.MULTILINE)
    assert match, 'Dockerfile has no FROM python:X.Y-... line'
    return match.group(1)


class TestPythonVersion(unittest.TestCase):
    def test_ci_tests_the_image_python(self):
        versions = re.findall(r'''python-version:\s*['"]?(\d+\.\d+)''', WORKFLOW.read_text())
        self.assertTrue(versions, f'no python-version in {WORKFLOW.relative_to(ROOT)}')
        for version in versions:
            with self.subTest(version=version):
                self.assertEqual(version, image_python(),
                                 f'{WORKFLOW.relative_to(ROOT)} tests Python {version}, the Dockerfile ships {image_python()}')

    def test_devcontainers_run_the_image_python(self):
        self.assertTrue(DEVCONTAINERS, 'no .devcontainer/*/devcontainer.json')
        for path in DEVCONTAINERS:
            name = path.relative_to(ROOT)
            with self.subTest(devcontainer=str(name)):
                match = re.search(r'"image"\s*:\s*"python:(\d+\.\d+)-', path.read_text())
                self.assertIsNotNone(match, f'{name} has no "image": "python:X.Y-..." line')
                self.assertEqual(match.group(1), image_python(),
                                 f'{name} runs Python {match.group(1)}, the Dockerfile ships {image_python()}')
