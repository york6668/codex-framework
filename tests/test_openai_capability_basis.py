"""Navigation coverage plus existing advisory boundaries, not NLP adoption proof.

The independent reviewer separately exercises the policy's scenario judgements.
Synthetic capability labels here are supplied facts, never availability evidence.
"""
import copy
from datetime import datetime, timezone
from pathlib import Path
import re
import unittest
from urllib.parse import urlsplit, unquote

from test_execution_check import action_case, check

ROOT = Path(__file__).resolve().parents[1]
BASIS = ROOT / '.agents/skills/york-engineering/references/openai-capability-basis.md'
ENTRIES = [
    '.agents/skills/york-engineering/SKILL.md',
    '.agents/skills/york-engineering/references/business-workflow.md',
    '.agents/skills/york-engineering/references/verification.md',
    '.agents/skills/york-task-alignment/SKILL.md',
    '.agents/skills/york-task-alignment/references/execution-check.md',
    '.agents/skills/york-workstation/SKILL.md',
    '.agents/skills/york-workstation/references/environment-sync.md',
]


def links(path):
    return re.findall(r'\]\(([^\s)]+)\)', path.read_text())


def reachable(start, target):
    todo, seen = [(ROOT / start).resolve()], set()
    while todo and len(seen) < 256:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        if path == target.resolve():
            return True
        if not path.is_file() or not path.is_relative_to(ROOT):
            continue
        for link in links(path):
            u = urlsplit(link)
            if not u.scheme and u.path:
                other = (path.parent / unquote(u.path)).resolve()
                if other.suffix == '.md' and other.is_relative_to(ROOT):
                    todo.append(other)
    return False


class OpenAICapabilityBasisTests(unittest.TestCase):
    def test_related_authority_entries_reach_complete_navigation_method(self):
        self.assertTrue(BASIS.is_file(), 'OpenAI official-navigation authority is absent')
        for entry in ENTRIES:
            with self.subTest(entry=entry):
                self.assertTrue(reachable(entry, BASIS), entry)
        official = set(links(BASIS))
        self.assertTrue({'https://developers.openai.com/llms.txt',
                         'https://learn.chatgpt.com/docs/llms.txt',
                         'https://developers.openai.com/api/llms.txt'} <= official)

    def test_example_catalog_does_not_restrict_another_supplied_capability(self):
        request = action_case()
        arbitrary = 'new-official-family:current-host:observed-entry'
        request['context']['capabilities'] = [arbitrary]
        request['action']['capabilities'] = [arbitrary]
        result = check.evaluate(request)
        self.assertEqual(result['status'], 'ready')
        # ready compares supplied relations; it does not certify official origin,
        # actual availability, eligibility, payment or the real current host.
        self.assertNotIn('host_capability_verified', result)

    def test_local_capability_label_cannot_satisfy_cloud_surface_requirement(self):
        request = action_case()
        request['context']['capabilities'] = ['codex-local:command-hooks']
        request['action']['capabilities'] = ['work-cloud:command-hooks']
        result = check.evaluate(request)
        self.assertEqual(result['status'], 'needs_evidence')
        self.assertIn('execution_capability', result['missing'])

    def test_no_snapshot_source_is_not_ready_despite_capability_self_label(self):
        request = action_case()
        request['context'].pop('node_snapshot_ref')
        result = check.evaluate(request)
        self.assertEqual(result['status'], 'needs_evidence')
        self.assertIn('node_runtime_snapshot', result['missing'])

    def test_expired_observation_not_refreshed_by_rereading_it(self):
        request = action_case()
        request['context'].update(node_snapshot_observed_at='2000-01-01T00:00:00Z',
                                  node_snapshot_max_age_seconds=60)
        result = check.evaluate(request)
        self.assertEqual(result['status'], 'needs_evidence')
        self.assertIn('node_snapshot_stale_or_unverified', result['missing'])
        request['context']['node_snapshot_observed_at'] = datetime.now(timezone.utc).isoformat()
        self.assertEqual(check.evaluate(request)['status'], 'ready')

    def test_basis_revision_does_not_extend_scope_or_mutate_other_work(self):
        request = action_case()
        request['action']['resource'] = {'kind': 'repo', 'id': 'unrelated-business', 'path': 'src/main.py'}
        before = copy.deepcopy(request)
        result = check.evaluate(request)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('outside_task_scope', result['blocked'])
        self.assertEqual(request, before)


if __name__ == '__main__':
    unittest.main()
