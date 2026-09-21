"""Action projections and local read protocol. Durable state is never a prompt."""
from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from .research_material import (INDEX_VERSION, MAX_FRAGMENT_CHARACTERS, bounded_excerpt, document_outline,
    read_locator, requires_current_event_locator, scoped_find, direct_location, resize_catalogue, catalogue_restart_location)
from .research_navigation import NAVIGATION_VERSION, navigation_view, sina_source

PROTOCOL = 'k10-v2-context-3.3.0-b70'


def digest(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def canonical_body_identity(source: Mapping[str, Any] | Any) -> str | None:
    """Hash the source body only, never URL, fetch metadata, or row version.

    ``k10_source_document_versions.content_sha256`` identifies a stored source
    revision and can legitimately change when a URL or provider metadata
    changes.  Research route identity instead needs to know whether the
    evidence text it would show is new.  The original body is authoritative;
    a source without one uses its stored excerpt.  Normalising line endings
    removes transport-only variation without collapsing meaningful content.
    """
    def value(*names: str) -> str | None:
        for name in names:
            candidate = source.get(name) if isinstance(source, Mapping) else getattr(source, name, None)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.replace("\r\n", "\n").replace("\r", "\n").strip()
        return None
    body = value("original_text", "originalText") or value("excerpt")
    return digest({"body": body}) if body is not None else None


def public_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in packet.items() if not key.startswith('_local')}


def ref_key(ref):
    return (ref.get('documentId'), ref.get('revision'))


def _bounded_visible_text(value: object) -> tuple[str | None, str | None]:
    if not isinstance(value, str):
        return None, None
    if len(value) <= MAX_FRAGMENT_CHARACTERS:
        return value, None
    return None, digest(value)


def _profile_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    """Expose identity and a field catalogue; field values require local read."""
    allowed = ('identity', 'review_status', 'compiled_at', 'profileContentSha256')
    projected = {key: row[key] for key in allowed if key in row}
    summary, summary_hash = _bounded_visible_text(row.get('summary'))
    if summary is not None:
        projected['summary'] = summary
    elif summary_hash is not None:
        projected.update({'summaryUnavailable': True, 'summarySha256': summary_hash})
    identity = row.get('identity') if isinstance(row.get('identity'), Mapping) else {}
    code = identity.get('ts_code')
    # Runtime and final request assembly both project. Do not replace the
    # original visible field catalogue with a catalogue of the catalogue.
    manifests = list(row['fieldManifest']) if isinstance(row.get('fieldManifest'), list) else []
    if 'fieldManifest' not in row:
        for key, value in row.items():
            if key in {*allowed, 'sources', 'fieldRefs', 'retrieval', 'raw_evidence_file', 'companyCode'}:
                continue
            manifests.append({'field': key, 'contentSha256': digest(value),
                              'kind': type(value).__name__})
    projected['fieldManifest'] = sorted(manifests, key=lambda item: item['field'])
    if isinstance(code, str):
        projected['companyCode'] = code
    return projected


def company_question_scope_error(request: Mapping[str, Any], packet: Mapping[str, Any] | None) -> str | None:
    if not isinstance(packet, Mapping):
        return 'outside_company_scope'
    questions = packet.get('_localState', packet).get('questions', [])
    question_id = request.get('questionId')
    if (questions and question_id is None) or (question_id is not None and
            not any(q.get('questionId') == question_id for q in questions)):
        return 'outside_company_scope'
    return None


def company_field_scope_error(request: Mapping[str, Any], packet: Mapping[str, Any] | None) -> str | None:
    """Authorize local fields from what this action actually showed, not the DB."""
    reason = company_question_scope_error(request, packet)
    if reason:
        return reason
    question_id = request.get('questionId')
    questions = packet.get('_localState', packet).get('questions', [])
    question = next((q for q in questions if q.get('questionId') == question_id), None)
    if question_id is not None and question is None:
        return 'outside_company_scope'
    scope = packet.get('companyScope') or {}
    code = request.get('companyCode')
    visible = [row for row in scope.get('companyProfiles', [])
        if row.get('identity', {}).get('ts_code') == code]
    if question is not None and code not in question.get('companyCodes', []):
        visible = []
    # A local search can reveal a new candidate, but only its actual returned
    # manifest in the same question scope grants subsequent field access.
    for result in packet.get('contextResults', []):
        identity, value = result.get('request', {}), result.get('value') or {}
        if (identity.get('kind') == 'company_search' and identity.get('questionId') == question_id
                and result.get('status') == 'found'
                and value.get('profileSnapshotId') == scope.get('profileSnapshotId')):
            visible.extend(row for row in value.get('companyProfiles', [])
                if row.get('identity', {}).get('ts_code') == code)
    if not visible:
        return 'outside_company_scope'
    fields = request.get('fields')
    allowed = {entry.get('field') for row in visible for entry in row.get('fieldManifest', [])}
    allowed.update(key for row in visible for key in ('identity', 'review_status', 'compiled_at') if key in row)
    if (not isinstance(fields, list) or not fields or any(not isinstance(key, str) or key not in allowed
            or key in {'raw_evidence_file', 'sources'} for key in fields)):
        return 'outside_company_field_manifest'
    return None


def _refine_company_fields(value, manifest=None):
    """Withhold an oversized complete field together with its qualifications."""
    if len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))) <= MAX_FRAGMENT_CHARACTERS:
        return value
    return {**{key: value[key] for key in ('profileSnapshotId', 'identity', 'review_status', 'contentSha256') if key in value},
        'status': 'not_safely_readable', 'needsFieldRefinement': True,
        'fieldManifest': manifest if manifest is not None else [
            {'field': key, 'contentSha256': digest(field), 'kind': type(field).__name__}
            for key, field in (value.get('fields') or {}).items()]}


def _safe_context_results(packet):
    """Recheck restored reads before any model request, including pending extras."""
    navigation_refs = set()
    for row in [row for key in ('evidenceCards', 'fullTextDocuments')
                for row in (packet[key] if isinstance(packet.get(key), list) else [])]:
        if not isinstance(row, Mapping):
            continue
        projection = row.get('sourceViewProjection')
        for key in ('excerpt', 'text', 'originalText', 'analysisText', 'body'):
            if isinstance(row.get(key), str):
                _view, found = navigation_view(row[key], enabled=sina_source(row))
                projection = found or projection
        if isinstance(projection, Mapping) and projection.get('version') == NAVIGATION_VERSION:
            navigation_refs.add(ref_key(row))
    safe = []
    for item in packet.get('contextResults', []):
        request, value = item.get('request', {}), item.get('value')
        reason = company_field_scope_error(request, {**packet, 'contextResults': safe}) if request.get('kind') == 'company_fields' else None
        if reason:
            item = {**item, 'value': {'status': reason}, 'contentSha256': digest({'status': reason})}
        elif request.get('kind') == 'company_search' and company_question_scope_error(request, packet):
            reduced = {'status': 'outside_company_scope'}
            item = {**item, 'value': reduced, 'contentSha256': digest(reduced)}
        elif request.get('kind') == 'company_fields' and isinstance(value, Mapping) and 'fields' in value:
            reduced = _refine_company_fields(value)
            item = {**item, 'value': reduced, 'contentSha256': digest(reduced)}
        elif request.get('kind') == 'source' and isinstance(value, Mapping):
            material_result = any(key in value for key in ('indexVersion', 'text', 'locators', 'locatorCount'))
            view_projection = value.get('sourceViewProjection')
            stale_navigation = (material_result and ref_key(request.get('sourceRef') or value.get('sourceRef') or {}) in navigation_refs
                                and (not isinstance(view_projection, Mapping) or view_projection.get('version') != NAVIGATION_VERSION))
            if (stale_navigation or material_result and value.get('indexVersion') != INDEX_VERSION or
                    isinstance(value.get('text'), str) and len(value['text']) + sum(
                        len(part.get('text', '')) for part in value.get('supportingContext', [])) > MAX_FRAGMENT_CHARACTERS):
                reduced = {'sourceRef': value.get('sourceRef'), 'status': 'requires_current_read_protocol',
                    'needsLocator': True, 'previousContentSha256': item.get('contentSha256')}
                restart = catalogue_restart_location(request.get('location'))
                if restart is not None:
                    reduced['restartLocation'] = restart
                item = {**item, 'value': reduced, 'contentSha256': digest(reduced)}
        safe.append(item)
    return safe


def _project_fulltext_document(row: Mapping[str, Any]) -> dict[str, Any]:
    """Never let a raw body hide in a packet field outside the read protocol."""
    projected = {key: value for key, value in row.items()
                 if key not in {'text', 'originalText', 'analysisText', 'body', 'excerpt'}}
    raw = next((row[key] for key in ('text', 'originalText', 'analysisText', 'body') if isinstance(row.get(key), str)), None)
    if raw is not None:
        projected.update({'needsLocator': True, 'contentSha256': digest(raw)})
        _view, projection = navigation_view(raw, enabled=sina_source(row))
        if projection:
            projected['sourceViewProjection'] = projection
    raw_excerpt = row.get('excerpt')
    if isinstance(raw_excerpt, str):
        raw_excerpt, projection = navigation_view(raw_excerpt, enabled=sina_source(row))
        if projection:
            projected['sourceViewProjection'] = projection
    excerpt, excerpt_hash = _bounded_visible_text(raw_excerpt)
    if excerpt is not None:
        projected['excerpt'] = excerpt
    elif excerpt_hash is not None:
        projected.update({'excerptUnavailable': True, 'excerptSha256': excerpt_hash})
    try:
        if len(json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(',', ':'))) > MAX_FRAGMENT_CHARACTERS:
            # An oversized outline must be refined locally as well; replacing
            # it with a hash is safer than moving the same whole document into
            # another packet field.
            projected = {key: value for key, value in projected.items() if key not in {'outline', 'locators'}}
            projected.update({'needsLocator': True, 'outlineUnavailable': True,
                              'contentSha256': projected.get('contentSha256') or digest(row)})
    except (TypeError, ValueError):
        return {'needsLocator': True, 'contentSha256': digest(str(row))}
    return projected


def project_packet(action: str, packet: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(packet)
    # B78 asks for one direct event judgment.  It receives the already
    # prepared claims, relevant evidence and bounded company data together;
    # it is not a disguised plan/assess/close projection.
    direct_round = action == 'research_round'
    planning = action in {'plan_queries', 'plan_research'}
    deciding = action in {'assess_evidence', 'assess_and_decide'}
    closing = action in {'close_research', 'assess_and_decide'}
    scope = dict(packet['companyScope']) if isinstance(packet.get('companyScope'), Mapping) else {}
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
    if planning or deciding or direct_round:
        scope['companyProfiles'] = [_profile_projection(row) for row in scope.get('companyProfiles', [])]
    elif action in {'plan_gaps', 'close_research', 'compare_companies'}:
        scope['companyProfiles'] = [_profile_projection(row) for row in scope.get('companyProfiles', [])]
    if scope or 'companyScope' in packet:
        result['companyScope'] = scope
    result['contextProtocol'] = PROTOCOL
    result['_localState'] = packet.get('_localState') or {'claims': packet.get('claims', []), 'questions': packet.get('questions', []),
        'fulltextRequests': packet.get('fulltextRequests', []), 'fixedPool': pool,
        'evidenceUpdates': packet.get('evidenceUpdates', []), 'queryPaths': packet.get('queryPaths', []),
        'pathDependencies': packet.get('_localPathDependencies', {}),
        'routeSourceKeys': packet.get('_localRouteSourceKeys', {}),
        'questionDependencies': packet.get('_localQuestionDependencies', {})}
    questions = list(packet.get('questions', []))
    if planning:
        questions = [q for q in questions if q['state'] == 'open']
    elif deciding:
        affected = set(packet.get('affectedQuestionIds', []))
        questions = [q for q in questions if q['questionId'] in affected] if affected else [q for q in questions if q['state'] == 'open']
    if closing or action == 'compare_companies':
        questions = [q if q['state'] == 'open' else {key: q[key] for key in
            ('questionId', 'claimIds', 'state', 'missingEvidence', 'resumeCondition', 'knownEvidence') if key in q} for q in questions]
    result['questions'] = questions
    if (planning or deciding or direct_round) and questions:
        codes = {code for q in questions for code in q.get('companyCodes', [])}
        scope['companyProfiles'] = [row for row in scope.get('companyProfiles', []) if row['identity']['ts_code'] in codes]
        scope['candidateCompanyCodes'] = [code for code in scope.get('candidateCompanyCodes', []) if code in codes]
    if 'contextResults' in result:
        result['contextResults'] = _safe_context_results(result)
    if action == 'plan_queries' or deciding:
        ids = {claim for q in questions for claim in q['claimIds']}
        result['claims'] = [claim for claim in packet.get('claims', []) if claim['claimId'] in ids]
    ids = {claim['claimId'] for claim in result.get('claims', [])}
    result['evidenceUpdates'] = [row for row in packet.get('evidenceUpdates', []) if row['claimId'] in ids]
    result['queryPaths'] = [{key: row[key] for key in ('pathId', 'questionId', 'state', 'resultSummary', 'targetSource', 'intent') if key in row}
        for row in packet.get('queryPaths', []) if row['questionId'] in {q['questionId'] for q in questions}]
    if closing or action in {'compare_companies', 'plan_gaps'}:
        # Only material remaining paths affect closure; completed route history
        # stays available through its question, not in every comparison.
        result['queryPaths'] = [row for row in result['queryPaths'] if row['state'] == 'planned']
    if closing:
        result['pathAvailability'] = []
        for question in questions:
            paths = [row for row in packet.get('queryPaths', []) if row['questionId'] == question['questionId'] and row['state'] != 'planned']
            if paths:
                last = max(paths, key=lambda row: packet.get('_localPathOrder', {}).get(row['pathId'], 0))
                result['pathAvailability'].append({'questionId': question['questionId'],
                    'lastAttempt': {key: last.get(key) for key in ('query', 'intent', 'targetSource', 'state', 'resultSummary')},
                    'attemptedRoutesSha256': digest(sorted(digest({key: row.get(key) for key in ('query', 'intent', 'targetSource', 'state', 'resultSummary')}) for row in paths))})
    result['fulltextRequests'] = [row for row in packet.get('fulltextRequests', []) if row['state'] in {'requested', 'admitted'}]
    if isinstance(packet.get('fullTextDocuments'), list):
        result['fullTextDocuments'] = [_project_fulltext_document(row) for row in packet['fullTextDocuments'] if isinstance(row, Mapping)]
    result.pop('toolOutcomes', None)
    reusable = result.pop('reusableSourceEvidence', {})
    shared = reusable.get('claims', [])
    own = {(c.get('text'), ref_key(c['sourceRef'])) for c in result.get('claims', [])}
    shared = [c for c in shared if (c.get('text'), ref_key(c['sourceRef'])) not in own]
    # A source snapshot owns its short IDs; two different shared facts must
    # never be shown with one ID, or shadow a local claim. Only collisions
    # receive a stable namespace, keeping unaffected paid request identities.
    identities = {}
    for claim in shared:
        identities.setdefault(claim['claimId'], set()).add(digest({key: claim.get(key)
            for key in ('text', 'kind', 'novelty', 'sourceRef', 'location')}))
    local_ids = {claim['claimId'] for claim in packet.get('_localState', packet).get('claims', [])}
    shared = [{**claim, 'claimId': 'shared_' + digest({key: claim.get(key)
                    for key in ('claimId', 'text', 'kind', 'novelty', 'sourceRef', 'location')})[:24]}
              if claim['claimId'] in local_ids or len(identities[claim['claimId']]) > 1 else claim for claim in shared]
    result['reusableSourceEvidence'] = {'claims': shared, 'companyRelations': reusable.get('companyRelations', []), 'isolated': reusable.get('isolated', [])} if action in {'plan_gaps', 'plan_research', 'close_research', 'assess_and_decide', 'compare_companies', 'research_round'} else {'claims': []}
    relevant_refs = {ref_key(c['sourceRef']) for c in result.get('claims', [])}
    relevant_refs.update(ref_key(row['sourceRef']) for row in result['evidenceUpdates'])
    relevant_refs.update(ref_key(ref) for q in questions for ref in q.get('knownEvidence', []))
    relevant_refs.update(ref_key(ref) for ref in packet.get('newEvidenceRefs', []))
    cards=[]
    for card in packet.get('evidenceCards', []):
        if (planning or deciding) and ref_key(card) not in relevant_refs:
            continue
        # A claim is represented exactly once, in claims. A naked source ID is
        # insufficient: cards with neither visible claim nor excerpt stay hidden.
        claim_ids = [c['claimId'] for c in [*result.get('claims', []), *result['reusableSourceEvidence']['claims']] if ref_key(c['sourceRef']) == ref_key(card)]
        raw_excerpt = card.get('excerpt')
        projection = None
        if isinstance(raw_excerpt, str):
            raw_excerpt, projection = navigation_view(raw_excerpt, enabled=sina_source(card))
        excerpt, excerpt_hash = _bounded_visible_text(raw_excerpt)
        if excerpt is not None or claim_ids:
            visible_card = {key: value for key, value in card.items() if key not in {'sourceStatements', 'excerpt'}}
            if projection:
                visible_card['sourceViewProjection'] = projection
            if excerpt is not None:
                visible_card['excerpt'] = excerpt
            elif excerpt_hash is not None:
                visible_card.update({'excerptUnavailable': True, 'excerptSha256': excerpt_hash})
            cards.append({**visible_card, 'claimIds': claim_ids})
    result['evidenceCards'] = cards
    visible = {ref_key(card) for card in cards}
    visible.update(ref_key(doc) for doc in result.get('fullTextDocuments', [])
                   if doc.get('eligibleAtNewsCutoff') and isinstance(doc.get('excerpt'), str) and doc['excerpt'].strip())
    for item in result.get('contextResults', []):
        value = item.get('value')
        if item.get('status') == 'found' and isinstance(value, dict):
            if value.get('eligibleAtNewsCutoff') and isinstance(value.get('text'), str) and value['text'].strip():
                visible.add(ref_key(value['sourceRef']))
            if item['request']['kind'] == 'claim' and 'sourceRef' in value:
                visible.add(ref_key(value['sourceRef']))
    result['allowedEvidenceRefs'] = [ref for ref in packet.get('allowedEvidenceRefs', []) if ref_key(ref) in visible]
    result['contextReadCapabilities'] = ['company_search', 'company_fields', 'claim', 'question', 'source']
    result['visibleContext'] = {'claimIds': sorted(ids), 'sourceRefs': result['allowedEvidenceRefs'],
        'companyFields': [{'companyCode': row['identity']['ts_code'], 'fields': sorted(row),
                           'contentSha256': digest(row)} for row in scope.get('companyProfiles', [])]}
    return result


def canonical_context_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """Identity is the addressed business material, not model JSON spelling."""
    fields = {
        'source': {'sourceRef', 'location'},
        'company_fields': {'companyCode', 'fields'},
        'company_search': {'query'},
        'claim': {'id'}, 'question': {'id'},
    }
    kind = request.get('kind')
    if kind not in fields:
        raise ValueError('unknown context kind')
    result = {key: value for key, value in request.items()
              if key in fields[kind] | {'kind', 'questionId'}}
    if result.get('questionId') is None:
        result.pop('questionId', None)
    if kind == 'company_fields' and isinstance(result.get('fields'), list):
        if not all(isinstance(field, str) for field in result['fields']):
            raise ValueError('invalid company fields')
        result['fields'] = sorted(set(result['fields']))
    if kind == 'source' and isinstance(result.get('location'), str):
        result['location'] = result['location'].strip()
    return result


def read_context(request: Mapping[str, Any], *, state, documents, binding, eligible_refs, request_fits=None,
                 visible_packet=None):
    """Resolve only already stored versions; unknown locators return no evidence."""
    kind = request.get('kind')
    if not isinstance(request.get('purpose'), str) or not request['purpose'].strip():
        raise ValueError('context purpose required')
    question_id = request.get('questionId')
    questions_by_id = {q['questionId']: q for q in state['questions'] if isinstance(q, Mapping) and isinstance(q.get('questionId'), str)}
    if kind not in {'company_search', 'company_fields'} and question_id is not None and question_id not in questions_by_id:
        raise ValueError('unknown context question')
    identity = {key: value for key, value in request.items() if key != 'purpose'}
    value = None
    if kind in {'claim', 'question'}:
        collection, key = ('claims', 'claimId') if kind == 'claim' else ('questions', 'questionId')
        value = next((row for row in state[collection] if row[key] == request.get('id')), None)
    elif kind == 'company_search' and binding:
        from .v2_profiles import retrieve_company_context
        scope_error = company_question_scope_error(request, visible_packet)
        if scope_error:
            value = {'status': scope_error}
        else:
            value = retrieve_company_context(db_path=binding[0], profiles_id=binding[1], query=request.get('query', ''))
            value.pop('fixedPool', None)
            value['companyProfiles'] = [_profile_projection(row) for row in value.get('companyProfiles', [])]
    elif kind == 'company_fields' and binding:
        from .v2_profiles import read_profiles, source_ids
        scope_error = company_field_scope_error(request, visible_packet)
        if scope_error:
            value = {'status': scope_error}
        rows = [] if scope_error else read_profiles(db_path=binding[0], profiles_id=binding[1], codes=[request.get('companyCode')])
        if rows and isinstance(request.get('fields'), list):
            profile = rows[0]
            fields = request['fields']
            if all(isinstance(key, str) and key in profile and key not in {'raw_evidence_file', 'sources'} for key in fields):
                selected = {key: profile[key] for key in fields}
                refs = set(source_ids(selected))
                sources = [source for source in profile['sources']
                    if any(ref == source['source_id'] or ref.startswith(source['source_id'] + '.') for ref in refs)]
                candidate = {'profileSnapshotId': binding[1], 'identity': profile['identity'],
                    'review_status': profile['review_status'], 'fields': selected,
                    'sources': sources, 'contentSha256': digest(profile)}
                value = _refine_company_fields(candidate, _profile_projection(profile).get('fieldManifest', []))
    elif kind == 'source':
        ref = request.get('sourceRef') or {}
        doc = next((doc for key, doc in documents.items() if (key.document_id, key.revision) == ref_key(ref)), None)
        location = request.get('location')
        if doc is not None:
            text = doc.analysis_text or doc.original_text or doc.excerpt or ''
            budget = max(1, len(text)*2) if callable(request_fits) else MAX_FRAGMENT_CHARACTERS
            question = questions_by_id.get(question_id) if isinstance(question_id, str) else None
            if requires_current_event_locator(doc):
                # A report document is background until a persisted open
                # question ties a direct structural read to this event.  A
                # free-form purpose, outline or excerpt cannot turn it into
                # evidence or silently load the whole annual report.
                if question is None or question.get('state') != 'open' or not question.get('claimIds'):
                    value = {'sourceRef': ref, 'status': 'requires_current_event_question',
                             'needsLocator': True, 'reason': 'background_requires_event_question'}
                elif not isinstance(location, str):
                    value = {'sourceRef': ref, 'status': 'requires_direct_locator',
                             'needsLocator': True, 'reason': 'background_requires_direct_locator',
                             'questionId': question_id}
                elif location.startswith('find:'):
                    # A scoped keyword lookup returns only the document's real
                    # locator catalogue.  It does not reveal an outline or
                    # body, and the next request still has to name one exact
                    # paragraph/table/line before it can become evidence.
                    if not scoped_find(location, question):
                        value = {'sourceRef': ref, 'status': 'requires_direct_locator',
                                 'needsLocator': True, 'reason': 'background_find_outside_question',
                                 'questionId': question_id}
                    else:
                        value = read_locator(doc, location, max_characters=budget)
                elif not direct_location(location):
                    value = {'sourceRef': ref, 'status': 'requires_direct_locator',
                             'needsLocator': True, 'reason': 'background_requires_direct_locator',
                             'questionId': question_id}
                else:
                    value = read_locator(doc, location, max_characters=budget)
            elif location == 'excerpt':
                value = bounded_excerpt(doc, max_characters=budget)
            elif isinstance(location, str):
                value = read_locator(doc, location, max_characters=budget)
            if isinstance(value, Mapping):
                value = {**value, 'sourceRef': ref,
                    'eligibleAtNewsCutoff': ref_key(ref) in eligible_refs,
                    'contentVersionAtCutoff': doc.metadata.get('contentVersionAtCutoff')}
            elif value:
                value = {'sourceRef': ref, 'location': location, 'text': value,
                    'publishedAt': doc.published_at, 'fetchedAt': doc.fetched_at,
                    'eligibleAtNewsCutoff': ref_key(ref) in eligible_refs,
                    'contentVersionAtCutoff': doc.metadata.get('contentVersionAtCutoff')}
    result = {'request': identity, 'status': 'found' if value is not None else 'unknown_reference',
              'value': value, 'contentSha256': digest(value)}
    if callable(request_fits) and isinstance(value, Mapping) and isinstance(value.get('locators'), list):
        while len(value['locators']) > 1 and not request_fits(result):
            value = resize_catalogue(value, len(value['locators']) // 2)
            result = {**result, 'value': value, 'contentSha256': digest(value)}
    if value is not None and callable(request_fits) and not request_fits(result):
        # The complete evidence unit is either visible or withheld. Do not trim
        # a trailing negation, unit, footnote, or nested company field to fit.
        reduced = {key: value[key] for key in ('sourceRef', 'sourceContentSha256', 'indexVersion',
            'locator', 'startOffset', 'endOffset', 'profileSnapshotId', 'identity', 'contentVersionAtCutoff')
            if isinstance(value, Mapping) and key in value}
        reduced.update({'status': 'not_safely_readable', 'needsLocator': kind == 'source',
            'needsFieldRefinement': kind == 'company_fields', 'contentSha256': digest(value),
            'reason': '完整资料连同限定说明无法放入本次实际请求；未截断，需进一步定位或保留资料缺口。'})
        return {**result, 'value': reduced, 'contentSha256': digest(reduced)}
    return result


def normalized_text(value):
    import re
    return re.sub(r'[\W_]+', '', str(value).casefold())


def question_identity(question):
    return digest([sorted(question.get('claimIds', [])), sorted(question.get('companyCodes', [])),
        normalized_text(question.get('question', '')), normalized_text(question.get('supportCondition', '')),
        normalized_text(question.get('refuteCondition', ''))])


def collapse_repeated_work(value, packet, *, admit_initial_source_labels: bool = False):
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
    source_keys = local.get('routeSourceKeys', {}) if isinstance(local.get('routeSourceKeys'), Mapping) else {}
    question_dependencies = (local.get('questionDependencies', {})
                             if isinstance(local.get('questionDependencies'), Mapping) else {})
    def route(row, *, source_key=None):
        q = known.get(row.get('questionId'), {})
        if row.get('state') != 'planned':
            dependency = local.get('pathDependencies', {}).get(row.get('pathId'))
            # No durable dependency means an old route's relation to current
            # evidence is unknown. Do not manufacture one from a newer
            # question and thereby suppress a legitimate increment.
            if not isinstance(dependency, Mapping):
                return None
        else:
            dependency = question_dependencies.get(row.get('questionId'))
            if not isinstance(dependency, Mapping):
                dependency = None
        return route_identity(row, q, dependency, initial_source_key=source_key)
    used = set()
    for row in local.get('queryPaths', []):
        if row['state'] == 'planned':
            continue
        source_key = source_keys.get(row.get('pathId'))
        identity = route(row, source_key=source_key)
        if identity is not None:
            used.add(identity)
        # A later planning receipt cannot add a fresh label to a completed
        # route.  Its base identity must therefore collide as well.
        identity = route(row)
        if identity is not None:
            used.add(identity)
    paths=[]
    for path in value.get('queryPaths', []):
        path = {**path, 'questionId': aliases.get(path.get('questionId'), path.get('questionId'))}
        source_key = declared_source_key(path) if admit_initial_source_labels else None
        key = route(path, source_key=source_key)
        if key is not None and key not in used:
            paths.append(path)
            used.add(key)
    handled = {(row['questionId'], ref_key(row['sourceRef'])) for row in local.get('fulltextRequests', [])
        if row.get('state') in {'fulfilled', 'rejected'}}
    requests = [row for row in value.get('fulltextRequests', [])
        if (row.get('questionId'), ref_key(row.get('sourceRef', {}))) not in handled]
    return {**value, **({'questions': questions} if 'questions' in value else {}),
        **({'queryPaths': paths} if 'queryPaths' in value else {}),
        **({'fulltextRequests': requests} if 'fulltextRequests' in value else {})}


def _dependency_evidence_refs(dependency: Mapping[str, Any]) -> list[tuple[str, int]]:
    """Return the dependency's own durable source references, if any."""
    raw = dependency.get('knownEvidenceRefs', dependency.get('knownEvidence', ()))
    refs: set[tuple[str, int]] = set()
    for ref in raw if isinstance(raw, (list, tuple)) else ():
        if isinstance(ref, Mapping):
            document_id, revision = ref.get('documentId'), ref.get('revision')
        elif isinstance(ref, (list, tuple)) and len(ref) == 2:
            document_id, revision = ref
        else:
            continue
        if (isinstance(document_id, str) and document_id
                and isinstance(revision, int) and not isinstance(revision, bool) and revision > 0):
            refs.add((document_id, revision))
    return sorted(refs)


def normalize_question_dependency(dependency: Mapping[str, Any], *,
                                  content_sha256_by_ref: Mapping[tuple[str, int], str]) -> dict[str, Any]:
    """Canonicalize a durable dependency from its own immutable source refs.

    This is deliberately separate from :func:`question_dependency`: old stage
    receipts have no complete Question object to rebuild, and borrowing the
    current question would turn a later evidence revision into historical
    input.  Callers supply hashes only for the frozen refs returned above.
    """
    refs = _dependency_evidence_refs(dependency)
    if not refs:
        return dict(dependency)
    normalized, visible_refs = [], []
    has_reliable_hash = False
    for document_id, revision in refs:
        content_sha256 = content_sha256_by_ref.get((document_id, revision))
        reliable = (isinstance(content_sha256, str)
                    and re.fullmatch(r'[0-9a-f]{64}', content_sha256.casefold()) is not None)
        visible_refs.append({'documentId': document_id, 'revision': revision,
                             **({'contentSha256': content_sha256.casefold()} if reliable else {})})
        if reliable:
            normalized.append({'contentSha256': content_sha256.casefold()})
            has_reliable_hash = True
        else:
            normalized.append({'documentId': document_id, 'revision': revision})
    if not has_reliable_hash:
        # Missing historical content remains unknown.  Do not rewrite an old
        # receipt just because this repair cannot prove an equivalent body.
        return dict(dependency)
    return {**dependency,
            'knownEvidence': [json.loads(value) for value in sorted({
                json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(',', ':')) for item in normalized
            })],
            'knownEvidenceRefs': visible_refs}


def question_dependency(question, *, content_sha256_by_ref: Mapping[tuple[str, int], str] | None = None):
    """Freeze only evidence already visible to this question.

    A source document ID is an ingestion identity, not a research fact
    identity. When the runtime has exact stored content hashes for this
    question's visible references, equal syndicated copies collapse to one
    dependency. Unknown hashes deliberately retain the old document/revision
    form instead of consulting unrelated database content.
    """
    refs = sorted({ref_key(ref) for ref in question.get('knownEvidence', [])
                   if isinstance(ref, Mapping) and isinstance(ref.get('documentId'), str)
                   and isinstance(ref.get('revision'), int) and not isinstance(ref.get('revision'), bool)})
    hashes = content_sha256_by_ref or {}
    normalized = []
    visible_refs = []
    has_reliable_hash = False
    for document_id, revision in refs:
        content_sha256 = hashes.get((document_id, revision))
        reliable = (isinstance(content_sha256, str)
                    and re.fullmatch(r'[0-9a-f]{64}', content_sha256.casefold()) is not None)
        visible_refs.append({"documentId": document_id, "revision": revision,
                             **({"contentSha256": content_sha256.casefold()} if reliable else {})})
        if reliable:
            normalized.append({"contentSha256": content_sha256.casefold()})
            has_reliable_hash = True
        else:
            normalized.append({"documentId": document_id, "revision": revision})
    dependency = {'questionIdentity': question_identity(question),
                  # Preserve the exact legacy tuple form when no content hash
                  # is available. A historical receipt with unknown identity
                  # must not start differing merely because this repair ran.
                  'knownEvidence': (refs if not has_reliable_hash else [json.loads(value) for value in sorted({
                      json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(',', ':')) for item in normalized
                  })])}
    # This raw list validates typed source locators. It is excluded from route
    # identity below, otherwise duplicate document IDs undo content matching.
    if has_reliable_hash:
        dependency['knownEvidenceRefs'] = visible_refs
    return dependency


def _semantic_target_refs(path):
    """Return the model-declared question targets as an order-independent identity."""
    raw = path.get("targetRefs") if isinstance(path, Mapping) else None
    if not isinstance(raw, list):
        return []
    values = {
        json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(',', ':')) for item in raw
        if isinstance(item, Mapping) and item.get("kind") in {"claim", "company"}
    }
    return [json.loads(value) for value in sorted(values)]


def declared_source_key(path: Mapping[str, Any]) -> str | None:
    """Freeze an initial route declaration without treating it as evidence.

    A provider's source label has no evidentiary value.  It can, however,
    distinguish two first-plan searches before either search has run.  Later
    replies never receive this admission and cannot expand the frozen set.
    """
    values = []
    for field in ('targetSource', 'query'):
        value = path.get(field)
        if isinstance(value, str):
            values.append(value)
    hosts: set[str] = set()
    for value in values:
        for raw in re.findall(r'https?://[^\s<>"\']+', value, flags=re.IGNORECASE):
            parsed = urlsplit(raw.rstrip('.,;:!?)，。；：！）'))
            if parsed.hostname:
                try:
                    port = f':{parsed.port}' if parsed.port is not None else ''
                except ValueError:
                    continue
                hosts.add((parsed.hostname.casefold() + port).rstrip('.'))
        for host in re.findall(r'(?i)\bsite:([a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)', value):
            hosts.add(host.casefold().rstrip('.'))
    if hosts:
        return 'host:' + '|'.join(sorted(hosts))
    target = path.get('targetSource')
    if not isinstance(target, str):
        return None
    normalized = ' '.join(target.casefold().split())
    return None if not normalized else 'label:' + normalized


def _verified_path_locators(path, dependency):
    """Keep only locators anchored to evidence already visible to this request.

    A free-text ``targetSource`` or rewritten query cannot manufacture a new
    paid route.  Optional structured locators are useful only when their
    document/version is already part of the frozen question dependency.
    """
    known: set[tuple[str, int]] = set()
    raw_refs = (dependency or {}).get("knownEvidenceRefs", (dependency or {}).get("knownEvidence", []))
    content_by_ref: dict[tuple[str, int], str] = {}
    for ref in raw_refs:
        if isinstance(ref, Mapping):
            document_id, revision = ref.get("documentId"), ref.get("revision")
        elif isinstance(ref, (list, tuple)) and len(ref) == 2:
            document_id, revision = ref
        else:
            continue
        if (isinstance(document_id, str) and isinstance(revision, int)
                and not isinstance(revision, bool)):
            known.add((document_id, revision))
            content_sha256 = ref.get("contentSha256") if isinstance(ref, Mapping) else None
            if isinstance(content_sha256, str) and re.fullmatch(r'[0-9a-f]{64}', content_sha256.casefold()):
                content_by_ref[(document_id, revision)] = content_sha256.casefold()
    values: set[str] = set()
    # ``targetSource`` is a model-authored label, sometimes a URL, and is not
    # proof of a versioned document or an exact location.  Only the persisted
    # typed locator can distinguish two otherwise equivalent routes.
    candidates = [path["sourceLocator"]] if isinstance(path, Mapping) and isinstance(path.get("sourceLocator"), Mapping) else []
    for item in candidates:
        document_id, revision = item.get("documentId"), item.get("revision")
        if (document_id, revision) not in known:
            continue
        locator = item.get("locator", item.get("location", item.get("url")))
        if not isinstance(locator, str) or not locator.strip():
            continue
        identity = ({"contentSha256": content_by_ref[(document_id, revision)]}
                    if (document_id, revision) in content_by_ref
                    else {"documentId": document_id, "revision": revision})
        values.add(json.dumps({"source": identity, "locator": locator.strip()}, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
    return [json.loads(value) for value in sorted(values)]


def route_identity(path, question, dependency=None, *, initial_source_key: str | None = None):
    frozen = dependency or question_dependency(question)
    # A route is qualified by the question's frozen identity, semantic purpose
    # and targets, plus real evidence versions or an explicitly anchored
    # source locator.  Wording and generated path IDs remain audit fields only.
    dependency_identity = ({key: value for key, value in frozen.items() if key != "knownEvidenceRefs"}
                           if isinstance(frozen, Mapping) else frozen)
    value = [dependency_identity, path.get("purposeKind") if isinstance(path, Mapping) else None,
             _semantic_target_refs(path), _verified_path_locators(path, frozen)]
    if initial_source_key is not None:
        value.append(initial_source_key)
    return digest(value)


def normalized_query(value):
    # Preserve term boundaries and search operators; punctuation can carry
    # a negation or an exact-phrase requirement.
    return ' '.join(str(value or '').casefold().split())
