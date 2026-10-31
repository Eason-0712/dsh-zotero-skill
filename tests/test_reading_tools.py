"""Scoped Q&A, native Zotero, revision and matrix boundary tests; no network."""
import copy
import csv
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock
import test_reader as fixture
from paper_reader import discovery, evidence, jobs, matrix, native, pdf, quality, review, storage as s


class ReadingToolsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        environment = patch.dict(os.environ, {'ZOTERO_SKILL_CONFIG_DIR': str(self.root / 'state')})
        environment.start(); self.addCleanup(environment.stop)
        self.source = self.root / 'paper.pdf'
        fixture.fixture_pdf(self.source)
        self.api = fixture.api
        self.options = s.settings({'outputs': ['translation', 'bilingual-markdown'], 'mode': 'quick'})
        entry = {'item': {'key': 'PAPER001', 'data': {'title': 'Adaptive branch models', 'DOI': '10.1000/example'}}, 'api_prefix': '/api/users/0'}
        resolver = patch.object(jobs, 'local_pdf', return_value=('PAPER001', 'ATTACH01', self.source))
        resolver.start(); self.addCleanup(resolver.stop)
        self.args = SimpleNamespace(attachment=None, ocr='off')
        self.job = jobs.prepare_one(self.api, entry, self.options, s.profile(''), self.args)['id']
        self.directory, self.manifest = jobs.load(self.job)
        self.document = s.read(self.manifest['document'])
        self.block = next(b for b in self.document['blocks'] if 'two branches' in b['text'])
        self.claim = {'id': 'method-1', 'category': 'method', 'kind': 'reported',
                      'statement': '该方法结合两个分支，并通过学习自适应权重完成特征融合。',
                      'evidence': [{'block_id': self.block['id'], 'quote': self.block['text']}]}
        self.payload = {'source_hash': self.manifest['source_hash'], 'claims': [self.claim],
                        'translations': [{'block_id': b['id'], 'text': '此段中文译文用于核对方法、实验及实现条件，保留原始证据的位置。'} for b in self.document['blocks'] if b['translate']], 'missing': {}}

    def accept(self):
        return jobs.accept(self.job, self.payload)

    def context(self, question='How are adaptive weights learned?', query='adaptive branches weights'):
        path = self.root / 'question.json'
        evidence.prepare_question([self.job], question, path, query=query)
        return s.read(path)

    def answer(self, context):
        passage = next(p for p in context['passages'] if p['block_id'] == self.block['id'])
        return {'fingerprint': context['fingerprint'], 'status': 'answered',
                'answer': '原文描述使用两个分支，并学习自适应权重；更细致的实现仍需结合其余方法章节。',
                'evidence': [{'job_id': self.job, 'block_id': self.block['id'], 'quote': passage['text']}],
                'limitations': ['当前仅检索到方法相关片段，没有证明其跨数据集泛化能力。']}

    def matrix_context(self):
        path = self.root / 'matrix-context.json'
        matrix.prepare([self.job], '', ['方法', '实验划分'], path)
        return s.read(path)

    def matrix_result(self, context):
        return {'fingerprint': context['fingerprint'], 'rows': [{'job_id': self.job, 'cells': {
            '方法': {'status': 'reported', 'value': '双分支自适应融合', 'claim_ids': ['method-1']},
            '实验划分': {'status': 'missing', 'value': '尚未提取到足够信息，需进一步阅读实验章节。', 'claim_ids': []}}}]}

    def test_corrections_keep_history_and_reject_stale_revision(self):
        first = self.accept()
        correction = dict(self.payload, translations=[], claims=[dict(self.claim, statement='修正后的解释说明两分支通过自适应权重融合，不能由此推出额外性能结论。')])
        second = jobs.revise(self.job, correction, first['revision'], '根据原文修正过度推断')
        self.assertNotEqual(first['revision'], second['revision'])
        self.assertEqual(len(jobs.history(self.job)), 2)
        with self.assertRaisesRegex(ValueError, 'revision changed'):
            jobs.revise(self.job, self.payload, first['revision'], '过期编辑不应覆盖新内容')
        self.assertEqual(jobs.inspect(self.job)['revision'], second['revision'])

    def test_reverting_content_creates_a_new_revision_without_history_cycles(self):
        first = self.accept()
        changed = dict(self.payload, translations=[dict(self.payload['translations'][0], text='这一处翻译经过人工核对，补充了方法实现的准确表述。')])
        second = jobs.revise(self.job, changed, first['revision'], '修正原始译文的表述方式')
        third = jobs.revise(self.job, self.payload, second['revision'], '人工复核后恢复原始译文')
        self.assertEqual(len(jobs.history(self.job)), 3)
        self.assertEqual(len({first['revision'], second['revision'], third['revision']}), 3)
        self.assertEqual(jobs.aggregate(self.directory)[0][changed['translations'][0]['block_id']], self.payload['translations'][0]['text'])

    def test_repeat_accept_is_idempotent_and_legacy_results_migrate(self):
        store = jobs.result_store(self.directory)
        s.write(store / 'legacy.json', self.payload)
        self.assertEqual(len(jobs.aggregate(self.directory)[1]), 1)
        extra = dict(self.payload, claims=[dict(self.claim, id='method-2', statement='这是基于同一方法段落补充的另一条有原文依据的解释。')])
        first = jobs.accept(self.job, extra)
        second = jobs.accept(self.job, extra)
        self.assertEqual(first['revision'], second['revision'])
        self.assertEqual(len(jobs.aggregate(self.directory)[1]), 2)
        self.assertTrue((store / 'legacy.json').exists())

    def test_snapshot_tampering_is_detected(self):
        self.accept()
        path = jobs.result_store(self.directory) / 'current.json'
        record = s.read(path); record['payload']['claims'][0]['statement'] = 'edited'
        s.write(path, record)
        with self.assertRaisesRegex(ValueError, 'modified'):
            jobs.aggregate(self.directory)

    def test_number_checks_allow_equivalent_percentages_and_report_changed_values(self):
        self.assertEqual(quality.numbers('Accuracy 95%, n=1,000; p<0.05.'), quality.numbers('准确率 0.95，样本量 1000；p<0.05。'))
        doc = {'blocks': [{'id': 'p1-b1', 'text': 'Accuracy is 95% with 100 samples.'}]}
        issues = quality.audit(doc, {'p1-b1': '准确率为 98%，共有 100 个样本。'})
        self.assertEqual(issues[0]['code'], 'number_mismatch')
        self.assertIn('0.95', issues[0]['missing'])

    def test_glossary_respects_word_boundaries_and_is_validated(self):
        profile = {'glossary': [{'source': 'GCN', 'target': '图卷积网络'}]}
        self.assertEqual(quality.audit({'blocks': [{'id': 'b', 'text': 'XGCN module'}]}, {'b': '模型模块'}, profile), [])
        issues = quality.audit({'blocks': [{'id': 'b', 'text': 'A GCN module'}]}, {'b': '模型模块'}, profile)
        self.assertEqual(issues[0]['code'], 'glossary_missing')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            quality.profile_fields({'glossary': profile['glossary'] * 2})

    def test_strict_acceptance_is_atomic_when_audit_fails(self):
        before = jobs.inspect(self.job)['revision']
        bad = dict(self.payload, translations=[dict(self.payload['translations'][0], text='译文意外添加了 93% 的性能数字，需要先回查原文。')])
        with self.assertRaisesRegex(ValueError, 'audit needs review'):
            jobs.accept(self.job, bad, strict=True)
        self.assertEqual(jobs.inspect(self.job)['revision'], before)
        self.assertTrue(jobs.accept(self.job, bad)['translation_audit'])

    def test_focused_question_is_scoped_bounded_and_cited(self):
        self.accept()
        context = self.context()
        self.assertTrue(context['passages'])
        self.assertLessEqual(sum(len(p['text']) for p in context['passages']), 10000)
        self.assertEqual({p['job_id'] for p in context['passages']}, {self.job})
        output = self.root / 'answer.md'
        evidence.answer(context, self.answer(context), output)
        self.assertIn('zotero://open-pdf/', output.read_text())
        with self.assertRaises(FileExistsError):
            evidence.answer(context, self.answer(context), output)

    def test_fabricated_answer_quote_and_tampered_context_are_rejected(self):
        context = self.context()
        answer = self.answer(context)
        answer['evidence'][0]['quote'] = 'This evidence was invented and is absent from the source.'
        with self.assertRaisesRegex(ValueError, 'Quote does not resolve'):
            evidence.answer(context, answer, self.root / 'bad.md')
        context['question'] = 'tampered question'
        with self.assertRaisesRegex(ValueError, 'modified'):
            evidence.answer(context, answer, self.root / 'tampered.md')

    def test_old_question_context_cannot_be_used_after_correction(self):
        first = self.accept(); context = self.context()
        changed = dict(self.payload, claims=[dict(self.claim, statement='复核后补充说明：动态融合权重的学习细节尚需进一步查证。')])
        jobs.revise(self.job, changed, first['revision'], '修正对动态融合权重的解释')
        with self.assertRaisesRegex(ValueError, 'Evidence changed'):
            evidence.answer(context, self.answer(context), self.root / 'stale.md')

    def test_no_hit_question_requires_explicit_insufficient_evidence(self):
        context = self.context(question='Does it measure optical spectroscopy?', query='spectroscopy xenon')
        self.assertFalse(context['passages'])
        result = {'fingerprint': context['fingerprint'], 'status': 'answered', 'answer': '当前检索内容无法支撑对这个问题作出肯定的回答。', 'evidence': [], 'limitations': ['检索未命中相关证据。']}
        with self.assertRaisesRegex(ValueError, 'needs source evidence'):
            evidence.answer(context, result, self.root / 'no.md')
        result['status'] = 'insufficient_evidence'
        self.assertEqual(evidence.answer(context, result, self.root / 'unknown.md')['source_quotes'], 0)

    def test_conflict_claim_needs_two_distinct_quotes(self):
        context = self.context(); answer = self.answer(context); answer['status'] = 'conflicting_evidence'
        with self.assertRaisesRegex(ValueError, 'two distinct'):
            evidence.answer(context, answer, self.root / 'false-conflict.md')

    def test_global_index_does_not_leak_unselected_papers(self):
        other = self.root / 'other.pdf'
        from reportlab.pdfgen.canvas import Canvas
        c = Canvas(str(other)); c.drawString(40, 700, 'Private spectroscopy evidence from a completely different paper.'); c.save()
        entry = {'item': {'key': 'OTHER001', 'data': {'title': 'Other paper'}}, 'api_prefix': '/api/users/0'}
        with patch.object(jobs, 'local_pdf', return_value=('OTHER001', 'OTHERPDF', other)):
            other_job = jobs.prepare_one(self.api, entry, self.options, s.profile(''), self.args)['id']
        evidence.prepare_question([other_job], 'spectroscopy', self.root / 'other-context.json')
        context = self.context(question='spectroscopy', query='spectroscopy')
        self.assertFalse(context['passages'])

    def test_fts_unavailable_uses_a_scoped_standard_library_fallback(self):
        actual = sqlite3.connect
        class WithoutFTS:
            def __init__(self, *args, **kwargs): self.db = actual(*args, **kwargs)
            def __enter__(self): return self
            def __exit__(self, *args): return self.db.__exit__(*args)
            def close(self): self.db.close()
            def execute(self, sql, *args):
                if 'VIRTUAL TABLE' in sql: raise sqlite3.OperationalError('no such module: fts5')
                return self.db.execute(sql, *args)
        with patch.object(evidence.sqlite3, 'connect', side_effect=WithoutFTS):
            context = self.context()
        self.assertEqual(context['retrieval'], 'stdlib_lexical_fallback')
        self.assertTrue(context['passages'])

    def test_matrix_has_source_cells_explicit_missing_and_safe_exports(self):
        self.accept()
        manifest = s.read(self.directory / 'manifest.json'); manifest['title'] = '=HYPERLINK("https://invalid")'
        s.write(self.directory / 'manifest.json', manifest)
        context = self.matrix_context()
        result = matrix.render(context, self.matrix_result(context), self.root / 'comparison')
        self.assertEqual(len(result['files']), 3)
        with (self.root / 'comparison.csv').open(newline='') as stream:
            rows = list(csv.reader(stream))
        self.assertNotIn(b'\r\r\n', (self.root / 'comparison.csv').read_bytes())
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[1][0].startswith("'="))
        text = (self.root / 'comparison.md').read_text()
        self.assertIn('[未报告]', text); self.assertIn('zotero://open-pdf/', text)
        self.assertEqual(len(s.read(self.root / 'comparison.evidence.json')['sources']), 1)

    def test_matrix_rejects_missing_papers_foreign_claims_and_false_reported_status(self):
        proposal = copy.deepcopy(self.payload); proposal['claims'][0]['kind'] = 'proposal'
        jobs.accept(self.job, proposal)
        context = self.matrix_context(); result = self.matrix_result(context)
        with self.assertRaisesRegex(ValueError, 'relabelled'):
            matrix.render(context, result, self.root / 'proposal')
        result['rows'][0]['cells']['方法']['claim_ids'] = ['foreign-claim']
        with self.assertRaisesRegex(ValueError, 'outside its paper'):
            matrix.render(context, result, self.root / 'foreign')
        result['rows'] = []
        with self.assertRaisesRegex(ValueError, 'each selected paper'):
            matrix.render(context, result, self.root / 'missing')

    def test_review_and_matrix_detect_stale_reading_results(self):
        first = self.accept(); context = self.matrix_context()
        review_path = self.root / 'review-context.json'
        review.prepare([self.job], '', review_path)
        changed = dict(self.payload, claims=[dict(self.claim, statement='这里增加一项重要的方法解释修正，需要使已有综述上下文失效。')])
        jobs.revise(self.job, changed, first['revision'], '补充重要的方法实现条件')
        with self.assertRaisesRegex(ValueError, 'Evidence changed'):
            matrix.render(context, self.matrix_result(context), self.root / 'stale-matrix')
        corpus = s.read(review_path)
        with self.assertRaisesRegex(ValueError, 'Evidence changed'):
            review.render(corpus, {'fingerprint': corpus['fingerprint']}, self.root / 'stale-review.md')

    def test_native_highlight_geometry_and_read_only_preview(self):
        self.accept()
        fake_api = MagicMock()
        preview = native.publish(fake_api, self.job)
        self.assertTrue(preview['dry_run']); fake_api.local_write.assert_not_called()
        self.assertEqual(preview['count'], 1)
        item = preview['items'][0]
        self.assertEqual(item['parentItem'], 'ATTACH01')
        position = json.loads(item['annotationPosition'])
        self.assertEqual(position['pageIndex'], 0)
        self.assertTrue(680 < position['rects'][0][1] < 725)
        self.assertTrue(680 < position['rects'][0][3] < 725)
        self.assertEqual(item['annotationText'], self.block['text'])

    def test_native_repeat_preserves_user_edited_comment(self):
        self.accept()
        def create(path, values, **kwargs):
            return self.api.Response(200, {'Content-Type': 'application/json'}, json.dumps({'successful': {'0': {'key': values[0]['key']}}}))
        with patch.object(self.api, 'request', return_value=self.api.Response(404, {}, '')), patch.object(self.api, 'local_write', side_effect=create) as write:
            first = native.publish(self.api, self.job, yes=True)
        self.assertEqual(len(first['created']), 1)
        existing = {'data': {'itemType': 'annotation', 'parentItem': 'ATTACH01', 'tags': [{'tag': 'dsh-reader'}], 'annotationComment': 'my edited comment'}}
        with patch.object(self.api, 'request', return_value=self.api.Response(200, {'Content-Type': 'application/json'}, json.dumps(existing))), patch.object(self.api, 'local_write') as write:
            second = native.publish(self.api, self.job, yes=True)
        write.assert_not_called(); self.assertEqual(len(second['reused']), 1)

    def test_native_write_reports_object_level_failure_and_rejects_collision(self):
        self.accept()
        failure = self.api.Response(200, {'Content-Type': 'application/json'}, '{"failed":{"0":{"code":400}}}')
        with patch.object(self.api, 'request', return_value=self.api.Response(404, {}, '')), patch.object(self.api, 'local_write', return_value=failure):
            result = native.publish(self.api, self.job, yes=True)
        self.assertFalse(result['complete']); self.assertFalse(result['created'])
        other = self.api.Response(200, {'Content-Type': 'application/json'}, '{"data":{"itemType":"note"}}')
        with patch.object(self.api, 'request', return_value=other), patch.object(self.api, 'local_write') as write:
            with self.assertRaisesRegex(ValueError, 'collision'):
                native.publish(self.api, self.job, yes=True)
            write.assert_not_called()

    def test_native_rotation_mapping_and_cropped_pdf_guard(self):
        from pypdf import PdfReader
        page = PdfReader(self.source).pages[0]; page.rotate(90)
        rects = native.original_rects(page, {'height': 600}, [[100, 100, 200, 120]])
        self.assertEqual(rects, [[100.0, 100.0, 120.0, 200.0]])
        page.cropbox.lower_left = (10, 10)
        with self.assertRaisesRegex(ValueError, 'uncropped'):
            native.original_rects(page, {'height': 600}, [[100, 100, 200, 120]])

    def test_imported_user_annotations_are_not_paper_facts(self):
        records = [{'key': 'USER0001', 'data': {'itemType': 'annotation', 'annotationText': 'A <b>quoted</b> passage', 'annotationComment': 'Could this be reproduced?', 'annotationPageLabel': '2'}}]
        with patch.object(self.api, 'fetch_all', return_value=records):
            result = native.collect(self.api, self.job)
        self.assertEqual(result['annotations'][0]['kind'], 'user_annotation_not_paper_fact')
        self.assertEqual(result['annotations'][0]['quote'], 'A quoted passage')

    def test_native_links_are_clickable_in_notes_and_user_edits_remain_escaped(self):
        self.accept()
        _, items = native.plan(self.job)
        key = items[0]['key']
        s.write(self.directory / 'native-annotations.json', {'source_hash': self.manifest['source_hash'], 'created': [key], 'reused': []})
        text = jobs.markdown(self.manifest, self.document, [self.claim], {})
        self.assertIn('&annotation=' + key, text)
        line = next(line for line in text.splitlines() if '&annotation=' in line)
        html = jobs.zotero_link_html(line + ' <script>bad()</script>')
        self.assertIn('&amp;annotation=' + key, html)
        self.assertIn('<a href=', html)
        self.assertNotIn('<script>', html)

    def test_publishing_again_after_native_annotation_uses_new_note_content(self):
        self.accept()
        response = self.api.Response(200, {'Content-Type': 'application/json'}, '{"successful":{"0":{"key":"NOTE0001"}}}')
        with patch.object(self.api, 'local_write', return_value=response) as write:
            jobs.publish_note(self.api, self.job)
            _, items = native.plan(self.job)
            s.write(self.directory / 'native-annotations.json', {'source_hash': self.manifest['source_hash'], 'created': [items[0]['key']], 'reused': []})
            jobs.publish_note(self.api, self.job)
        self.assertEqual(write.call_count, 2)
        self.assertIn('&amp;annotation=', write.call_args.args[1][0]['note'])

    def test_numeric_audit_recognizes_chinese_adjacent_numbers(self):
        self.assertEqual(quality.numbers('准确率95%，被试1000人'), quality.numbers('Accuracy 0.95, subjects 1,000'))

    def test_local_api_header_casing_does_not_break_json_or_pagination(self):
        response = self.api.Response(200, {'content-type': 'application/json', 'total-results': '12'}, '{"items":3}')
        self.assertEqual(self.api.parse_body(response), {'items': 3})
        self.assertEqual(self.api.total_results(response), 12)

    def test_citation_chaining_labels_relations_without_inventing_support(self):
        def get(path, params, **kwargs):
            if params.get('select'):
                return {'results': [{'id': 'https://openalex.org/W100', 'title': 'Seed', 'referenced_works': ['https://openalex.org/W200']}]}
            return {'results': [{'id': 'https://openalex.org/W200', 'title': 'Candidate', 'publication_date': '2026-01-01', 'is_retracted': True}]}
        with patch.object(self.api, 'openalex_get', side_effect=get):
            result = discovery.discover(self.api, [self.job])
        row = result['recommendations'][0]
        self.assertEqual(len(row['relations']), 2)
        self.assertTrue(row['is_retracted']); self.assertTrue(row['needs_fulltext_review'])
        self.assertIn('不表示支持', row['caution'])
        self.assertEqual(row['evidence_level'], 'metadata_and_citation_relation')

    def test_ambiguous_seed_is_not_silently_selected(self):
        manifest = s.read(self.directory / 'manifest.json'); manifest['doi'] = ''
        s.write(self.directory / 'manifest.json', manifest)
        matches = [{'title': manifest['title'], 'id': 'W1'}, {'title': manifest['title'], 'id': 'W2'}]
        with patch.object(self.api, 'api_get', return_value={'data': {}}), patch.object(self.api, 'openalex_get', return_value={'results': matches}):
            result = discovery.discover(self.api, [self.job])
        self.assertFalse(result['seeds']); self.assertFalse(result['recommendations']); self.assertTrue(result['warnings'])

    def test_bilingual_markdown_is_reflowed_and_linked_without_pdf_rendering(self):
        self.accept()
        with patch.object(pdf, 'bilingual', side_effect=AssertionError('No heavy PDF rendering required')):
            result = jobs.export(self.job, self.root / 'exports')
        path = next(Path(p) for p in result['files'] if p.endswith('bilingual.md'))
        text = path.read_text()
        self.assertIn(self.block['text'], text); self.assertIn('中文译文', text)
        self.assertIn('zotero://open-pdf/', text)


if __name__ == '__main__': unittest.main()
