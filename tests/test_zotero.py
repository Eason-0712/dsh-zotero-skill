import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('zotero_under_test', ROOT / 'zotero/scripts/zotero.py')
z = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = z
spec.loader.exec_module(z)
ENTRY = '@article{li2026,\n title={中文论文},\n author={Li},\n year={2026}\n}\n'
ITEM = {'key': 'ABC12345', 'data': {'title': '中文论文', 'date': '2026',
        'creators': [{'firstName': 'Ming', 'lastName': 'Li'}]}}


class ZoteroTests(unittest.TestCase):
    def run_cli(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            z.main(argv)
        return json.loads(out.getvalue())

    def test_search_with_keys(self):
        with patch.object(z, 'api_get', return_value=[ITEM]), patch.object(z, 'export_bibtex', return_value=ENTRY):
            rows = self.run_cli(['search', '中文', '--with-bibtex-keys', '--json'])
        self.assertEqual(rows[0]['bibtexKey'], 'li2026')
        self.assertEqual(rows[0]['creators'], ['Ming Li'])
        self.assertEqual(rows[0]['year'], '2026')

    def test_ambiguous_query_rejected(self):
        with patch.object(z, 'api_get', return_value=[ITEM, dict(ITEM, key='SECOND')]):
            with self.assertRaisesRegex(SystemExit, 'Ambiguous'):
                z.find_item(item_key=None, query_text='test')

    def test_cite_backups_and_bib_dedup(self):
        for kind, citation in [('--markdown', '[@li2026]'), ('--tex', r'\cite{li2026}')]:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                draft = Path(temp) / '中文草稿.md'
                bib = Path(temp) / 'references.bib'
                draft.write_text('论断 <cite>。', encoding='utf-8')
                bib.write_text(ENTRY, encoding='utf-8')
                with patch.object(z, 'api_get', return_value=ITEM), patch.object(z, 'export_bibtex', return_value=ENTRY):
                    result = self.run_cli(['cite', '--item-key', ITEM['key'], kind, str(draft),
                                           '--bib', str(bib), '--marker', '<cite>'])
                self.assertEqual(draft.read_text(encoding='utf-8'), '论断 ' + citation + '。')
                self.assertEqual(bib.read_text(encoding='utf-8'), ENTRY)
                self.assertFalse(result['bib_entry_added'])
                self.assertEqual(len(result['backups']), 2)
                self.assertEqual(Path(result['backups'][0]).read_text(encoding='utf-8'), '论断 <cite>。')

    def test_bad_marker_does_not_mutate_files_or_call_api(self):
        with tempfile.TemporaryDirectory() as temp:
            draft, bib = Path(temp) / 'draft.md', Path(temp) / 'refs.bib'
            for text in ['no marker', '<cite> twice <cite>']:
                draft.write_text(text, encoding='utf-8')
                with patch.object(z, 'api_get') as api:
                    with self.assertRaisesRegex(SystemExit, 'exactly once'):
                        z.main(['cite', '--item-key', 'ABC', '--markdown', str(draft),
                                '--bib', str(bib), '--marker', '<cite>'])
                    api.assert_not_called()
                self.assertFalse(bib.exists())
                self.assertEqual(draft.read_text(encoding='utf-8'), text)

    def test_export_to_file(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'refs.bib'
            with patch.object(z, 'api_response', return_value=z.Response(200, {}, ENTRY)):
                result = self.run_cli(['export-bibtex', '--item-key', 'ABC', '--out', str(target)])
            self.assertEqual(target.read_text(encoding='utf-8'), ENTRY)
            self.assertEqual(result['bibtex_entries'], 1)

    def test_loopback_skips_proxy(self):
        response = MagicMock()
        response.status = 200
        response.headers = {'Content-Type': 'application/json'}
        response.read.return_value = b'[]'
        response.__enter__.return_value = response
        with patch.object(z, 'DEFAULT_BASE_URL', 'http://127.0.0.1:23119'), \
             patch.object(z.urllib.request, 'ProxyHandler', wraps=z.urllib.request.ProxyHandler) as proxy, \
             patch.object(z.urllib.request, 'build_opener') as build:
            build.return_value.open.return_value = response
            result = z.request('/api/users/0/items')
            proxy.assert_called_once_with({})
            self.assertEqual(result.status, 200)
            self.assertEqual(z.parse_body(result), [])

    def test_import_requires_explicit_flag(self):
        with patch.object(z, 'connector_post') as post:
            with self.assertRaisesRegex(SystemExit, 'without --yes'):
                z.main(['import-bibtex', '--text', ENTRY])
            post.assert_not_called()
        with patch.object(z, 'connector_post', return_value=z.Response(200, {'Content-Type': 'application/json'}, '{}')):
            result = self.run_cli(['import-bibtex', '--text', ENTRY, '--yes'])
            self.assertTrue(result['session'].startswith('dsh-'))

    def test_profile_override(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'ZOTERO_PROFILE_DIR': temp}):
            self.assertEqual(z.profile_dir(), Path(temp).resolve())

    def test_installer_dry_run_install_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'skills'
            command = [sys.executable, str(ROOT / 'install.py'), '--skills-dir', str(root)]
            dry = subprocess.run(command + ['--dry-run'], capture_output=True)
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertFalse(root.exists())
            installed = subprocess.run(command, capture_output=True)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertTrue((root / 'zotero/SKILL.md').is_file())
            repeated = subprocess.run(command, capture_output=True)
            self.assertNotEqual(repeated.returncode, 0)


if __name__ == '__main__':
    unittest.main()
