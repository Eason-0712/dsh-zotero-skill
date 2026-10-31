"""Offline contract tests: source evidence, PDF output, caching and corpus reuse."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'zotero/scripts'))
from paper_reader import storage as s, jobs, pdf, review
spec = importlib.util.spec_from_file_location('reader_test_zotero', ROOT / 'zotero/scripts/zotero.py')
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)


def fixture_pdf(path, *, columns=False):
    from reportlab.pdfgen.canvas import Canvas
    c = Canvas(str(path), pagesize=(600, 800))
    c.setFont('Helvetica', 11)
    if columns:
        for i in range(12):
            c.drawString(35, 740 - 18 * i, f'LEFT {i} studies independent feature extraction.')
            c.drawString(325, 740 - 18 * i, f'RIGHT {i} compares held out subject performance.')
    else:
        c.drawString(35, 750, 'A paper about reproducible scientific models')
        c.drawString(35, 710, 'The method combines two branches and learns adaptive weights.')
        c.drawString(35, 675, 'The experiment uses held out subjects to avoid data leakage.')
        c.drawString(35, 640, 'The authors report improved accuracy with reduced model size.')
        c.drawString(35, 605, 'A limitation is that deployment latency was not evaluated.')
    c.showPage()
    c.drawString(35, 740, 'Appendix provides training settings and implementation details.')
    c.save()


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {'ZOTERO_SKILL_CONFIG_DIR': str(self.directory / 'state')})
        self.environment.start()
        self.source = self.directory / 'paper.pdf'
        fixture_pdf(self.source)
        self.record = {'key': 'PAPER001', 'data': {'title': 'Scientific models', 'itemType': 'journalArticle'}}
        self.entry = {'item': self.record, 'api_prefix': '/api/users/0'}
        self.options = s.settings({'outputs': ['notes', 'translation', 'annotated-pdf', 'bilingual-pdf'], 'mode': 'deep'})
        self.args = SimpleNamespace(attachment=None, ocr='off')
        self.resolve = patch.object(jobs, 'local_pdf', return_value=('PAPER001', 'ATTACH01', self.source))
        self.resolve.start()
        # Offline layout contracts use a built-in CJK test font. Real embedded
        # Noto rendering is checked separately; unit tests never download assets.
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        if 'STSong-Light' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        test_font = patch.object(pdf, 'font_name', return_value='STSong-Light')
        test_font.start()
        self.addCleanup(test_font.stop)
        self.addCleanup(self.resolve.stop)
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.temp.cleanup)

    def prepare(self):
        return jobs.prepare_one(api, self.entry, self.options, s.profile(''), self.args)

    def full_result(self, job_id):
        _, manifest = jobs.load(job_id)
        doc = s.read(manifest['document'])
        claims = []
        for index, category in enumerate(jobs.CATEGORIES):
            block = doc['blocks'][min(index, len(doc['blocks']) - 1)]
            claims.append({'id': f'claim-{index}', 'category': category, 'kind': 'reported',
                           'statement': '这是一条需要原文支持的具体中文阅读结论，用于验证证据链。',
                           'evidence': [{'block_id': block['id'], 'quote': block['text']}]})
        return {'source_hash': manifest['source_hash'], 'claims': claims,
                'translations': [{'block_id': b['id'], 'text': '这是一段完整的中文译文，包含原文的科学方法、实验和适用条件。'} for b in doc['blocks'] if b['translate']], 'missing': {}}

    def test_cache_is_reused_without_reparsing_and_profile_is_independent(self):
        first = self.prepare()
        with patch.object(pdf, 'extract', side_effect=AssertionError('cache should be used')):
            again = self.prepare()
            second_profile = jobs.prepare_one(api, self.entry, self.options,
                                              {'question': 'A different research question'}, self.args)
        self.assertEqual(first['id'], again['id'])
        self.assertTrue(again['cache_hit'])
        self.assertNotEqual(first['id'], second_profile['id'])

    def test_new_layout_and_outputs_reuse_existing_model_work(self):
        first = self.prepare()
        jobs.accept(first['id'], self.full_result(first['id']))
        self.options = s.settings(dict(self.options, layout='stacked', execution='parallel', outputs=['notes', 'bilingual-pdf']))
        second = self.prepare()
        self.assertNotEqual(first['id'], second['id'])
        self.assertTrue(second['status']['ready'])
        self.assertEqual(second['status']['claims'], first['status']['claims'] + len(jobs.CATEGORIES))
        self.assertEqual(jobs.load(first['id'])[1]['result_store'], jobs.load(second['id'])[1]['result_store'])

    def test_bad_source_and_fabricated_quote_are_rejected_without_persisting(self):
        job_id = self.prepare()['id']
        result = self.full_result(job_id)
        bad = copy.deepcopy(result)
        bad['source_hash'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'source_hash'):
            jobs.accept(job_id, bad)
        bad = copy.deepcopy(result)
        bad['claims'][0]['evidence'][0]['quote'] = 'This sentence does not exist in the original paper.'
        with self.assertRaisesRegex(ValueError, 'Quote not found'):
            jobs.accept(job_id, bad)
        self.assertEqual(jobs.inspect(job_id)['claims'], 0)

    def test_partial_results_resume_and_conflicts_are_not_overwritten(self):
        job_id = self.prepare()['id']
        result = self.full_result(job_id)
        partial = dict(result, translations=result['translations'][:1], claims=[])
        self.assertFalse(jobs.accept(job_id, partial)['ready'])
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            jobs.export(job_id, self.directory / 'exports')
        exported = jobs.export(job_id, self.directory / 'exports', partial=True)
        self.assertTrue(all(not f.endswith('.pdf') for f in exported['files']))
        self.assertTrue(jobs.accept(job_id, result)['ready'])
        conflict = copy.deepcopy(partial)
        conflict['translations'][0]['text'] = '不同的译文，不应覆盖用户已经接受的翻译。'
        with self.assertRaisesRegex(ValueError, 'differs'):
            jobs.accept(job_id, conflict)
        self.assertTrue(jobs.inspect(job_id)['ready'])

    def test_deep_notes_need_coverage_or_explicit_missing_evidence(self):
        job_id = self.prepare()['id']
        result = self.full_result(job_id)
        result['claims'] = [c for c in result['claims'] if c['category'] != 'formula']
        self.assertEqual(jobs.accept(job_id, result)['uncovered_categories'], ['formula'])
        evidence_limit = {'source_hash': result['source_hash'], 'missing': {'formula': '论文未给出数学公式，无法把额外推导当成作者的证明。'}}
        self.assertTrue(jobs.accept(job_id, evidence_limit)['ready'])

    def test_export_preserves_source_and_user_edits(self):
        from pypdf import PdfReader
        job_id = self.prepare()['id']
        jobs.accept(job_id, self.full_result(job_id))
        original_hash = s.file_hash(self.source)
        with patch.object(pdf, 'bilingual', wraps=pdf.bilingual):
            first = jobs.export(job_id, self.directory / 'exports')
        notes = Path(first['directory']) / 'notes.md'
        notes.write_text('my edited note', encoding='utf-8')
        second = jobs.export(job_id, self.directory / 'exports')
        self.assertNotEqual(first['directory'], second['directory'])
        self.assertEqual(notes.read_text(), 'my edited note')
        self.assertEqual(s.file_hash(self.source), original_hash)
        annotations = [a.get_object() for p in PdfReader(Path(first['directory']) / 'annotated.pdf').pages for a in p.get('/Annots', [])]
        self.assertTrue(annotations)
        self.assertIn('具体中文', str(annotations[0]['/Contents']))
        self.assertTrue(all(len(a['/QuadPoints']) % 8 == 0 for a in annotations))

    def test_changed_source_cannot_be_exported_or_added_to_corpus(self):
        job_id = self.prepare()['id']
        jobs.accept(job_id, self.full_result(job_id))
        with self.source.open('ab') as stream:
            stream.write(b'\n% changed\n')
        with self.assertRaisesRegex(ValueError, 'changed'):
            jobs.export(job_id, self.directory / 'exports')
        with self.assertRaisesRegex(ValueError, 'Stale'):
            review.prepare([job_id], '', self.directory / 'corpus.json')

    def test_review_requires_real_claims_and_preserves_hypotheses(self):
        job_id = self.prepare()['id']
        jobs.accept(job_id, self.full_result(job_id))
        path = self.directory / 'corpus.json'
        review.prepare([job_id], '', path)
        corpus = s.read(path)
        evidence = [{'job_id': job_id, 'claim_id': 'claim-0'}]
        analysis = {'fingerprint': corpus['fingerprint'], 'comparisons': [], 'development': [],
                    'reading_order': [], 'gaps': [{'kind': 'hypothesis', 'statement': '尚需检索更多文献和实验验证的候选研究问题。', 'evidence': evidence}],
                    'limitations': ['该语料未覆盖足够的实验协议，不能证明普遍泛化。'],
                    'search_queries': ['cross subject generalization protocol']}
        bad = copy.deepcopy(analysis)
        bad['gaps'][0]['evidence'][0]['claim_id'] = 'invented'
        with self.assertRaisesRegex(ValueError, 'Unknown corpus'):
            review.render(corpus, bad, self.directory / 'bad.md')
        self.assertFalse((self.directory / 'bad.md').exists())
        result = review.render(corpus, analysis, self.directory / 'review.md')
        self.assertIn('待验证假设', Path(result['markdown']).read_text(encoding='utf-8'))
        changed = copy.deepcopy(corpus)
        changed['papers'][0]['title'] = 'tampered'
        with self.assertRaisesRegex(ValueError, 'modified'):
            review.render(changed, analysis, self.directory / 'tampered.md')

    def test_selection_uses_authenticated_bridge_and_never_substitutes_collection(self):
        args = SimpleNamespace(selected=True)
        with patch.object(api, 'local_write', return_value=api.Response(404, {}, 'not installed')), patch.object(api, 'selected_items') as fallback:
            with self.assertRaisesRegex(ValueError, 'companion'):
                jobs.select(api, args)
            fallback.assert_not_called()
        with patch.object(api, 'local_write', return_value=api.Response(200, {'Content-Type': 'application/json'}, json.dumps([self.entry]))) as request:
            self.assertEqual(jobs.select(api, args)[0]['item']['key'], 'PAPER001')
            request.assert_called_once_with('/api/dsh-reader/selection', {}, method='POST')

    def test_bilingual_layouts_and_overflow_keep_every_translation(self):
        from pypdf import PdfReader
        doc = pdf.extract(self.source, ocr='off')
        translations = {b['id']: ('精读译文包含方法与实验条件。' * 350) for b in doc['blocks']}
        for layout in ('side-by-side', 'stacked', 'alternating'):
            target = self.directory / (layout + '.pdf')
            count = pdf.bilingual(self.source, target, doc, translations, layout)
            reader = PdfReader(target)
            self.assertGreater(count, 2)
            self.assertEqual(len(reader.pages), count)
            self.assertEqual(float(reader.pages[0].mediabox.width), 1200 if layout == 'side-by-side' else 600)
            self.assertEqual(float(reader.pages[0].mediabox.height), 1600 if layout == 'stacked' else 800)
            text = ''.join(p.extract_text() or '' for p in reader.pages)
            self.assertIn('精读译文', text)
            self.assertIn('A paper about', text)

    def test_column_order_reads_left_column_before_right(self):
        source = self.directory / 'columns.pdf'
        fixture_pdf(source, columns=True)
        doc = pdf.extract(source, ocr='off')
        page = ' '.join(b['text'] for b in doc['blocks'] if b['page'] == 1)
        self.assertLess(page.index('LEFT 11'), page.index('RIGHT 0'))

    def test_ocr_is_requested_for_scan_and_unreadable_is_explicit(self):
        from reportlab.pdfgen.canvas import Canvas
        source = self.directory / 'scan.pdf'
        c = Canvas(str(source)); c.showPage(); c.save()
        words = [{'text': 'Scanned text with an exact quote for evidence.', 'x0': 30, 'x1': 300, 'top': 30, 'bottom': 45, 'confidence': .9}]
        with patch.object(pdf, '_ocr', return_value=words) as ocr:
            result = pdf.extract(source)
            ocr.assert_called_once()
        self.assertTrue(result['pages'][0]['ocr'])
        self.assertTrue(result['pages'][0]['readable'])
        self.assertIn('OCR', result['warnings'][0])
        self.assertFalse(pdf.extract(source, ocr='off')['pages'][0]['readable'])

    def test_corpus_search_reasons_do_not_claim_to_have_read_fulltext(self):
        candidate = {'title': 'Adaptive graph models', 'abstract': 'A method for graph feature learning.', 'score': .5}
        with patch.object(api, 'gather_candidates', return_value=[candidate]):
            result = review.search(api, ['graph learning'], {'question': 'graphs', 'keywords': ['graph'], 'priorities': ['practical']})
        row = result['recommendations'][0]
        self.assertTrue(row['needs_fulltext_review'])
        self.assertEqual(row['matched_profile_terms'], ['graph'])
        self.assertEqual(row['evidence_level'], 'metadata_and_abstract')

    def test_no_empty_output_default_and_no_unbounded_parallelism(self):
        self.assertEqual(s.settings()['execution'], 'economy')
        self.assertEqual(s.settings()['outputs'], [])
        with self.assertRaises(ValueError):
            s.settings({'max_workers': 999})
        with self.assertRaises(ValueError):
            s.settings({'profile': '../../secrets'})

    def test_job_lock_prevents_racing_result_writers(self):
        job_id = self.prepare()['id']
        directory, _ = jobs.load(job_id)
        with s.lock(directory):
            with self.assertRaisesRegex(ValueError, 'busy'):
                jobs.accept(job_id, self.full_result(job_id))
        self.assertTrue(jobs.accept(job_id, self.full_result(job_id))['ready'])

    def test_publishing_escapes_model_html_and_is_idempotent(self):
        job_id = self.prepare()['id']
        result = self.full_result(job_id)
        result['claims'][0]['statement'] = '<script>alert(1)</script> 这是必须安全转义的原文分析内容。'
        jobs.accept(job_id, result)
        response = api.Response(200, {'Content-Type': 'application/json'}, '{"successful":{"0":{"key":"NOTE0001"}}}')
        with patch.object(api, 'local_write', return_value=response) as request:
            first = jobs.publish_note(api, job_id)
            second = jobs.publish_note(api, job_id)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(first['note_key'], 'NOTE0001')
        self.assertTrue(second['reused'])
        html = request.call_args.args[1][0]['note']
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('<a href="zotero://open-pdf/', html)

    def test_references_are_not_translated_but_appendix_is(self):
        from reportlab.pdfgen.canvas import Canvas
        source = self.directory / 'references.pdf'
        c = Canvas(str(source))
        for index, text in enumerate(['References', 'A reference entry about scientific computing.', 'Appendix', 'Additional experimental and implementation details.']):
            c.drawString(40, 750-index*30, text)
        c.save()
        result = pdf.extract(source, ocr='off')
        reference = next(b for b in result['blocks'] if 'reference entry' in b['text'])
        appendix = next(b for b in result['blocks'] if 'Additional experimental' in b['text'])
        self.assertFalse(reference['translate'])
        self.assertTrue(appendix['translate'])


if __name__ == '__main__':
    unittest.main()
