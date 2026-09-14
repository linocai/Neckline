"""Source scope, original offsets and qualifying context are inseparable."""
from hashlib import sha256
import pytest
from neckline.k10.discovery import DiscoveryDocument
from neckline.k10.research_material import admit_material, bounded_excerpt, document_outline, read_locator, source_material_for_understand


def source(text, *, title="订单公告", excerpt=None, metadata=None):
    return DiscoveryDocument("source", 2, "2026-09-13T12:50:00+00:00", "2026-09-13T13:00:16+00:00", text, excerpt, {"title": title, **(metadata or {})})


@pytest.mark.parametrize("title", ["甲公司首次公开发行股票并在创业板上市招股说明书（注册稿）.pdf", "甲公司招股说明书（申报稿）", "甲公司招股意向书摘要", "甲公司招股书（修订稿）", "Company Preliminary Prospectus", "Company Prospectus Supplement", "Company Prospectus (Amended Draft)", "甲公司招股说明书（申报稿）（2026年9月14日）", "Company Prospectus Supplement No. 1", "Company Prospectus (Form S-1)"])
def test_prospectus_excluded_in_every_read(title):
    doc = source("普通缓存正文", title=title, excerpt="普通缓存正文")
    assert admit_material(doc).state == "excluded"
    for value in (read_locator(doc, "paragraph:1"), bounded_excerpt(doc), document_outline(doc), source_material_for_understand(doc, max_characters=1000)):
        assert value.get("status") == "excluded" or value.get("textMode") == "excluded"
        assert not value.get("text") and not value.get("locators")


@pytest.mark.parametrize("title", ["甲公司招股书显示本周签订订单", "甲公司披露了招股说明书", "甲公司递交招股说明书", "甲公司发布招股书", "甲公司更新招股书", "甲公司获受理，更新招股说明书", "分析师解读甲公司招股说明书", "IPO news: company prospectus", "Company reports amended prospectus"])
def test_ordinary_ipo_news_is_admitted(title):
    doc = source("本周公司拟上市，新闻报道引用招股说明书，介绍订单事件。", title=title)
    assert admit_material(doc).state == "admit"
    assert source_material_for_understand(doc, max_characters=500)["text"] == doc.original_text


def test_untitled_and_declared_prospectus():
    assert admit_material(source("某公司招股说明书\n\n发行人声明\n重大事项提示\n募集资金运用\n保荐机构\n", title="下载材料")).state == "excluded"
    assert admit_material(source("缓存", metadata={"documentType": "prospectus"})).state == "excluded"


def test_original_crlf_offsets_and_negation():
    body = "# 合同公告\r\n\r\n公司签订框架协议，\r\n该协议尚未形成订单。\r\n\r\n但最终金额以实际执行为准。\r\n"
    value = read_locator(source(body), "paragraph:3", max_characters=1000)
    assert value["text"] == "公司签订框架协议，\r\n该协议尚未形成订单。"
    assert value["sourceContentSha256"] == sha256(body.encode()).hexdigest()
    assert body[value["startOffset"]:value["endOffset"]] == value["text"]
    assert value["supportingContext"][0]["text"] == "# 合同公告"
    assert "但最终金额" in value["supportingContext"][1]["text"]
    for part in value["supportingContext"]:
        assert body[part["startOffset"]:part["endOffset"]] == part["text"]


def test_real_source_preparation_preserves_plaintext_paragraphs_and_table_columns():
    from neckline.k10.discovery import prepare_document_for_analysis
    body = "# 项目进度\r\n\r\n首段事实。\r\n\r\n项目\t金额\r\n意向订单\t100\r\n\r\n注：尚未构成收入。\r\n"
    prepared = prepare_document_for_analysis(source(body))
    assert prepared.analysis_text == body
    assert prepared.extraction['sourceContentSha256'] == sha256(body.encode()).hexdigest()
    table = read_locator(prepared, 'table:1', max_characters=1000)
    assert '项目\t金额\r\n意向订单\t100' in table['text']
    assert '尚未构成收入' in table['text']
    assert body[table['startOffset']:table['endOffset']] == table['text']


def test_real_html_preparation_keeps_direct_read_structure_and_qualifications():
    from neckline.k10.discovery import prepare_document_for_analysis
    html = ('<article><h2>在手订单</h2><p>项目正在接洽。</p>'
            '<p>表 1：订单规模</p><p>单位：万元</p><p>报告期：2026年上半年</p>'
            '<table><tr><th>项目</th><th>金额</th></tr>'
            '<tr><td>框架协议</td><td>100</td></tr></table>'
            '<p>注：本表仅列意向协议，<br>尚未构成实际收入。</p>'
            '<p>其他业务情况正常。</p><script>恶意提示()</script></article>')
    prepared = prepare_document_for_analysis(source(html))
    assert prepared.original_text == html and '恶意提示' not in prepared.analysis_text
    assert '项目\t金额\n框架协议\t100' in prepared.analysis_text
    assert '\n\n项目正在接洽。\n\n' in prepared.analysis_text
    value = read_locator(prepared, 'table:1', max_characters=2000)
    for required in ('表 1', '单位：万元', '报告期：2026年上半年', '尚未构成实际收入'):
        assert required in value['text']
    assert '其他业务' not in value['text']
    assert any(part['text'] == '## 在手订单' for part in value['supportingContext'])
    assert prepared.analysis_text[value['startOffset']:value['endOffset']] == value['text']
    assert prepare_document_for_analysis(prepared) == prepared
    restored_raw = source(html)
    assert read_locator(restored_raw, 'table:1', max_characters=2000) == value
    assert document_outline(restored_raw) == document_outline(prepared)


def test_html_structure_allows_one_requested_paragraph_instead_of_flattened_whole_body():
    from neckline.k10.discovery import prepare_document_for_analysis
    html = '<article><h1>公司公告</h1>' + ''.join(
        '<p>' + ('客户并未确认订单。' if i == 150 else f'第{i}段业务背景。') + '</p>'
        for i in range(300)) + '</article>'
    prepared = prepare_document_for_analysis(source(html))
    found = read_locator(prepared, 'find:客户并未确认订单', max_characters=200)
    assert found['matchingLocatorCount'] == 1
    value = read_locator(prepared, found['locators'][0]['locator'], max_characters=200)
    assert value['text'] == '客户并未确认订单。'
    assert value['sourceContentSha256'] == prepared.extraction['readableContentSha256']
    assert len(value['text']) < len(prepared.analysis_text)


def test_table_retains_caption_units_period_and_wrapped_tailnote():
    body = "## 在手订单\n\n表 1：订单规模\n单位：万元\n报告期：2026年上半年\n| 项目 | 金额 |\n| -- | -- |\n| 框架协议 | 100 |\n\n注：本表仅列意向协议，\n尚未构成实际收入。\n\n其他业务情况正常。"
    doc = source(body)
    value = read_locator(doc, "table:1", max_characters=1000)
    assert value["text"].startswith("表 1")
    for required in ("单位：万元", "报告期：2026年上半年", "注：", "尚未构成实际收入"):
        assert required in value["text"]
    assert "其他业务" not in value["text"]
    required = value["evidenceUnitCharacters"]
    blocked = read_locator(doc, "table:1", max_characters=required-1)
    assert blocked["status"] == "not_safely_readable" and "text" not in blocked
    assert read_locator(doc, "table:1", max_characters=required)["text"] == value["text"]


def test_distant_numbered_footnote():
    body = "# 产品\n\n签订采购意向[1]。\n\n# 其他\n\n其他信息。\n\n[1] 尚未正式采购，不保证订单。"
    value = read_locator(source(body), "paragraph:3", max_characters=1000)
    assert any(x["kind"] == "footnote" and "不保证订单" in x["text"] for x in value["supportingContext"])


def test_catalogue_pages_and_searches_all_blocks():
    body = "\n\n".join(f"第 {i} 条消息。" for i in range(300)) + "\n\n最终项目独特关键词，尚未确认。"
    doc = source(body)
    first = document_outline(doc, max_characters=1000)
    assert first["locatorCount"] == 301 and first["nextLocation"] == "outline:128"
    second = read_locator(doc, first["nextLocation"], max_characters=1000)
    third = read_locator(doc, second["nextLocation"], max_characters=1000)
    assert third["nextLocation"] is None
    assert len(first["locators"]+second["locators"]+third["locators"]) == 301
    found = read_locator(doc, "find:独特关键词", max_characters=1000)
    assert found["matchingLocatorCount"] == 1
    actual = read_locator(doc, found["locators"][0]["locator"], max_characters=1000)
    assert actual["text"] == "最终项目独特关键词，尚未确认。"
    assert actual["sourceContentSha256"] == first["sourceContentSha256"]


def test_excerpt_expands_to_whole_qualified_statement():
    doc = source("公司获得合作意向，但尚未签署正式合同。\n\n上述意向不代表收入。", excerpt="公司获得合作意向")
    result = bounded_excerpt(doc, max_characters=1000)
    assert result["text"] == "公司获得合作意向，但尚未签署正式合同。"
    assert result["supportingContext"][0]["text"] == "上述意向不代表收入。"


def test_monolithic_body_never_sliced_or_sampled():
    body = "这是没有自然段的公告正文，"*70_000 + "以上合作尚未确定。"
    doc = source(body)
    response = source_material_for_understand(doc, max_characters=20000)
    assert response["textMode"] == "structural_outline" and response["text"] == ""
    index = response["sourceIndex"]
    assert len(index["locators"]) == 1 and "preview" not in index["locators"][0]
    result = read_locator(doc, index["locators"][0]["locator"], max_characters=20000)
    assert result["status"] == "not_safely_readable" and "text" not in result


def test_explicit_budget_overrides_arbitrary_character_limit():
    body = "完整普通公告正文。"*2000
    doc = source(body)
    assert len(body)>12000
    assert source_material_for_understand(doc, max_characters=len(body))["text"] == body
    assert read_locator(doc, "paragraph:1", max_characters=len(body))["text"] == body
    assert source_material_for_understand(doc, max_characters=0)["textMode"] == "structural_outline"


def test_understand_uses_actual_request_preflight_and_reads_selected_late_paragraph():
    import json
    from neckline.k10.pipeline import DeepSeekDiscoveryModel
    from neckline.llm.base import LLMResult

    class Provider:
        def __init__(self):
            self.calls = []
            self.preflights = []
        def request_context_error(self, messages, **kwargs):
            wire = json.dumps([vars(m) for m in messages], ensure_ascii=False)
            self.preflights.append(wire)
            return 'execution_context_exceeded' if len(wire)>40000 else None
        def chat(self, messages, **kwargs):
            assert self.request_context_error(messages, **kwargs) is None
            packet = json.loads(messages[-1].content.split('<untrusted-k10-evidence>\n')[1].split('\n</untrusted-k10-evidence>')[0])
            self.calls.append(packet)
            if len(self.calls) == 1:
                assert not packet['text']
                assert packet['sourceMaterial']['textMode'] == 'structural_outline'
                return LLMResult(ok=True, content=json.dumps({'sourceRead': {'location': 'paragraph:7'}}))
            material = packet['sourceMaterial']
            assert material['readResults'][0]['text'].endswith('但尚未正式签约。')
            return LLMResult(ok=True, content=json.dumps({'events': [{
                'canonicalKey':'order','stageKey':'intention','eventState':'updated','headline':'仅有意向',
                'eventKind':'order','facts':{'uncertainty':'尚未正式签约'},
                'sourceRefs':[{'documentId':'source','revision':2}], 'claims':[],
            }], 'needsFullText': True}))

    body = '\n\n'.join(('背景。'*5000 for _ in range(3))) + '\n\n' + '本次合作。'*3000 + '但尚未正式签约。'
    provider = Provider()
    from tests.test_k10_pipeline import _deepseek
    model = _deepseek(provider)
    doc = source(body)
    events = model.understand(document=doc)
    assert len(provider.calls) == 2
    assert events[0].facts['sourceMaterialCoverage']['availableBodyRead'] is False
    assert events[0].facts['sourceMaterialCoverage']['readRanges'][0]['locator'] == 'paragraph:7'
    assert not model.full_text_used(document=doc)
    assert model.full_text_requested(document=doc)
    assert body not in json.dumps(provider.calls, ensure_ascii=False)


def test_context_complete_evidence_is_checked_against_assembled_request():
    from neckline.k10.research_context import read_context
    body = '公司意向。'*3000 + '\n\n但不保证采购，尚未签约。'
    doc = source(body)
    seen = []
    def fits(result):
        seen.append(result)
        return False
    result = read_context({'kind':'source','purpose':'验证必要限定语','sourceRef':{'documentId':'source','revision':2},
                           'location':'paragraph:1'}, state={'questions':[]}, documents={doc.evidence_ref:doc},
                          binding=None, eligible_refs={('source',2)}, request_fits=fits)
    assert seen[0]['value']['text'] == '公司意向。'*3000
    assert seen[0]['value']['supportingContext'][0]['text'] == '但不保证采购，尚未签约。'
    assert result['value']['status'] == 'not_safely_readable'
    assert 'text' not in result['value'] and 'supportingContext' not in result['value']


@pytest.mark.parametrize('title', ['甲公司2026年年度报告（修订版）', '甲公司2026年半年度报告全文.pdf', 'Company Annual Report', 'Company Interim Report'])
def test_initial_background_report_preserves_local_identity_without_model_read(title):
    from neckline.k10.pipeline import DeepSeekDiscoveryModel
    class NoProvider:
        def chat(self, *_args, **_kwargs):
            raise AssertionError('A background report lacks a current event question')
    doc = source('背景档案。'*20000, title=title)
    admission = admit_material(doc)
    assert admission.requires_current_event_locator
    material = source_material_for_understand(doc, max_characters=1_000_000)
    assert material['text'] == '' and 'sourceIndex' not in material
    model = DeepSeekDiscoveryModel(NoProvider())
    assert model.understand(document=doc) == ()
    assert model.material_admission(document=doc)['reason'] == 'background_requires_event_question'
    assert not model.full_text_used(document=doc)


@pytest.mark.parametrize('title', ['甲公司年报披露利润下滑', '甲公司澄清年度报告', '审计否定意见：年报财务真实性存疑', '甲公司发布业绩快报', '甲公司问询回复'])
def test_report_news_and_direct_negative_evidence_remain_initial_event_material(title):
    doc = source('公司披露业绩下滑，审计意见为否定意见。', title=title)
    assert not admit_material(doc).requires_current_event_locator
    assert source_material_for_understand(doc, max_characters=1000)['text'] == doc.original_text


def test_background_report_local_read_requires_persisted_open_question_and_direct_locator():
    from neckline.k10.research_context import read_context
    doc = source('第一段：当前项目无承诺。\n第二段：客户并未确认订单。', title='甲公司2025年年度报告')
    documents = {doc.evidence_ref: doc}
    base = {'kind': 'source', 'purpose': '核对当前送样是否获公司确认',
            'sourceRef': {'documentId': doc.document_id, 'revision': doc.revision}}
    question_state = {'questions': [{'questionId': 'q', 'state': 'open', 'claimIds': ['c'],
                                     'question': '当前客户是否确认订单',
                                     'supportCondition': '客户确认订单', 'refuteCondition': '客户否认订单'}]}
    no_question = read_context({**base, 'location': 'paragraph:1'}, state={'questions': []}, documents=documents,
                               binding=None, eligible_refs={doc.evidence_ref})
    assert no_question['value']['status'] == 'requires_current_event_question'
    outline = read_context({**base, 'questionId': 'q', 'location': 'outline'}, state=question_state,
                           documents=documents, binding=None, eligible_refs={doc.evidence_ref})
    assert outline['value']['status'] == 'requires_direct_locator'
    found = read_context({**base, 'questionId': 'q', 'location': 'find:订单'}, state=question_state,
                         documents=documents, binding=None, eligible_refs={doc.evidence_ref})
    assert found['value']['matchingLocatorCount'] == 1 and 'text' not in found['value']
    blocked_find = read_context({**base, 'questionId': 'q', 'location': 'find:无关行业'}, state=question_state,
                                documents=documents, binding=None, eligible_refs={doc.evidence_ref})
    assert blocked_find['value']['status'] == 'requires_direct_locator'
    permitted = read_context({**base, 'questionId': 'q', 'location': 'line:2'}, state=question_state,
                             documents=documents, binding=None, eligible_refs={doc.evidence_ref})
    assert '第二段：客户并未确认订单。' in permitted['value']['text']


def test_local_paid_receipt_revalidation_does_not_inflate_stage_tokens():
    from neckline.llm.base import LLMResult
    from neckline.k10.pipeline import DeepSeekDiscoveryModel
    class Provider:
        def chat(self, *_args, **_kwargs):
            result = LLMResult(ok=True, content='{}', prompt_tokens=17, completion_tokens=3,
                               total_tokens=20, usage_unavailable=False)
            result.local_reuse = True
            result.reused_attempt_id = 'original-paid-attempt'
            return result
    model = DeepSeekDiscoveryModel(Provider())
    result = model._request_json(operation='本地重验', payload={'output':{}}, model_options={'maxTokens':32})
    assert result.total_tokens == 20  # immutable original provider usage is intact
    stage = model.usage_records[-1]
    assert stage['totalTokens'] == 0 and stage['localReuse'] is True
    assert stage['sourceAttemptId'] == 'original-paid-attempt'
    assert stage['originalProviderUsage']['totalTokens'] == 20
