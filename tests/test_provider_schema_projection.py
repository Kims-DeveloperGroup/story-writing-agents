from __future__ import annotations
import unittest
import copy
from story_writing_agents.provider_schema_projection import factor_rule_schema
from story_writing_agents.provider_schema_projection import hoist_schema_definitions, require_schema_projection
from story_writing_agents.provider_message_catalog import WireShapeError
from story_writing_agents.structured_output_validation import SchemaRules
from story_writing_agents.provider_schema_projection import rebase_schema_definitions


class ProviderSchemaProjectionTests(unittest.TestCase):
    def test_small_factoring_annotations_and_owner_refs_are_exact(self) -> None:
        # Ref spelling and lossless factoring preserve independently supplied neighboring values.
        graph = {'type': 'object', 'properties': {'left': {'$ref': '#/$defs/value'},
            'right': {'$ref': '#/$defs/alias'}}, 'required': ['left', 'right'],
            'additionalProperties': False, '$defs': {'value': {'type': 'integer', 'enum': [1]},
                                                  'alias': {'$ref': '#/$defs/value'}}}
        compact = factor_rule_schema(hoist_schema_definitions(graph))
        original_rules, compact_rules = SchemaRules.declare(graph), SchemaRules.declare(compact)
        for supplied, valid in (({'left': 1, 'right': 1}, True), ({'left': True, 'right': 1}, False),
                ({'left': 1, 'right': None}, False), ({'left': 1}, False),
                ({'left': 1, 'right': 2}, False), ({'left': 1, 'right': 1, 'extra': 1}, False)):
            self.assertEqual(not original_rules.findings(supplied), valid); self.assertEqual(not compact_rules.findings(supplied), valid)
        self.assertEqual(original_rules.node_fingerprint(), compact_rules.node_fingerprint())
        require_schema_projection(graph, graph)
        require_schema_projection(compact, compact)
        with self.assertRaises(WireShapeError): require_schema_projection(graph, compact)
        # Literal annotation overlays preserve one annotation and every nested annotation.
        inline = {'type': 'string', 'minLength': 2, 'maxLength': 3, 'description': 'Literal annotation.'}
        overlay = {'$ref': '#/$defs/text', 'description': 'Literal annotation.',
                   '$defs': {'text': {'type': 'string', 'minLength': 2, 'maxLength': 3}}}
        inline_rules, overlay_rules = SchemaRules.declare(inline), SchemaRules.declare(overlay)
        inline_fingerprint = inline_rules.node_fingerprint()
        self.assertEqual(inline_fingerprint, overlay_rules.node_fingerprint())
        for value, valid in (('ab', True), ('abc', True), ('a', False), ('abcd', False), (None, False), (2, False)):
            self.assertEqual(not inline_rules.findings(value), valid); self.assertEqual(not overlay_rules.findings(value), valid)
        distinct = ({**inline, 'description': 'Different annotation.'},
                    {**inline, 'minLength': 3}, {**inline, 'enum': ['ab']}, {**inline, 'enum': ['ac']})
        identities = {SchemaRules.declare(rule).node_fingerprint() for rule in distinct}
        self.assertEqual(len({inline_fingerprint, *identities}), 5); self.assertTrue(SchemaRules.declare({**inline, 'minLength': 3}).findings('ab'))
        twice = {**overlay, '$defs': {'text': dict(inline)}}; twice_rules = SchemaRules.declare(twice)
        self.assertNotEqual(inline_fingerprint, twice_rules.node_fingerprint())
        nested = copy.deepcopy(twice); nested['$defs']['text']['description'] = 'Changed inner annotation.'
        self.assertNotEqual(twice_rules.node_fingerprint(), SchemaRules.declare(nested).node_fingerprint())
        ordered = SchemaRules.declare({'anyOf': [{'type': 'integer'}, {'type': 'string'}]})
        reversed_order = SchemaRules.declare({'anyOf': [{'type': 'string'}, {'type': 'integer'}]})
        self.assertNotEqual(ordered.node_fingerprint(), reversed_order.node_fingerprint())
        for unused in ({'$ref': '#/$defs/unknown'}, {'$ref': '#/$defs/unused'}):
            unused_graph = {**overlay, '$defs': {**overlay['$defs'], 'unused': unused}}
            with self.assertRaises(ValueError): SchemaRules.declare(unused_graph)
        for bad_ref in ({'$ref': '#/$defs/unknown'}, {'$ref': '#/$defs/value', 'minimum': 0}):
            bad_graph = copy.deepcopy(graph); bad_graph['properties']['left'] = bad_ref
            with self.assertRaises(ValueError): SchemaRules.declare(bad_graph)
        cycle = copy.deepcopy(graph)
        cycle['$defs']['value'] = {'$ref': '#/$defs/alias'}
        with self.assertRaises(ValueError): SchemaRules.declare(cycle)
        lossy = copy.deepcopy(compact); lossy['additionalProperties'] = True
        with self.assertRaises(WireShapeError): require_schema_projection(compact, lossy)
        # Identical local names in different owners preserve their distinct literal constraints.
        local_integer = {'type': 'object', 'properties': {'value': {'$ref': '#/$defs/local'}},
            'required': ['value'], 'additionalProperties': False, '$defs': {'local': {'type': 'integer', 'enum': [1]}}}
        local_text = {'type': 'object', 'properties': {'value': {'$ref': '#/$defs/local'}},
            'required': ['value'], 'additionalProperties': False,
            '$defs': {'local': {'type': 'string', 'enum': ['literal']}}}
        different_owners = {'type': 'object', 'properties': { 'integer_owner': local_integer, 'text_owner': local_text},
            'required': ['integer_owner', 'text_owner'], 'additionalProperties': False}
        with self.assertRaises(WireShapeError): hoist_schema_definitions(different_owners)
        associated_components = {**different_owners, 'properties': {
            'integer_owner': rebase_schema_definitions(local_integer, 'fixture_integer'),
            'text_owner': rebase_schema_definitions(local_text, 'fixture_text')}}
        for invalid_namespace in ('', '../fixture', 'fixture-integer', '1fixture'):
            with self.assertRaises(WireShapeError):
                rebase_schema_definitions(local_integer, invalid_namespace)
        associated = hoist_schema_definitions(associated_components); associated_rules = SchemaRules.declare(associated)
        valid_owners = {'integer_owner': {'value': 1}, 'text_owner': {'value': 'literal'}}
        self.assertFalse(associated_rules.findings(valid_owners))
        for wrong_owner in ( {'integer_owner': {'value': 'literal'}, 'text_owner': {'value': 'literal'}},
                {'integer_owner': {'value': 1}, 'text_owner': {'value': 1}},
                {'integer_owner': {'value': True}, 'text_owner': {'value': 'literal'}}):
            self.assertTrue(associated_rules.findings(wrong_owner))
        rooted = {'$ref': '#/$defs/root', '$defs': {'root': associated_components}}
        rooted_rules = SchemaRules.declare(hoist_schema_definitions(rooted))
        self.assertFalse(rooted_rules.findings(valid_owners)); self.assertTrue(rooted_rules.findings({'integer_owner': {'value': 2}, 'text_owner': {'value': 'literal'}}))
        flattened = factor_rule_schema(associated)
        self.assertEqual(associated_rules.node_fingerprint(), SchemaRules.declare(flattened).node_fingerprint())
        require_schema_projection(associated, associated)
        require_schema_projection(flattened, flattened)
        with self.assertRaises(WireShapeError): require_schema_projection(associated, flattened)
        self.assertFalse(SchemaRules.declare(flattened).findings(valid_owners))
        base_component = { 'type' : 'object' , 'properties' : { 'schema_id' : { '$ref' :
            '#/$defs/identity' } } , 'required' : [ 'schema_id' ] , 'additionalProperties' : False ,
            '$defs' : { 'identity' : { 'type' : 'string' , 'enum' : [ 'outer-binding' ] } } }
        semantic_component = { 'type' : 'object' , 'properties' : { 'schema_id' : { '$ref' :
            '#/$defs/identity' } } , 'required' : [ 'schema_id' ] , 'additionalProperties' : False ,
            '$defs' : { 'identity' : { 'type' : 'string' , 'enum' : [ 'inner-binding' ] } } }
        pair_graph = {'type': 'object', 'properties': {
            'base': rebase_schema_definitions(base_component, 'fixture_base'),
            'semantic': rebase_schema_definitions(semantic_component, 'fixture_semantic')},
            'required': ['base', 'semantic'], 'additionalProperties': False}
        paired_rules = SchemaRules.declare(hoist_schema_definitions(pair_graph))
        self.assertFalse(paired_rules.findings({'base': {'schema_id': 'outer-binding'},
                                               'semantic': {'schema_id': 'inner-binding'}}))
        for swapped in ( ({'base': {'schema_id': 'inner-binding'}, 'semantic': {'schema_id': 'inner-binding'}},
            {'base': {'schema_id': 'outer-binding'}, 'semantic': {'schema_id': 'outer-binding'}})):
            self.assertTrue(paired_rules.findings(swapped))
