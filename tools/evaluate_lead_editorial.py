"""Offline captured-artifact comparison; never runs agents or grants authority."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
from pathlib import Path
import re
import sys

AXES = (
    'intent_fulfillment',
    'voice_preservation',
    'important_error_avoidance',
    'edit_restraint',
    'approval_burden',
)
CASES = (
    'dialogue-boundaries',
    'intentional-unreliable-narrator',
    'stronger-effect',
    'minority-setting-error',
    'incompatible-recommendations',
    'review-only-exact-input',
    'discarded-alternative-history',
    'exploration-and-controlled-adoption',
    'decision-memory-restart-and-transfer',
)
DECISIONS = ("clarifications", "confirmations", "revisions", "apply", "commit", "rejections")
METRICS = (
    'submitted_calls',
    'prompt_tokens',
    'completion_tokens',
    'instruction_utf8_bytes',
    'schema_utf8_bytes',
    'payload_utf8_bytes',
    'wall_seconds',
)
META_LIMIT, ARTIFACT_LIMIT, OUTPUT_LIMIT, TOTAL_LIMIT = 4 << 20, 16 << 20, 1 << 20, 64 << 20
PACKET_LIMIT = 128 << 20
ID = ("pattern", r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
SHA = ("pattern", r"[0-9a-f]{64}")
TEXT, SMALL = ("text", 4096), ("text", 256)
COUNT, VERSION = ("int", 0, 10**9), ("int", 1, 1)


def _nullable(spec):
    return ("nullable", spec)


def _enum(*values):
    return ("enum", values)


REF = {
    'kind': _enum(
        'output',
        'capture_record',
        'input_record',
        'accounting_record',
        'workflow_record',
        'review_record',
        'protocol_record',
    ),
    'path': TEXT,
    'sha256': SHA,
}
REFS = ("list", REF, 32)
CONDITIONS = {
    'repository_commit': _nullable(('pattern', '[0-9a-f]{40}')),
    'provider': _nullable(SMALL),
    'model': _nullable(SMALL),
    'settings': _nullable(('map', ('scalar',), 32)),
    'unknown_settings': ('list', SMALL, 32),
    'call_ceiling': _nullable(('int', 1, 10 ** 6)),
    'evidence_access': _nullable(TEXT),
    'user_decision_policy': _nullable(TEXT),
}
COSTS = {name: _nullable(COUNT) for name in METRICS}
COSTS["wall_seconds"] = _nullable(("number", 0, 10**9))
COSTS.update(decisions={name: _nullable(COUNT) for name in DECISIONS}, evidence=REFS)
OBSERVATION = {
    'observation_id': ID,
    'auditor_id': ID,
    'status': _enum('pass', 'fail', 'not_observed', 'not_supported'),
    'rationale': _nullable(TEXT),
    'evidence': REFS,
}
RUN = {
    'status': _enum('captured', 'failed'),
    'run_kind': _enum('provider_capture', 'synthetic_capture'),
    'input_sha256': SHA,
    'conditions': CONDITIONS,
    'output': _nullable(REF),
    'capture_evidence': _nullable(REF),
    'input_evidence': _nullable(REF),
    'costs': COSTS,
    'workflow_observations': ('list', OBSERVATION, 32),
    'reason': _nullable(TEXT),
    '?interaction_context': _nullable({'text': _nullable(TEXT), 'evidence': REFS}),
}
MISSING = {"status": _enum("not_observed", "not_supported"), "reason": TEXT}
CAPTURE = {
    'format_version': VERSION,
    'comparison_id': ID,
    'dataset_sha256': SHA,
    'comparison_kind': _enum('before_after', 'single_vs_multi'),
    'treatment': TEXT,
    'seed': ('int', 0, 2 ** 32 - 1),
    'protocol': {'status': _enum('predeclared', 'retrospective', 'unknown'), 'evidence': _nullable(REF)},
    'arms': ('list', {'arm_id': ID, 'label': SMALL}, 2),
    'cases': ('list', {'case_id': ID, 'case_sha256': SHA, 'runs': ('map', ('any',), 2)}, 9),
}
RATING = {"score": _nullable(("int", 0, 4)), "rationale": _nullable(TEXT)}
ERROR = {
    'side': _enum('A', 'B'),
    'source_id': _nullable(ID),
    'description': TEXT,
    'impact': TEXT,
    'disposition': _enum('resolved', 'deferred', 'rejected', 'unresolved'),
    'reason': TEXT,
}
REVIEW = {
    'reviewer_id': ID,
    'role': _enum('author_intent', 'reader_editor', 'other'),
    'evidence_kind': _enum('human_blinded', 'human_unblinded', 'model_generated', 'synthetic_fixture'),
    'case_id': ID,
    'ratings': {side: {axis: RATING for axis in AXES} for side in 'AB'},
    'preference': _enum('A', 'B', 'tie', 'not_observed'),
    'important_errors': ('list', ERROR, 32),
    'evidence': _nullable(REF),
}
REVIEWS = {
    'format_version': VERSION,
    'comparison_id': ID,
    'review_packet_sha256': SHA,
    'reviews': ('list', REVIEW, 144),
}
LIMITATION = (
    'Hashes bind supplied bytes, not provider/human authenticity, audit semantics or '
    'genuine blinding. No automatic improvement assessment; synthetic data is '
    'mechanics-only.'
)
SCORING_NOTE = (
    'Required confirmations are safeguards. Missing interaction context requires a '
    'null human approval_burden score; output and brief alone do not establish '
    'avoidability.'
)

RUBRIC = {
    "score_range": [0, 4],
    "dimensions": {
        "intent_fulfillment": {
            "0": "Misses or reverses the author's effect.",
            "2": "Partly achieves it, with material compromise.",
            "4": "Clearly achieves it within the brief.",
        },
        "voice_preservation": {
            "0": "Replaces the intended voice or characterization.",
            "2": "Preserves some distinctive qualities but flattens others.",
            "4": "Retains the relevant voice while making the requested improvement.",
        },
        "important_error_avoidance": {
            "0": "Introduces or hides a major source-bound error.",
            "2": "Leaves a meaningful issue unresolved or poorly explained.",
            "4": "Introduces no important error and handles material concerns accurately.",
        },
        "edit_restraint": {
            "0": "Rewrites protected events, motives, or ambiguity without authority.",
            "2": "Makes some unnecessary changes or gives weak reasons.",
            "4": "Changes only what the brief allows; any necessary tradeoff is explicit.",
        },
        "approval_burden": {
            "0": "Creates avoidable decisions or conceals required confirmation.",
            "2": "Adds some avoidable clarification or review effort.",
            "4": (
                "Makes decisions easy to assess and asks only necessary questions while "
                "retaining required confirmations."
            ),
        },
    },
    "instructions": [
        (
            "Score each dimension independently; higher is better. Retain rationale, "
            "passages and important errors."
        ),
        "Scores 1 and 3 fall between adjacent anchors.",
        "Use null when evidence is unobserved; do not infer scores from missing material.",
        (
            "Do not collapse the vector into one score or conceal a serious error or failed"
            " authority check."
        ),
        (
            "Constructed fixture labels describe synthetic input/intent, not provider "
            "execution or a real writer preference."
        ),
        SCORING_NOTE,
    ],
}



def _need(condition, message):
    if not condition:
        raise ValueError(message)


def _check(value, spec):
    if isinstance(spec, dict):
        _need(type(value) is dict, "expected object")
        required = {key for key in spec if not key.startswith("?")}
        _need(required <= value.keys() <= {key.lstrip('?') for key in spec}, 'unknown or missing field')
        for key, child in spec.items():
            if key.lstrip("?") in value:
                _check(value[key.lstrip("?")], child)
        return
    kind, *args = spec
    if kind == "any" or (kind == "nullable" and value is None):
        return
    if kind == "nullable":
        _check(value, args[0])
    elif kind == "pattern":
        _need(type(value) is str and re.fullmatch(args[0], value), "invalid identifier/digest")
    elif kind == "text":
        _need(
            (type(value) is str and value.strip() and (len(value.encode('utf-8')) <= args[0])),
            'invalid text',
        )
    elif kind in {"int", "number"}:
        types = (int,) if kind == "int" else (int, float)
        _need(
            (type(value) in types and math.isfinite(value) and (args[0] <= value <= args[1])),
            'invalid number',
        )
    elif kind == "enum":
        _need(type(value) is str and value in args[0], "invalid enum")
    elif kind == "list":
        _need(type(value) is list and len(value) <= args[1], "invalid array")
        for child in value:
            _check(child, args[0])
    elif kind == "map":
        _need(type(value) is dict and len(value) <= args[1], "invalid map")
        for key, child in value.items():
            _check(key, ("text", 128))
            _check(child, args[0])
    elif kind == "scalar":
        _need(value is None or type(value) in (bool, int, float, str), "invalid setting")
        if type(value) is str:
            _need(len(value.encode("utf-8")) <= 4096, "setting too long")
        if type(value) in (int, float):
            _need(math.isfinite(value), "nonfinite setting")


def _canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        allow_nan=False,
    ).encode('utf-8')


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _unique(items):
    _need(len(items) == len(set(items)), "duplicate identity")


def _pairs(pairs):
    _unique([key for key, _ in pairs])
    return dict(pairs)


def _read(path, limit):
    _need(path.is_file() and not path.is_symlink(), "regular file required")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    _need(len(data) <= limit, "file byte limit exceeded")
    return data


def _json(path, limit=META_LIMIT):
    raw = _read(path, limit)
    value = json.loads(
        raw.decode('utf-8'),
        object_pairs_hook=_pairs,
        parse_constant=lambda _: _need(False, 'nonfinite JSON'),
    )
    return value, raw


class _Artifacts:
    def __init__(self, root):
        self.root, self.cache, self.total, self.references = root.resolve(), {}, 0, 0


    def read(self, ref, kind):
        if ref is None:
            return None
        _check(ref, REF)
        _need(ref["kind"] == kind, "wrong artifact kind")
        _need("\\" not in ref["path"] and "\0" not in ref["path"], "unsafe artifact path")
        parts = ref["path"].split("/")
        _need(
            (
                all((part not in {'', '.', '..'} for part in parts))
                and not Path(ref['path']).is_absolute()
            ),
            'unsafe artifact path',
        )
        path = self.root
        for part in parts:
            path = path / part
            _need(not path.is_symlink(), "symlink artifact")
        _need(path.is_file(), "artifact file missing")
        path = path.resolve(strict=True)
        _need(path.is_relative_to(self.root), "artifact escapes root")
        self.references += 1
        _need(self.references <= 512, "artifact reference limit exceeded")
        if path not in self.cache:
            data = _read(path, ARTIFACT_LIMIT)
            self.total += len(data)
            _need(self.total <= TOTAL_LIMIT, "aggregate artifact limit exceeded")
            self.cache[path] = data
        data = self.cache[path]
        _need(_sha(data) == ref["sha256"], "artifact digest mismatch")
        if kind == "output":
            _need(len(data) <= OUTPUT_LIMIT, "output byte limit exceeded")
            data.decode("utf-8")
        return data


    def cited(self, refs, kind):
        for ref in refs:
            self.read(ref, kind)


def _input(case):
    return {
        name: case.get(name, None if name == 'editorial_brief' else [])
        for name in ('sources', 'user_prompts', 'editorial_brief', 'context_material', 'workflow_steps')
    }


def _load(dataset_path, captures_path):
    dataset, dataset_raw = _json(dataset_path)
    _need(
        (
            type(dataset) is dict
            and type(dataset.get('format_version')) is int
            and dataset['format_version'] == 1
        ),
        'wrong dataset version',
    )
    _need(
        (
            dataset.get('dataset_id') == 'lead-editorial-coordination-v1'
            and dataset.get('scoring_dimensions') == list(AXES)
        ),
        'wrong dataset/rubric',
    )
    _need(type(dataset.get("constructed_fixtures")) is bool, "dataset fixture label required")
    cases = dataset["cases"]
    _need(type(cases) is list and len(cases) == 9, "nine baseline cases required")
    _unique([case["case_id"] for case in cases])
    _need({case["case_id"] for case in cases} == set(CASES), "wrong baseline cases")
    for case in cases:
        _need(case["scoring_dimensions"] == list(AXES), "wrong case rubric")
        _need(type(case.get("constructed_fixture")) is bool, "case fixture label required")
        _unique([source["source_id"] for source in case["sources"]])
        for source in case["sources"]:
            _check(
                source,
                {
                    'source_id': ID,
                    'artifact_path': TEXT,
                    'encoding': _enum('utf-8'),
                    'text': ('text', OUTPUT_LIMIT),
                    'sha256': SHA,
                },
            )
            _need(
                _sha(source['text'].encode('utf-8')) == source['sha256'],
                'source text digest mismatch',
            )
    captures, raw = _json(captures_path)
    _check(captures, CAPTURE)
    _need(captures["dataset_sha256"] == _sha(dataset_raw), "dataset digest mismatch")
    ids = [arm["arm_id"] for arm in captures["arms"]]
    _need(len(ids) == 2, "two arms required")
    _unique(ids)
    _unique([case["case_id"] for case in captures["cases"]])
    supplied = {case["case_id"]: case for case in captures["cases"]}
    _need(supplied.keys() <= set(CASES), "unknown capture case")
    artifacts, normalized = _Artifacts(captures_path.parent), {}
    artifacts.read(captures["protocol"]["evidence"], "protocol_record")
    for case in cases:
        entry = supplied.get(case["case_id"], {"runs": {}})
        if "case_sha256" in entry:
            _need(entry["case_sha256"] == _sha(_canonical(case)), "case digest mismatch")
        _need(entry["runs"].keys() <= set(ids), "unknown run arm")
        runs = {}
        for arm in ids:
            run = entry['runs'].get(arm, {'status': 'not_observed', 'reason': 'No capture supplied.'})
            _need(type(run) is dict, "run object required")
            _check(run, MISSING if run.get("status") in {"not_observed", "not_supported"} else RUN)
            if run["status"] in {"not_observed", "not_supported"}:
                run = {
                    **run,
                    'run_kind': None,
                    'conditions': {key: [] if key == 'unknown_settings' else None for key in CONDITIONS},
                    'costs': {
                        **dict.fromkeys(METRICS),
                        'decisions': dict.fromkeys(DECISIONS),
                        'evidence': [],
                    },
                    'workflow_observations': [],
                    'capture_evidence': None,
                    'input_evidence': None,
                }
                output = None
            else:
                _need(run['input_sha256'] == _sha(_canonical(_input(case))), 'input digest mismatch')
                _unique(run["conditions"]["unknown_settings"])
                output = artifacts.read(run["output"], "output")
                _need(run["status"] != "captured" or bool(output), "captured output missing")
                _need(run["status"] != "failed" or run["reason"], "failed run reason missing")
                artifacts.read(run["capture_evidence"], "capture_record")
                artifacts.read(run["input_evidence"], "input_record")
                artifacts.cited(run["costs"]["evidence"], "accounting_record")
                _unique([item["observation_id"] for item in run["workflow_observations"]])
                for item in run["workflow_observations"]:
                    _need(
                        (
                            item['status'] not in {'pass', 'fail'}
                            or (item['rationale'] and item['evidence'])
                        ),
                        'uncited workflow audit',
                    )
                    artifacts.cited(item["evidence"], "workflow_record")
            context = run.get("interaction_context") or {"text": None, "evidence": []}
            artifacts.cited(context["evidence"], "workflow_record")
            counts = run["costs"]["decisions"]
            available = (
                all((value is not None for value in counts.values()))
                and bool(run['costs']['evidence'])
                and bool(context['text'] and context['evidence'])
            )
            partial = any(value is not None for value in counts.values()) or context["text"]
            interaction = {
                'decision_counts': counts,
                'context_text': context['text'],
                'evidence_sha256': {
                    'accounting': [ref['sha256'] for ref in run['costs']['evidence']],
                    'interaction': [ref['sha256'] for ref in context['evidence']],
                },
                'context_status': ('available' if available else 'partial' if partial else 'not_observed'),
                'scoring_note': SCORING_NOTE,
            }
            runs[arm] = {
                **run,
                'output_text': (None if output is None else output.decode('utf-8')),
                'output_sha256': (None if output is None else _sha(output)),
                'output_utf8_bytes': (None if output is None else len(output)),
                'author_visible_interaction': interaction,
            }
        normalized[case["case_id"]] = runs
    return {
        'dataset': dataset,
        'dataset_sha256': _sha(dataset_raw),
        'captures': captures,
        'captures_sha256': _sha(raw),
        'runs': normalized,
        'artifacts': artifacts,
    }


def _prepare(data):
    captures = data["captures"]
    ids = [arm["arm_id"] for arm in captures["arms"]]
    packet = {
        'format_version': 1,
        'comparison_id': captures['comparison_id'],
        'dataset_id': data['dataset']['dataset_id'],
        'dataset_sha256': data['dataset_sha256'],
        'captures_sha256': data['captures_sha256'],
        'rubric_id': 'lead-editorial-rubric-v1',
        'rubric': RUBRIC,
        'constructed_fixtures': data['dataset']['constructed_fixtures'],
        'cases': [],
    }
    mapping = {}
    for case in data["dataset"]["cases"]:
        key = f'{captures["comparison_id"]}\0{captures["seed"]}\0{case["case_id"]}'
        order = ids[::-1] if hashlib.sha256(key.encode("utf-8")).digest()[0] & 1 else ids
        mapping[case["case_id"]] = dict(zip("AB", order))
        sides = {
            side: {
                name: data['runs'][case['case_id']][arm][name]
                for name in (
                                'status',
                                'output_text',
                                'output_sha256',
                                'output_utf8_bytes',
                                'author_visible_interaction',
                            )
            }
            for (side, arm) in mapping[case['case_id']].items()
        }
        packet['cases'].append(
            {
                'case_id': case['case_id'],
                'constructed_fixture': case['constructed_fixture'],
                'case_sha256': _sha(_canonical(case)),
                'input_sha256': _sha(_canonical(_input(case))),
                'source_sha256': {source['source_id']: source['sha256'] for source in case['sources']},
                'prompt_sha256': _sha(_canonical(case.get('user_prompts', []))),
                'input': _input(case),
                'sides': sides,
            },
        )
    return (
        packet,
        {
            'format_version': 1,
            'seed': captures['seed'],
            'captures_sha256': data['captures_sha256'],
            'mapping': mapping,
        },
    )


def _reviews(data, packet, reviews_path):
    reviews, raw = _json(reviews_path)
    _check(reviews, REVIEWS)
    _need(
        (
            reviews['comparison_id'] == packet['comparison_id']
            and reviews['review_packet_sha256'] == _sha(_canonical(packet) + b'\n')
        ),
        'review packet binding mismatch',
    )
    _unique([(item["reviewer_id"], item["case_id"]) for item in reviews["reviews"]])
    indexed = {case["case_id"]: case for case in packet["cases"]}
    for review in reviews["reviews"]:
        _need(review["case_id"] in indexed, "unknown review case")
        case = indexed[review["case_id"]]
        _need(
            sum((item['case_id'] == review['case_id'] for item in reviews['reviews'])) <= 16,
            'reviewer limit exceeded',
        )
        data["artifacts"].read(review["evidence"], "review_record")
        for side in "AB":
            for axis, rating in review["ratings"][side].items():
                if rating["score"] is not None:
                    _need(
                        (case['sides'][side]['output_text'] is not None and rating['rationale']),
                        'score lacks output/rationale',
                    )
                    if axis == "approval_burden" and review["evidence_kind"].startswith("human_"):
                        _need(
                            (
                                case['sides'][side]['author_visible_interaction']['context_status']
                                == 'available'
                            ),
                            'human burden score lacks interaction context',
                        )
        for error in review["important_errors"]:
            _need(
                (error['source_id'] is None or error['source_id'] in case['source_sha256']),
                'unknown error source',
            )
    return reviews["reviews"], _sha(raw)


def _compare(data, packet, mapping, reviews, reviews_sha):
    captures, result = data["captures"], []
    ids = [arm["arm_id"] for arm in captures["arms"]]
    for case, prepared in zip(data["dataset"]["cases"], packet["cases"]):
        runs, labels = data["runs"][case["case_id"]], mapping[case["case_id"]]
        differences, unknown = [], []
        for key in CONDITIONS:
            values = [runs[arm]["conditions"][key] for arm in ids]
            if key == "repository_commit":
                if any(value is None for value in values):
                    unknown.append(key)
                elif values[0] != values[1]:
                    differences.append(key)
            elif key == "unknown_settings":
                if any(values):
                    unknown.append(key)
            elif any(value is None for value in values):
                unknown.append(key)
            elif _canonical(values[0]) != _canonical(values[1]):
                differences.append(key)
        controls = [
            key for key in differences
            if key != "repository_commit" or captures["comparison_kind"] != "before_after"
        ]
        condition_status = "mismatched" if controls else "unknown" if unknown else "matched"
        expected = [
            item['observation_id']
            for item in case.get('expected_observations', [])
            if item['category'] == 'workflow'
        ]
        missing, cost_missing, violations = {}, {}, []
        for arm, run in runs.items():
            audits = {item["observation_id"]: item for item in run["workflow_observations"]}
            missing[arm] = [
                name for name in expected
                if name not in audits or audits[name]['status'] != 'pass'
            ]
            values = {**{name: run["costs"][name] for name in METRICS}, **run["costs"]["decisions"]}
            cost_missing[arm] = [name for name, value in values.items() if value is None]
            if not run["costs"]["evidence"]:
                cost_missing[arm].append("accounting_backing")
            ceiling, calls = run["conditions"]["call_ceiling"], run["costs"]["submitted_calls"]
            if ceiling is not None and calls is not None and calls > ceiling:
                violations.append(arm + ":call_ceiling_exceeded")
            if any(item["status"] == "fail" for item in audits.values()):
                violations.append(arm + ":reported_workflow_failure")
        rows, eligible = [], set()
        for review in (item for item in reviews if item["case_id"] == case["case_id"]):
            by_arm = {labels[side]: review["ratings"][side] for side in "AB"}
            deltas = {
                axis: (
                    None
                    if any((by_arm[arm][axis]['score'] is None for arm in ids))
                    else by_arm[ids[1]][axis]['score'] - by_arm[ids[0]][axis]['score']
                )
                for axis in AXES
            }
            unbacked = [
                side + '.approval_burden'
                for side in 'AB'
                if (
                       review['ratings'][side]['approval_burden']['score'] is not None
                       and (
                           prepared['sides'][side]['author_visible_interaction']['context_status']
                           != 'available'
                       )
                   )
            ]
            rows.append(
                {
                    **review,
                    'unblinded_preference': labels.get(review['preference'], review['preference']),
                    'paired_deltas': deltas,
                    'unbacked_rating_fields': unbacked,
                },
            )
            if (
                review['evidence_kind'] == 'human_blinded'
                and review['evidence']
                and all((value is not None for value in deltas.values()))
                and not unbacked
            ):
                eligible.add(review["role"])
        limitations = [LIMITATION]
        if "repository_commit" in differences and captures["comparison_kind"] == "before_after":
            limitations.append("Declared before/after repository difference is the treatment.")
        if condition_status != "matched":
            limitations.append("Comparison conditions missing or mismatched.")
        if "expected_observations" not in case or any(missing.values()) or violations:
            limitations.append(
                'Workflow plan/evidence incomplete or failed; reported audits are not certified.',
            )
        if any(cost_missing.values()):
            limitations.append("Cost accounting incomplete; unknown is not zero.")
        real = all(
            (
                (
                    run['status'] == 'captured'
                    and run['run_kind'] == 'provider_capture'
                    and run['capture_evidence']
                    and run['input_evidence']
                )
                for run in runs.values()
            ),
        )
        protocol = (captures['protocol']['status'] == 'predeclared' and captures['protocol']['evidence'])
        if not real:
            limitations.append("Actual provider capture/input evidence incomplete; synthetic is not a run.")
        if not protocol:
            limitations.append("Backed predeclared protocol absent; preregistration evidence incomplete.")
        if not {"author_intent", "reader_editor"} <= eligible:
            limitations.append("Complete backed blinded author/reader review evidence incomplete.")
        if any(
            run["costs"]["submitted_calls"] is None or run["conditions"]["call_ceiling"] is None
            for run in runs.values()
        ):
            limitations.append("Call-limit compliance not observed; absent accounting is not zero.")
        complete = (
            real
            and protocol
            and condition_status == 'matched'
            and "expected_observations" in case
            and not any(missing.values())
            and not violations
            and not any(cost_missing.values())
            and {'author_intent', 'reader_editor'} <= eligible
        )
        preferences = {
            (
                "arm" if row["preference"] in {"A", "B"} else "semantic",
                row["unblinded_preference"],
            )
            for row in rows
            if row["preference"] != "not_observed"
        }
        disagreements = {
            axis: len({
                row['paired_deltas'][axis] for row in rows
                if row['paired_deltas'][axis] is not None
            }) > 1
            for axis in AXES
        }
        result.append(
            {
                **{
                    key: prepared[key]
                    for key in (
                                   'case_id',
                                   'case_sha256',
                                   'input_sha256',
                                   'source_sha256',
                                   'prompt_sha256',
                               )
                },
                'runs': runs,
                'side_to_arm': labels,
                'matched_conditions': {
                    'status': condition_status,
                    'differences': differences,
                    'unknown_fields': unknown,
                },
                'workflow': {
                    'expected': expected,
                    'missing_or_nonpassing': missing,
                    'violations': violations,
                },
                'cost_missing': cost_missing,
                'reviews': rows,
                'disagreements': {'preferences': len(preferences) > 1, 'axes': disagreements},
                'evidence_status': ('PAIRED_EVIDENCE_AVAILABLE' if complete else 'INCONCLUSIVE'),
                'limitations': limitations,
            },
        )
    return {
        'format_version': 1,
        'comparison_id': packet['comparison_id'],
        'dataset_sha256': data['dataset_sha256'],
        'captures_sha256': data['captures_sha256'],
        'review_packet_sha256': _sha(_canonical(packet) + b'\n'),
        'reviews_sha256': reviews_sha,
        'comparison_kind': captures['comparison_kind'],
        'treatment': captures['treatment'],
        'arms': captures['arms'],
        'coverage': {
            'case_count': 9,
            'captured_pairs': sum(
                all(run['status'] == 'captured' for run in row['runs'].values())
                for row in result
            ),
        },
        'cases': result,
        'quality_conclusion': (
            'PAIRED_EVIDENCE_AVAILABLE'
            if all((row['evidence_status'] == 'PAIRED_EVIDENCE_AVAILABLE' for row in result))
            else 'INCONCLUSIVE'
        ),
        'improvement_assessment': 'NOT_ASSESSED',
        'limitations': [LIMITATION],
    }


def _markdown(value, blind):
    title = "Blinded editorial review packet" if blind else "Descriptive editorial comparison"
    lines = ["# " + title, "", LIMITATION, ""]
    if not blind:
        lines.extend(['Conclusion: ' + value['quality_conclusion'], 'Improvement: NOT_ASSESSED', ''])
    if blind:
        lines.extend(["## Scoring rubric", "Score range: 0–4; higher is better.", ""])
        for axis, anchors in value["rubric"]["dimensions"].items():
            lines.extend(["### " + axis.replace("_", " "), ""])
            for score, anchor in anchors.items():
                lines.append("- " + score + ": " + anchor)
            lines.append("")
        lines.extend([*value["rubric"]["instructions"], ""])
        fixture = value["constructed_fixtures"]
        lines.extend(["Dataset constructed fixtures: " + str(fixture).lower(), ""])
    for case in value["cases"]:
        lines.extend(["## " + case["case_id"], ""])
        if blind:
            fixture = case["constructed_fixture"]
            lines.extend(["Case constructed fixture: " + str(fixture).lower(), ""])
            lines.extend(
                [
                    '### Exact input',
                    '<pre>' + html.escape(_canonical(case['input']).decode()) + '</pre>',
                    '',
                ],
            )
        sides = case["sides"] if blind else case["runs"]
        for label, side in sides.items():
            lines.extend(["### " + html.escape(label), "Status: " + side["status"], ""])
            if side["output_text"] is None:
                lines.append("Output: not observed.")
            else:
                lines.extend(
                    [
                        'Output SHA256: ' + side['output_sha256'],
                        '<pre>' + html.escape(side['output_text']) + '</pre>',
                    ],
                )
            lines.extend(
                [
                    'Interaction: ' + side['author_visible_interaction']['context_status'],
                    '<pre>' + html.escape(_canonical(side['author_visible_interaction']).decode()) + '</pre>',
                    '',
                ],
            )
        if not blind:
            details = {key: child for key, child in case.items() if key != "runs"}
            run_details = {
                key: {
                    name: child for name, child in run.items()
                    if name != "output_text"
                }
                for key, run in sides.items()
            }
            lines.extend(
                [
                    '### Attributed review, costs and gaps',
                    '<pre>' + html.escape(_canonical(details).decode()) + '</pre>',
                    '<pre>' + html.escape(_canonical(run_details).decode()) + '</pre>',
                    '',
                ],
            )
    return "\n".join(lines).rstrip() + "\n"


def _write(directory, files):
    _need(not directory.exists() and not directory.is_symlink(), "output directory already exists")
    encoded = {
        name: (value.encode('utf-8') if isinstance(value, str) else _canonical(value) + b'\n')
        for (name, value) in files.items()
    }
    _need(all((len(raw) <= PACKET_LIMIT for raw in encoded.values())), 'generated file limit exceeded')
    directory.mkdir(mode=0o700)
    for name, raw in encoded.items():
        with (directory / name).open("xb") as stream:
            stream.write(raw)


class _GradeCancelled(Exception):
    """Call-local cancellation before review publication."""


def _grade_packet(path):
    packet, raw = _json(path, PACKET_LIMIT)
    interaction = {
        'decision_counts': {name: _nullable(COUNT) for name in DECISIONS},
        'context_text': _nullable(TEXT),
        'evidence_sha256': {name: ('list', SHA, 32) for name in ('accounting', 'interaction')},
        'context_status': _enum('available', 'partial', 'not_observed'),
        'scoring_note': TEXT,
    }
    side = {'status': _enum('captured', 'failed', 'not_observed', 'not_supported'),
            'output_text': ('any',), 'output_sha256': _nullable(SHA),
            'output_utf8_bytes': _nullable(('int', 0, OUTPUT_LIMIT)),
            'author_visible_interaction': interaction}
    source = {'source_id': ID, 'artifact_path': TEXT, 'text': ('text', OUTPUT_LIMIT),
              'encoding': _enum('utf-8'), 'sha256': SHA}
    case = {'case_id': ID, 'constructed_fixture': ('any',), 'case_sha256': SHA,
            'input_sha256': SHA, 'source_sha256': ('map', SHA, 32), 'prompt_sha256': SHA,
            'input': {'sources': ('list', source, 32), 'user_prompts': ('list', ('text', OUTPUT_LIMIT), 128),
                      'editorial_brief': ('any',), 'context_material': ('list', ('any',), 128),
                      'workflow_steps': ('list', ('any',), 128)},
            'sides': {label: side for label in 'AB'}}
    _check(packet, {'format_version': VERSION, 'comparison_id': ID, 'dataset_id': SMALL,
                    'dataset_sha256': SHA, 'captures_sha256': SHA, 'rubric_id': SMALL,
                    'rubric': ('any',), 'constructed_fixtures': ('any',), 'cases': ('list', case, 9)})
    _need(raw == _canonical(packet) + b'\n', 'packet is not canonical current JSON')
    _need(packet['dataset_id'] == 'lead-editorial-coordination-v1' and
          packet['rubric_id'] == 'lead-editorial-rubric-v1' and packet['rubric'] == RUBRIC,
          'wrong packet dataset/rubric')
    _need(type(packet['constructed_fixtures']) is bool, 'packet fixture label required')
    _need(len(packet['cases']) == 9 and {item['case_id'] for item in packet['cases']} == set(CASES),
          'complete nine-case packet required')
    for item in packet['cases']:
        _need(type(item['constructed_fixture']) is bool, 'case fixture label required')
        inputs = item['input']
        _need(inputs['editorial_brief'] is None or type(inputs['editorial_brief']) is dict,
              'invalid editorial brief')
        _need(all(type(value) is dict for key in ('context_material', 'workflow_steps')
                  for value in inputs[key]), 'invalid context/workflow input')
        pending, count = [(inputs, 0)], 0
        while pending:
            value, depth = pending.pop()
            count += 1
            _need(depth <= 16 and count <= 4096, 'packet input structure too large')
            if type(value) is dict:
                _need(len(value) <= 128 and all(type(key) is str for key in value), 'invalid input object')
                pending.extend((part, depth + 1) for pair in value.items() for part in pair)
            elif type(value) is list:
                _need(len(value) <= 128, 'input array too large')
                pending.extend((part, depth + 1) for part in value)
            elif type(value) is str:
                _need(len(value.encode('utf-8')) <= OUTPUT_LIMIT, 'input text too large')
            else:
                _need(value is None or type(value) in (bool, int, float), 'invalid input value')
        _need(item['input_sha256'] == _sha(_canonical(inputs)) and
              item['prompt_sha256'] == _sha(_canonical(inputs['user_prompts'])), 'packet input digest mismatch')
        sources = inputs['sources']
        _unique([entry['source_id'] for entry in sources])
        _need(item['source_sha256'] == {entry['source_id']: entry['sha256'] for entry in sources},
              'packet source map mismatch')
        for entry in sources:
            _need(entry['sha256'] == _sha(entry['text'].encode('utf-8')), 'packet source digest mismatch')
        for value in item['sides'].values():
            text = value['output_text']
            if value['status'] != 'captured':
                _need(text is None and value['output_sha256'] is None and value['output_utf8_bytes'] is None,
                      'noncaptured side has output')
            else:
                _need(type(text) is str and bool(text) and len(text.encode('utf-8')) <= OUTPUT_LIMIT,
                      'invalid captured output')
                _need(value['output_sha256'] == _sha(text.encode('utf-8')) and
                      value['output_utf8_bytes'] == len(text.encode('utf-8')), 'packet output digest mismatch')
            view = value['author_visible_interaction']
            counts = view['decision_counts']
            available = (all(number is not None for number in counts.values()) and
                         bool(view['evidence_sha256']['accounting']) and bool(view['context_text']) and
                         bool(view['evidence_sha256']['interaction']))
            partial = any(number is not None for number in counts.values()) or bool(view['context_text'])
            status = 'available' if available else 'partial' if partial else 'not_observed'
            _need(view['context_status'] == status and view['scoring_note'] == SCORING_NOTE,
                  'packet interaction status mismatch')
    return packet, _sha(raw)


def _grade_ask(prompt, spec):
    while True:
        try:
            value = input(prompt + ': ')
        except (EOFError, KeyboardInterrupt) as error:
            raise _GradeCancelled() from error
        if value.strip() == '/cancel':
            raise _GradeCancelled()
        try:
            _check(value, spec)
        except (ValueError, TypeError):
            print('Invalid answer; correct it or type /cancel.')
            continue
        return value


def _grade_display(case, rubric):
    print('\nCase: ' + case['case_id'])
    print('Constructed source fixture: ' + str(case['constructed_fixture']).lower())
    print('Exact brief/source/context (JSON; escapes preserve control characters):')
    print(json.dumps(case['input'], ensure_ascii=False, indent=2))
    for side, supplied in case['sides'].items():
        print('\nAnonymous ' + side + ' (' + supplied['status'] + ')')
        print('Exact output: ' + json.dumps(supplied['output_text'], ensure_ascii=False))
        print('Author-visible interaction: ' + _canonical(supplied['author_visible_interaction']).decode('utf-8'))
    for axis in AXES:
        print('\n' + axis)
        for score, anchor in rubric['dimensions'][axis].items():
            print(score + ': ' + anchor)
    for instruction in rubric['instructions']:
        print(instruction)


def _grade_case(case, reviewer_id, role, evidence_kind):
    _grade_display(case, RUBRIC)
    ratings = {}
    for side in 'AB':
        ratings[side] = {}
        supplied = case['sides'][side]
        for axis in AXES:
            unavailable = (supplied['output_text'] is None or
                           (axis == 'approval_burden' and supplied['author_visible_interaction']['context_status'] != 'available'))
            if unavailable:
                print(side + ' ' + axis + ': unobserved; required evidence is absent.')
                score = None
            else:
                value = _grade_ask(side + ' ' + axis + ' score (0..4 or unobserved)',
                                   _enum('0', '1', '2', '3', '4', 'unobserved', 'null'))
                score = None if value in {'unobserved', 'null'} else int(value)
            reason = _grade_ask(side + ' ' + axis + ' reason/passages (or why unobserved)', ('text', 2048))
            uncertainty = _grade_ask(side + ' ' + axis + ' uncertainty (use none if none)', ('text', 1024))
            ratings[side][axis] = {'score': score, 'rationale': reason + '\nUncertainty: ' + uncertainty}
    preference = _grade_ask('Preference', _enum('A', 'B', 'tie', 'not_observed'))
    errors = []
    while len(errors) < 32 and _grade_ask('Record an important error? yes/no', _enum('yes', 'no')) == 'yes':
        side = _grade_ask('Error side', _enum('A', 'B'))
        source = _grade_ask('Error source (source ID or none)', _enum('none', *case['source_sha256']))
        errors.append({'side': side, 'source_id': None if source == 'none' else source,
                       'description': _grade_ask('Error description', TEXT),
                       'impact': _grade_ask('Error impact', TEXT),
                       'disposition': _grade_ask('Error disposition', _enum('resolved', 'deferred', 'rejected', 'unresolved')),
                       'reason': _grade_ask('Error disposition reason', TEXT)})
    return {'reviewer_id': reviewer_id, 'role': role, 'evidence_kind': evidence_kind,
            'case_id': case['case_id'], 'ratings': ratings, 'preference': preference,
            'important_errors': errors, 'evidence': None}


def _grade_identity():
    print('Self-attribution is recorded, not authenticated. Use your own independent participant ID.')
    reviewer = _grade_ask('Reviewer ID', ID)
    role = _grade_ask('Reviewer role', _enum('author_intent', 'reader_editor'))
    blinded = _grade_ask('Have you viewed only anonymous material, without arm/model/cost attribution? yes/no', _enum('yes', 'no'))
    _grade_ask('Confirm these are your independent judgments: yes', _enum('yes'))
    return reviewer, role, 'human_blinded' if blinded == 'yes' else 'human_unblinded'


def _grade_validate(packet, reviews, packet_sha):
    _check(reviews, REVIEWS)
    _need(reviews['comparison_id'] == packet['comparison_id']
          and reviews['review_packet_sha256'] == packet_sha, 'review packet binding mismatch')
    _need(len(reviews['reviews']) == 9, 'complete participant review required')
    _unique([row['case_id'] for row in reviews['reviews']])
    _need({row['case_id'] for row in reviews['reviews']} == set(CASES), 'review case mismatch')
    _need(len({(row['reviewer_id'], row['role'], row['evidence_kind']) for row in reviews['reviews']}) == 1,
          'participant identity changed')
    indexed = {row['case_id']: row for row in packet['cases']}
    for review in reviews['reviews']:
        case = indexed[review['case_id']]
        for side in 'AB':
            for axis, rating in review['ratings'][side].items():
                _need(rating['rationale'] is not None, 'rating reason/uncertainty missing')
                if rating['score'] is not None:
                    _need(case['sides'][side]['output_text'] is not None, 'score lacks output')
                    if axis == 'approval_burden':
                        _need(case['sides'][side]['author_visible_interaction']['context_status'] == 'available',
                              'human burden score lacks interaction context')
        for error in review['important_errors']:
            _need(error['source_id'] is None or error['source_id'] in case['source_sha256'], 'unknown error source')


def _grade(packet_path, directory):
    print('For later compare ingestion, --out-dir must be a direct child of the capture artifact root.')
    _need(not directory.exists() and not directory.is_symlink(), 'output directory exists')
    _need(directory.name not in {'', '.', '..'}, 'invalid output directory name')
    packet, digest = _grade_packet(packet_path)
    print('Blinded participant grading. ' + LIMITATION)
    print('Use /cancel or EOF at any prompt to stop without publishing a complete review.')
    reviewer, role, kind = _grade_identity()
    reviews = []
    for case in packet['cases']:
        while True:
            row = _grade_case(case, reviewer, role, kind)
            choice = _grade_ask('Case review: accept/edit', _enum('accept', 'edit'))
            if choice == 'accept':
                reviews.append(row)
                break
    while True:
        print('Recorded all nine cases for ' + reviewer + ' (' + role + ').')
        choice = _grade_ask('Final review: save/edit/identity', _enum('save', 'edit', 'identity'))
        if choice == 'save':
            break
        if choice == 'identity':
            reviewer, role, kind = _grade_identity()
            for row in reviews:
                row.update(reviewer_id=reviewer, role=role, evidence_kind=kind)
        else:
            case_id = _grade_ask('Case to correct', _enum(*CASES))
            case = next(case for case in packet['cases'] if case['case_id'] == case_id)
            index = next(index for index, row in enumerate(reviews) if row['case_id'] == case_id)
            reviews[index] = _grade_case(case, reviewer, role, kind)
    evidence = {'format_version': 1, 'comparison_id': packet['comparison_id'], 'review_packet_sha256': digest,
                'reviewer_id': reviewer, 'role': role, 'self_attributed': True, 'identity_authenticated': False,
                'constructed_fixtures': packet['constructed_fixtures'], 'reviews': reviews}
    evidence_sha = _sha(_canonical(evidence) + b'\n')
    # A JSON round-trip keeps the immutable evidence bytes independent of later review references.
    evidence = json.loads(_canonical(evidence))
    ref = {'kind': 'review_record', 'path': directory.name + '/review-record.json', 'sha256': evidence_sha}
    for row in reviews:
        row['evidence'] = dict(ref)
    artifact = {'format_version': 1, 'comparison_id': packet['comparison_id'],
                'review_packet_sha256': digest, 'reviews': reviews}
    _grade_validate(packet, artifact, digest)
    _write(directory, {'review-record.json': evidence, 'reviews.json': artifact})
    print('Complete packet-bound review published. Evidence paths expect this directory under the capture artifact root.')
    return 0




def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "compare", "grade"))
    for name in ("dataset", "captures", "out-dir"):
        parser.add_argument("--" + name, required=name == "out-dir", type=Path,
                            help="grade output must be a direct child of the capture artifact root" if name == "out-dir" else None)
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--reviews", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "grade":
            _need(args.packet is not None and not (args.dataset or args.captures or args.reviews),
                  "grade requires only --packet and --out-dir")
            return _grade(args.packet, args.out_dir)
        _need(args.dataset is not None and args.captures is not None, "dataset/captures required")
        _need(
            (
                (args.command == 'compare') == bool(args.packet and args.reviews)
                and (args.command == 'compare' or not (args.packet or args.reviews))
            ),
            'compare requires packet/reviews; prepare excludes them',
        )
        data = _load(args.dataset, args.captures)
        packet, blind_map = _prepare(data)
        if args.command == "prepare":
            files = {
                'review-packet.json': packet,
                'review-packet.md': _markdown(packet, True),
                'blind-map.json': blind_map,
            }
        else:
            supplied, raw = _json(args.packet, PACKET_LIMIT)
            _need(
                (raw == _canonical(packet) + b'\n' and supplied == packet),
                'stale or changed prepared packet',
            )
            reviews, digest = _reviews(data, packet, args.reviews)
            report = _compare(data, packet, blind_map["mapping"], reviews, digest)
            files = {'comparison-report.json': report, 'comparison-report.md': _markdown(report, False)}
        _write(args.out_dir, files)
        print("Generated " + ", ".join(files) + "; no agents or workflow controls executed.")
        return 0
    except _GradeCancelled:
        print("Grading cancelled; no complete review was published.", file=sys.stderr)
        return 3
    except (ValueError, KeyError, TypeError, RecursionError, OverflowError) as error:
        print("Invalid offline input: " + str(error), file=sys.stderr)
        return 2
    except OSError as error:
        print("Offline filesystem failure: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
