"""Action projections and local read protocol. Durable state is never a prompt."""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping

PROTOCOL = 'k10-v2-context-3.2.1'


def digest(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def public_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in packet.items() if not key.startswith('_local')}


def ref_key(ref):
    return (ref.get('documentId'), ref.get('revision'))


def project_packet(action: str, packet: Mapping[str, Any]) -> dict[str, Any]:
    if not packet.get('companyScope'):
        return dict(packet)
    result = dict(packet)
    scope = dict(packet['companyScope'])
    for key in ('companyProfiles', 'candidateCompanyCodes', 'profileSnapshotId', 'profileStatus', 'scopeRule', 'localProfileQuery'):
        if key in result:
            value = result.pop(key)
            if key == 'candidateCompanyCodes':
                value = sorted(set(scope.get(key, [])) | set(value))
            elif key == 'companyProfiles':
                value = list({row['identity']['ts_code']: row for row in [*scope.get(key, []), *value]}.values())
            scope[key] = value
    pool = scope.pop('fixedPool', [])
    scope.pop('localProfileQuery', None)
    # Identity and provenance remain local; a candidate summary is sufficient
    # for planning a source query or reading a new source paragraph.
    if action in {'plan_queries', 'assess_evidence'}:
        scope['companyProfiles'] = [{key: row[key] for key in
            ('identity', 'summary', 'review_status', 'profileContentSha256') if key in row}
            for row in scope.get('companyProfiles', [])]
    result['companyScope'] = scope
    result['contextProtocol'] = PROTOCOL
    result['_localState'] = {'claims': packet.get('claims', []), 'questions': packet.get('questions', []),
        'fulltextRequests': packet.get('fulltextRequests', []), 'fixedPool': pool,
        'evidenceUpdates': packet.get('evidenceUpdates', []), 'queryPaths': packet.get('queryPaths', []),
        'pathDependencies': packet.get('_localPathDependencies', {})}
    questions = list(packet.get('questions', []))
    if action == 'plan_queries':
        questions = [q for q in questions if q['state'] == 'open']
    elif action == 'assess_evidence':
        affected = set(packet.get('affectedQuestionIds', []))
        questions = [q for q in questions if q['questionId'] in affected] if affected else [q for q in questions if q['state'] == 'open']
    if action in {'close_research', 'compare_companies'}:
        questions = [q if q['state'] == 'open' else {key: q[key] for key in
            ('questionId', 'claimIds', 'state', 'missingEvidence', 'resumeCondition', 'knownEvidence') if key in q} for q in questions]
    result['questions'] = questions
    if action in {'plan_queries', 'assess_evidence'} and questions:
        codes = {code for q in questions for code in q.get('companyCodes', [])}
        scope['companyProfiles'] = [row for row in scope.get('companyProfiles', []) if row['identity']['ts_code'] in codes]
        scope['candidateCompanyCodes'] = [code for code in scope.get('candidateCompanyCodes', []) if code in codes]
    if action in {'plan_queries', 'assess_evidence'}:
        ids = {claim for q in questions for claim in q['claimIds']}
        result['claims'] = [claim for claim in packet.get('claims', []) if claim['claimId'] in ids]
    ids = {claim['claimId'] for claim in result.get('claims', [])}
    result['evidenceUpdates'] = [row for row in packet.get('evidenceUpdates', []) if row['claimId'] in ids]
    result['queryPaths'] = [{key: row[key] for key in ('pathId', 'questionId', 'state', 'resultSummary', 'targetSource', 'intent') if key in row}
        for row in packet.get('queryPaths', []) if row['questionId'] in {q['questionId'] for q in questions}]
    if action in {'close_research', 'compare_companies', 'plan_gaps'}:
        # Only material remaining paths affect closure; completed route history
        # stays available through its question, not in every comparison.
        result['queryPaths'] = [row for row in result['queryPaths'] if row['state'] == 'planned']
    if action == 'close_research':
        result['pathAvailability'] = []
        for question in questions:
            paths = [row for row in packet.get('queryPaths', []) if row['questionId'] == question['questionId'] and row['state'] != 'planned']
            if paths:
                last = max(paths, key=lambda row: packet.get('_localPathOrder', {}).get(row['pathId'], 0))
                result['pathAvailability'].append({'questionId': question['questionId'],
                    'lastAttempt': {key: last.get(key) for key in ('query', 'intent', 'targetSource', 'state', 'resultSummary')},
                    'attemptedRoutesSha256': digest(sorted(digest({key: row.get(key) for key in ('query', 'intent', 'targetSource', 'state', 'resultSummary')}) for row in paths))})
    result['fulltextRequests'] = [row for row in packet.get('fulltextRequests', []) if row['state'] in {'requested', 'admitted'}]
    result.pop('toolOutcomes', None)
    reusable = result.pop('reusableSourceEvidence', {})
    shared = reusable.get('claims', [])
    own = {(c.get('text'), ref_key(c['sourceRef'])) for c in result.get('claims', [])}
    shared = [c for c in shared if (c.get('text'), ref_key(c['sourceRef'])) not in own]
    result['reusableSourceEvidence'] = {'claims': shared, 'companyRelations': reusable.get('companyRelations', []), 'isolated': reusable.get('isolated', [])} if action in {'plan_gaps', 'close_research', 'compare_companies'} else {'claims': []}
    relevant_refs = {ref_key(c['sourceRef']) for c in result.get('claims', [])}
    relevant_refs.update(ref_key(row['sourceRef']) for row in result['evidenceUpdates'])
    relevant_refs.update(ref_key(ref) for q in questions for ref in q.get('knownEvidence', []))
    relevant_refs.update(ref_key(ref) for ref in packet.get('newEvidenceRefs', []))
    cards=[]
    for card in packet.get('evidenceCards', []):
        if action in {'plan_queries', 'assess_evidence'} and ref_key(card) not in relevant_refs:
            continue
        # A claim is represented exactly once, in claims. A naked source ID is
        # insufficient: cards with neither visible claim nor excerpt stay hidden.
        claim_ids = [c['claimId'] for c in [*result.get('claims', []), *result['reusableSourceEvidence']['claims']] if ref_key(c['sourceRef']) == ref_key(card)]
        if card.get('excerpt') or claim_ids:
            cards.append({**{key: value for key, value in card.items() if key != 'sourceStatements'}, 'claimIds': claim_ids})
    result['evidenceCards'] = cards
    visible = {ref_key(card) for card in cards}
    visible.update(ref_key(doc) for doc in packet.get('fullTextDocuments', []) if doc.get('eligibleAtNewsCutoff'))
    for item in packet.get('contextResults', []):
        value = item.get('value')
        if item.get('status') == 'found' and isinstance(value, dict):
            if value.get('eligibleAtNewsCutoff'):
                visible.add(ref_key(value['sourceRef']))
            if item['request']['kind'] == 'claim' and 'sourceRef' in value:
                visible.add(ref_key(value['sourceRef']))
    result['allowedEvidenceRefs'] = [ref for ref in packet['allowedEvidenceRefs'] if ref_key(ref) in visible]
    result['contextReadCapabilities'] = ['company_search', 'company_fields', 'claim', 'question', 'source']
    result['visibleContext'] = {'claimIds': sorted(ids), 'sourceRefs': result['allowedEvidenceRefs'],
        'companyFields': [{'companyCode': row['identity']['ts_code'], 'fields': sorted(row),
                           'contentSha256': digest(row)} for row in scope.get('companyProfiles', [])]}
    return result


def read_context(request: Mapping[str, Any], *, state, documents, binding, eligible_refs):
    """Resolve only already stored versions; unknown locators return no evidence."""
    kind = request.get('kind')
    if not isinstance(request.get('purpose'), str) or not request['purpose'].strip():
        raise ValueError('context purpose required')
    question_id = request.get('questionId')
    if question_id is not None and question_id not in {q['questionId'] for q in state['questions']}:
        raise ValueError('unknown context question')
    identity = {key: value for key, value in request.items() if key != 'purpose'}
    value = None
    if kind in {'claim', 'question'}:
        collection, key = ('claims', 'claimId') if kind == 'claim' else ('questions', 'questionId')
        value = next((row for row in state[collection] if row[key] == request.get('id')), None)
    elif kind == 'company_search' and binding:
        from .v2_profiles import retrieve_company_context
        value = retrieve_company_context(db_path=binding[0], profiles_id=binding[1], query=request.get('query', ''))
        value.pop('fixedPool', None)
    elif kind == 'company_fields' and binding:
        from .v2_profiles import read_profiles
        rows = read_profiles(db_path=binding[0], profiles_id=binding[1], codes=[request.get('companyCode')])
        if rows and isinstance(request.get('fields'), list):
            profile = rows[0]
            fields = request['fields']
            if all(isinstance(key, str) and key in profile and key not in {'raw_evidence_file'} for key in fields):
                selected = {key: profile[key] for key in fields}
                def references(item):
                    if isinstance(item, dict):
                        for key, child in item.items():
                            if key in {'source_ref', 'source_refs'}:
                                yield from ([child] if isinstance(child, str) else child)
                            else:
                                yield from references(child)
                    elif isinstance(item, list):
                        for child in item:
                            yield from references(child)
                refs = set(references(selected))
                sources = [] if 'sources' in fields else [source for source in profile['sources']
                    if any(ref == source['source_id'] or ref.startswith(source['source_id'] + '.') for ref in refs)]
                value = {'profileSnapshotId': binding[1], 'identity': profile['identity'],
                    'review_status': profile['review_status'], 'fields': selected,
                    'sources': sources, 'contentSha256': digest(profile)}
    elif kind == 'source':
        ref = request.get('sourceRef') or {}
        doc = next((doc for key, doc in documents.items() if (key.document_id, key.revision) == ref_key(ref)), None)
        location = request.get('location')
        if doc is not None:
            if location == 'excerpt':
                value = doc.excerpt
            elif isinstance(location, str) and location.startswith('paragraph:'):
                try:
                    index = int(location.split(':', 1)[1]) - 1
                    paragraphs = (doc.analysis_text or doc.original_text or '').splitlines()
                    value = paragraphs[index] if 0 <= index < len(paragraphs) else None
                except ValueError:
                    pass
            if value:
                value = {'sourceRef': ref, 'location': location, 'text': value,
                    'publishedAt': doc.published_at, 'fetchedAt': doc.fetched_at,
                    'eligibleAtNewsCutoff': ref_key(ref) in eligible_refs,
                    'contentVersionAtCutoff': doc.metadata.get('contentVersionAtCutoff')}
    return {'request': identity, 'status': 'found' if value is not None else 'unknown_reference',
            'value': value, 'contentSha256': digest(value)}


def normalized_text(value):
    import re
    return re.sub(r'[\W_]+', '', str(value).casefold())


def question_identity(question):
    return digest([sorted(question.get('claimIds', [])), sorted(question.get('companyCodes', [])),
        normalized_text(question.get('question', '')), normalized_text(question.get('supportCondition', '')),
        normalized_text(question.get('refuteCondition', ''))])


def collapse_repeated_work(value, packet):
    """Collapse only program-provable identity changes, preserving new facts."""
    if not packet.get('contextProtocol'):
        return value
    local = packet.get('_localState', packet)
    originals = {question_identity(q): q for q in local.get('questions', [])}
    aliases, questions = {}, []
    for q in value.get('questions', []):
        identity = question_identity(q)
        old = originals.get(identity)
        if old and old['questionId'] != q['questionId']:
            aliases[q['questionId']] = old['questionId']
            # A fresh ID cannot reopen an already handled question.
            if old['state'] != 'open':
                continue
            q = {**q, 'questionId': old['questionId'], 'question': old['question'], 'claimIds': old['claimIds']}
        elif old is None:
            originals[identity] = q
        if q not in questions:
            questions.append(q)
    known = {q['questionId']: q for q in [*local.get('questions', []), *questions]}
    def route(row):
        q = known.get(row.get('questionId'), {})
        dependency = local.get('pathDependencies', {}).get(row.get('pathId')) if row.get('state') != 'planned' else None
        return route_identity(row, q, dependency)
    used = {route(row) for row in local.get('queryPaths', []) if row['state'] != 'planned'}
    paths=[]
    for path in value.get('queryPaths', []):
        path = {**path, 'questionId': aliases.get(path.get('questionId'), path.get('questionId'))}
        key = route(path)
        if key not in used:
            paths.append(path)
            used.add(key)
    handled = {(row['questionId'], ref_key(row['sourceRef'])) for row in local.get('fulltextRequests', [])
        if row.get('state') in {'fulfilled', 'rejected'}}
    requests = [row for row in value.get('fulltextRequests', [])
        if (row.get('questionId'), ref_key(row.get('sourceRef', {}))) not in handled]
    return {**value, **({'questions': questions} if 'questions' in value else {}),
        **({'queryPaths': paths} if 'queryPaths' in value else {}),
        **({'fulltextRequests': requests} if 'fulltextRequests' in value else {})}


def question_dependency(question):
    return {'questionIdentity': question_identity(question),
            'knownEvidence': sorted((ref_key(ref) for ref in question.get('knownEvidence', [])))}


def route_identity(path, question, dependency=None):
    return digest([dependency or question_dependency(question),
        normalized_query(path.get('query')), normalized_text(path.get('intent')), normalized_text(path.get('targetSource'))])


def normalized_query(value):
    # Preserve term boundaries and search operators; punctuation can carry
    # a negation or an exact-phrase requirement.
    return ' '.join(str(value or '').casefold().split())
