"""Explicit, atomic local imports. Runtime reads SQLite, never source paths."""
from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any

from .schema import read_connection, require_schema, write_connection

UNIVERSE_ID = "k10-v2-initial-20260909"
PROFILES_ID = "k10-v2-profiles-20260909"
STRATEGY_SHA256 = "44817dfdb8f7815568909b4bf0bf8b6ba3054815be2a50d8682c7b4d8602dd3d"
INPUT_HASHES = {
    "universe": "161c1dbbf7119a3ce5bf9d6aa183635eb0af05eee7b77d9e962ede5f54eb3b48",
    "profiles": "5bacdc5b2da3783a0c131d219d71bb6faf99d2cad01cc631f9a47e2587d21782",
    "index": "c2ddfa747ec80344abfc16e6f018336d89f106ec88afb121ef6e9ba33a758d10",
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_inputs(*, universe_file: Path, profiles_dir: Path) -> dict[str, Any]:
    files = {"universe": universe_file, "profiles": profiles_dir / "profiles.jsonl", "index": profiles_dir / "screening_index.jsonl"}
    raw = {key: path.read_bytes() for key, path in files.items()}
    hashes = {key: sha256(value).hexdigest() for key, value in raw.items()}
    if hashes != INPUT_HASHES:
        raise ValueError("固定输入 SHA-256 不匹配；未写入目标")
    universe = json.loads(raw["universe"])
    profiles = [json.loads(line) for line in raw["profiles"].splitlines() if line.strip()]
    index = [json.loads(line) for line in raw["index"].splitlines() if line.strip()]
    members = universe["stocks"]
    if universe.get("strategy_version") != "K10-v2" or universe.get("count") != 1089 or any(len(rows) != 1089 for rows in (members, profiles, index)):
        raise ValueError("股票池／资料／索引必须各有 1,089 条")
    names = {row["ts_code"]: row["name"] for row in members}
    pmap = {row["identity"]["ts_code"]: row for row in profiles}
    imap = {row["ts_code"]: row for row in index}
    if len(names) != 1089 or set(names) != set(pmap) or set(names) != set(imap):
        raise ValueError("代码重复或三份输入集合不一致")
    evidence = {}
    root = profiles_dir.resolve()
    for code, name in names.items():
        profile, item = pmap[code], imap[code]
        if profile["identity"]["name"] != name or item["name"] != name:
            raise ValueError("公司名称不一致")
        if profile.get("review_status") != "local_draft_awaiting_user":
            raise ValueError("资料必须保留本地初稿来源状态")
        for field in ("summary", "businesses", "relationships", "match_terms", "sources", "evidence", "revenue_structure", "market_distribution", "industry_chain", "raw_evidence_file"):
            if field not in profile:
                raise ValueError(f"完整资料缺少 {field}")
        for relative in (profile["raw_evidence_file"], item["profile_file"]):
            path = (root / relative).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError("引用文件不存在或越过输入目录")
        individual = json.loads((root / item["profile_file"]).read_bytes())
        if individual != profile:
            raise ValueError("逐公司档案与 profiles.jsonl 不一致")
        content = (root / profile["raw_evidence_file"]).read_bytes()
        evidence[code] = (json.loads(content), sha256(content).hexdigest())
    return {"universe": universe, "profiles": pmap, "index": imap, "evidence": evidence, "hashes": hashes}


def import_profiles(*, universe_file: Path, profiles_dir: Path, db_path: Path,
                    confirmed_target: Path, universe_id: str, profiles_id: str, imported_at: str) -> dict[str, Any]:
    if db_path.resolve() != confirmed_target.resolve():
        raise ValueError("confirmed-target 不等于数据库目标")
    if (universe_id, profiles_id) != (UNIVERSE_ID, PROFILES_ID):
        raise ValueError("必须明确使用固定快照标识")
    snapshot = validate_inputs(universe_file=universe_file, profiles_dir=profiles_dir)
    hashes = snapshot["hashes"]
    # Validation precedes even opening the destination. Schema migration is separate.
    with write_connection(db_path) as conn:
        require_schema(conn)
        existing = conn.execute("SELECT hashes_json FROM k10_v2_profile_snapshots WHERE snapshot_id=?", (profiles_id,)).fetchone()
        if existing:
            if existing[0] != _json(hashes):
                raise ValueError("同一快照 ID 的哈希冲突")
            for table in ('k10_v2_company_profiles','k10_v2_company_profile_evidence'):
                if conn.execute(f'SELECT count(*) FROM {table} WHERE snapshot_id=?', (profiles_id,)).fetchone()[0] != 1089:
                    raise ValueError('已存快照缺少公司资料或原始证据，禁止默认为完整')
            return {"status": "unchanged", "count": 1089, "profileSnapshotId": profiles_id}
        conn.execute("INSERT INTO k10_v2_universe_snapshots VALUES (?,?,?,?,?)", (universe_id, "K10-v2", hashes["universe"], _json(snapshot["universe"]), imported_at))
        conn.execute("INSERT INTO k10_v2_profile_snapshots VALUES (?,?,?,?,?)", (profiles_id, universe_id, _json(hashes), "local_draft_awaiting_user", imported_at))
        for member in snapshot["universe"]["stocks"]:
            code = member["ts_code"]
            conn.execute("INSERT INTO k10_v2_universe_members VALUES (?,?,?)", (universe_id, code, member["name"]))
            conn.execute("INSERT INTO k10_v2_company_profiles VALUES (?,?,?,?)", (profiles_id, code, _json(snapshot["profiles"][code]), _json(snapshot["index"][code])))
            evidence, digest = snapshot["evidence"][code]
            conn.execute("INSERT INTO k10_v2_company_profile_evidence VALUES (?,?,?,?)", (profiles_id, code, _json(evidence), digest))
    return {"status": "imported", "count": 1089, "profileSnapshotId": profiles_id}


def read_profiles(*, db_path: Path, profiles_id: str, codes: list[str] | None = None, index_only: bool = False) -> list[dict]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        column = "index_json" if index_only else "profile_json"
        query = f"SELECT {column} FROM k10_v2_company_profiles WHERE snapshot_id=?"
        args: list[Any] = [profiles_id]
        if codes is not None:
            if not codes:
                return []
            query += " AND company_code IN (" + ",".join("?" for _ in codes) + ")"
            args.extend(codes)
        return [json.loads(row[0]) for row in conn.execute(query + " ORDER BY company_code", args)]


# Only business values contribute retrieval terms. These runtime fields are
# identities/status, never business evidence, even when nested under facts.
_NON_SEMANTIC = {'claimId', 'questionId', 'pathId', 'documentId', 'revision',
    'sourceRef', 'sourceRefs', 'verificationStatus', 'state', 'coverage',
    'eventState', 'eventKind', 'canonicalKey', 'stageKey', 'kind', 'novelty',
    'compiled_at', 'fetchedAt', 'publishedAt', 'createdAt', 'updatedAt'}

def semantic_text(value: Any) -> str:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return value
        return semantic_text(decoded) if isinstance(decoded, (dict, list)) else value
    if isinstance(value, dict):
        return ' '.join(semantic_text(item) for key, item in value.items() if key not in _NON_SEMANTIC)
    if isinstance(value, (list, tuple)):
        return ' '.join(semantic_text(item) for item in value)
    return ''

def matches_term(term: str, text: str) -> bool:
    if not isinstance(term, str) or not term:
        return False
    term, text = term.casefold(), text.casefold()
    left = r'(?<![a-z0-9_])' if re.match(r'[a-z0-9_]', term) else ''
    right = r'(?![a-z0-9_])' if re.search(r'[a-z0-9_]$', term) else ''
    return re.search(left + re.escape(term) + right, text) is not None


def retrieve_company_context(*, db_path: Path, profiles_id: str, query: Any,
                             hinted_codes: list[str] | None = None) -> dict[str, Any]:
    """Local field retrieval; company names are not the only lookup key.

    Draft index terms, products, subsidiaries and dependencies are routing leads,
    never verified event evidence. No network or runtime laboratory dependency.
    """
    import re
    index = read_profiles(db_path=db_path, profiles_id=profiles_id, index_only=True)
    allowed = {row['ts_code'] for row in index}
    hints = set(hinted_codes or [])
    if not hints <= allowed:
        raise ValueError('公司不属于固定资料快照')
    text = semantic_text(query).casefold()
    matches = {}
    for row in index:
        terms = [row['name'], *row.get('aliases', []), *row.get('match_terms', []),
                 *row.get('dependency_terms', []),
                 *(item.get('entity', '') for item in row.get('relationships', []))]
        found = [term for term in terms if isinstance(term, str) and term and matches_term(term, text)]
        if found or row['ts_code'] in hints:
            matches[row['ts_code']] = found
    profiles = read_profiles(db_path=db_path, profiles_id=profiles_id, codes=sorted(matches))
    projected = []
    query_terms = set(re.findall(r'[a-z0-9_-]+|[\u4e00-\u9fff]{2,}', text))
    for profile in profiles:
        code = profile['identity']['ts_code']
        terms = [term.casefold() for term in matches[code]] + list(query_terms)
        def relevant(value):
            content = semantic_text(value).casefold()
            return any(matches_term(term, content) for term in terms)
        fields = {'identity': profile['identity'], 'summary': profile['summary'],
                  'review_status': profile['review_status'], 'compiled_at': profile['compiled_at'],
                  'profileContentSha256': sha256(_json(profile).encode()).hexdigest()}
        for key in ('businesses', 'relationships', 'revenue_structure', 'market_distribution', 'industry_chain'):
            value = profile.get(key)
            if isinstance(value, list):
                selected = [item for item in value if relevant(item)]
                if selected: fields[key] = selected
            elif value and relevant(value):
                if isinstance(value, dict) and isinstance(value.get('items'), list):
                    selected = [item for item in value['items'] if relevant(item)]
                    # Keep period, denominator, scope differences and all
                    # original caveats; only unrelated item rows are omitted.
                    fields[key] = {**value, 'items': selected,
                        'projectionScope': {'includedItems': len(selected), 'sourceItems': len(value['items']),
                            'fullFieldAvailable': key}}
                else:
                    fields[key] = value
        # Source metadata remains available without resending raw evidence/full archives.
        def source_ids(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {'source_ref', 'source_refs'}:
                        yield from ([item] if isinstance(item, str) else item)
                    else:
                        yield from source_ids(item)
            elif isinstance(value, list):
                for item in value:
                    yield from source_ids(item)
        refs = set(source_ids(fields))
        fields['sources'] = [source for source in profile['sources']
            if any(ref == source['source_id'] or ref.startswith(source['source_id'] + '.') for ref in refs)]
        fields['fieldRefs'] = [{'field': key, 'contentSha256': sha256(_json(value).encode()).hexdigest()}
            for key, value in fields.items() if key not in {'sources', 'identity'}]
        fields['retrieval'] = {'matchedTerms': matches[code], 'titleHint': code in hints,
                               'missingFields': [key for key in ('businesses','relationships','revenue_structure','market_distribution','industry_chain') if key not in fields]}
        projected.append(fields)
    return {'profileSnapshotId': profiles_id,
            'fixedPool': [{'companyCode': row['ts_code'], 'name': row['name']} for row in index],
            'candidateCompanyCodes': sorted(matches), 'companyProfiles': projected,
            'localProfileQuery': {'contentSha256': sha256(text.encode()).hexdigest()}, 'profileStatus': 'local_draft_awaiting_user',
            'scopeRule': '仅池内公司可展开尽调；池外主体仅作背景。按命题语义、产品、子公司及产业链线索关联，不得凭公司名猜测。资料未核，缺失字段明确列示；不能映射则结束，不搜索全市场。'}
