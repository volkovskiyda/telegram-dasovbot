import difflib
import unittest
from pathlib import Path

from dasovbot.cli import render_help

SKILL = Path(__file__).resolve().parent.parent / 'skills' / 'dasovbot' / 'SKILL.md'
START = '<!-- cli-help:start -->'
END = '<!-- cli-help:end -->'
REFRESH = 'python -c "from dasovbot.cli import render_help; print(render_help())"'


def embedded_help(text: str) -> str:
    block = text.split(START, 1)[1].split(END, 1)[0].strip()
    lines = block.splitlines()
    if lines and lines[0].startswith('```'):
        lines = lines[1:]
    if lines and lines[-1].startswith('```'):
        lines = lines[:-1]
    return '\n'.join(lines).strip()


class TestSkillDoc(unittest.TestCase):
    def test_frontmatter_names_the_skill(self):
        text = SKILL.read_text()
        self.assertTrue(text.startswith('---\nname: dasovbot\ndescription: '), text[:60])

    def test_command_reference_matches_the_cli_help(self):
        text = SKILL.read_text()
        self.assertIn(START, text)
        self.assertIn(END, text)
        expected = render_help().strip()
        actual = embedded_help(text)
        if actual != expected:
            diff = '\n'.join(difflib.unified_diff(actual.splitlines(), expected.splitlines(),
                                                  'skills/dasovbot/SKILL.md', 'dasovbot --help', lineterm=''))
            self.fail(f'the skill\'s command reference is stale; refresh it with {REFRESH} and paste the '
                      f'output between the markers\n{diff}')
