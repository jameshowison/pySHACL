# -*- coding: utf-8 -*-
#
from decimal import Decimal
import rdflib
import logging

from pyshacl.consts import *
from pyshacl.errors import ShapeLoadError, ReportableRuntimeError, ConstraintLoadWarning, ConstraintLoadError
from pyshacl.constraints import ALL_CONSTRAINT_PARAMETERS, \
    CONSTRAINT_PARAMETERS_MAP
from pyshacl.sparql_query_helper import SPARQLQueryHelper

class Shape(object):

    def __init__(self, sg, node, p=False, path=None, logger=None):
        """
        Shape
        :type sg: pyshacl.shapes_graph.ShapesGraph
        :type node: rdflib.term.Node
        :type p: bool
        :type path: rdflib.Node
        :type logger: logging.Logger
        """
        self.logger = logger or logging.getLogger(__name__)
        self.sg = sg
        self.node = node
        self._p = p
        self._path = path
        self._advanced = False

        deactivated_vals = set(self.objects(SH_deactivated))
        if len(deactivated_vals) > 1:
            # TODO:coverage: we don't have any tests for invalid shapes
            raise ShapeLoadError("A SHACL Shape cannot have more than one sh:deactivated predicate.",
                                 "https://www.w3.org/TR/shacl/#deactivated")
        elif len(deactivated_vals) < 1:
            self._deactivated = False
        else:
            d = next(iter(deactivated_vals))
            if not isinstance(d, rdflib.Literal):
                # TODO:coverage: we don't have any tests for invalid shapes
                raise ShapeLoadError(
                    "The value of sh:deactivated predicate on a SHACL Shape must be a Literal.",
                    "https://www.w3.org/TR/shacl/#deactivated")
            self._deactivated = bool(d.value)
        severity = set(self.objects(SH_severity))
        if len(severity):
            self._severity = next(iter(severity))
        else:
            self._severity = SH_Violation
        messages = set(self.objects(SH_message))
        if len(messages):
            self._messages = messages
        else:
            self._messages = None
        names = set(self.objects(SH_name))
        if len(names):
            self._names = iter(names)
        else:
            self._names = None
        descriptions = set(self.objects(SH_description))
        if len(descriptions):
            self._descriptions = iter(descriptions)
        else:
            self._descriptions = None

    def set_advanced(self, val):
        self._advanced = bool(val)

    def get_other_shape(self, shape_node):
        try:
            return self.sg.lookup_shape_from_node(shape_node)
        except (KeyError, AttributeError):
            # TODO:coverage: we never hit this during a successful test run
            return None

    @property
    def is_property_shape(self):
        return bool(self._p)

    def property_shapes(self):
        # TODO:coverage: this is never used?
        return self.sg.graph.objects(self.node, SH_property)

    @property
    def deactivated(self):
        return self._deactivated

    @property
    def severity(self):
        return self._severity

    @property
    def message(self):
        if self._messages is None:
            return
        for m in self._messages:
            yield m

    @property
    def name(self):
        # TODO:coverage: this is never used?
        if self._names is None:
            return
        for n in self._names:
            yield n

    @property
    def description(self):
        # TODO:coverage: this is never used?
        if self._descriptions is None:
            return
        for d in self._descriptions:
            yield d

    def objects(self, predicate=None):
        return self.sg.graph.objects(self.node, predicate)

    @property
    def order(self):
        order_nodes = list(self.objects(SH_order))
        if len(order_nodes) < 1:
            return Decimal("0.0")
        if len(order_nodes) > 1:
            raise ShapeLoadError(
                "A SHACL Shape can have only one sh:order property.",
                "https://www.w3.org/TR/shacl-af/#rules-order")
        order_node = next(iter(order_nodes))
        if not isinstance(order_node, rdflib.Literal):
            raise ShapeLoadError(
                "A SHACL Shape must be a numeric literal.",
                "https://www.w3.org/TR/shacl-af/#rules-order")
        return Decimal(order_node.value)

    def target_nodes(self):
        return self.sg.graph.objects(self.node, SH_targetNode)

    def target_classes(self):
        return self.sg.graph.objects(self.node, SH_targetClass)

    def implicit_class_targets(self):
        types = list(self.sg.graph.objects(self.node, RDF_type))
        subclasses = list(self.sg.graph.subjects(RDFS_subClassOf, RDFS_Class))
        subclasses.append(RDFS_Class)
        for t in types:
            if t in subclasses:
                return [self.node]
        return []

    def target_objects_of(self):
        return self.sg.graph.objects(self.node, SH_targetObjectsOf)

    def target_subjects_of(self):
        return self.sg.graph.objects(self.node, SH_targetSubjectsOf)

    def path(self):
        if not self.is_property_shape:
            return None
        if self._path is not None:
            return self._path
        raise RuntimeError("property shape has no _path!")  # pragma: no cover

    def parameters(self):
        return (p for p, v in self.sg.predicate_objects(self.node)
                if p in ALL_CONSTRAINT_PARAMETERS)

    def target(self):
        """

        :type target_graph: rdflib.Graph
        """
        target_nodes = self.target_nodes()
        target_classes = self.target_classes()
        implicit_targets = self.implicit_class_targets()
        target_objects_of = self.target_objects_of()
        target_subjects_of = self.target_subjects_of()
        return (target_nodes, target_classes, implicit_targets,
                target_objects_of, target_subjects_of)

    def advanced_target(self):
        custom_targets = set(self.sg.graph.objects(self.node, SH_target))
        result_set = dict()
        for c in custom_targets:
            ct = dict()
            is_types = set(self.sg.graph.objects(c, RDF_type))
            is_target_type = False
            parameters = set(self.sg.graph.objects(c, SH_parameter))
            if SH_SPARQLTargetType in is_types or len(parameters) > 0:
                is_target_type = True
            ct['type'] = SH_SPARQLTargetType if is_target_type else SH_SPARQLTarget
            selects = set(self.sg.graph.objects(c, SH_select))
            if len(selects) < 1:
                continue
            ct['select'] = next(iter(selects))
            qh = SPARQLQueryHelper(self, c, ct['select'], deactivated=self._deactivated)
            ct['qh'] = qh
            qh.collect_prefixes()
            result_set[c] = ct
        return result_set

    def focus_nodes(self, target_graph):
        """
        The set of focus nodes for a shape may be identified as follows:

        specified in a shape using target declarations
        specified in any constraint that references a shape in parameters of shape-expecting constraint parameters (e.g. sh:node)
        specified as explicit input to the SHACL processor for validating a specific RDF term against a shape
        :return:
        """
        (target_nodes, target_classes, implicit_classes,
         target_objects_of, target_subjects_of) = self.target()
        if self._advanced:
            advanced_targets = self.advanced_target()
        else:
            advanced_targets = False
        found_node_targets = set()
        for n in iter(target_nodes):
            # Note, a node_target _can_ be a literal.
            if n in iter(target_graph.subjects()):
                found_node_targets.add(n)
                continue
            elif n in iter(target_graph.predicates()):
                found_node_targets.add(n)
                continue
            elif n in iter(target_graph.objects()):
                found_node_targets.add(n)
                continue
        target_classes = set(target_classes)
        target_classes.update(set(implicit_classes))
        found_target_instances = set()
        for tc in target_classes:
            s = target_graph.subjects(RDF_type, tc)
            found_target_instances.update(s)
            subc = target_graph.subjects(RDFS_subClassOf, tc)
            for subclass in iter(subc):
                if subclass == tc:
                    continue
                s1 = target_graph.subjects(RDF_type, subclass)
                found_target_instances.update(s1)
        found_node_targets.update(found_target_instances)
        found_target_subject_of = set()
        for s_of in target_subjects_of:
            subs = {s for s, o in target_graph.subject_objects(s_of)}
            found_target_subject_of.update(subs)
        found_node_targets.update(found_target_subject_of)
        found_target_object_of = set()
        for o_of in target_objects_of:
            objs = {o for s, o in target_graph.subject_objects(o_of)}
            found_target_object_of.update(objs)
        found_node_targets.update(found_target_object_of)
        if advanced_targets:
            for at_node, at in advanced_targets.items():
                if at['type'] == SH_SPARQLTargetType:
                    # SPARQLTargetType not supported yet
                    continue
                qh = at['qh']
                c = qh.apply_prefixes(at['select'])
                results = target_graph.query(c, initBindings=None)
                if not results or len(results.bindings) < 1:
                    continue
                for r in results:
                    t = r['this']
                    found_node_targets.add(t)

        return found_node_targets

    @classmethod
    def value_nodes_from_path(cls, sg, focus, path_val, target_graph, recursion=0):
        # Link: https://www.w3.org/TR/shacl/#property-paths
        if isinstance(path_val, rdflib.URIRef):
            return set(target_graph.objects(focus, path_val))
        elif isinstance(path_val, rdflib.Literal):
            raise ReportableRuntimeError(
                    "Values of a property path cannot be a Literal.")
        # At this point, path_val _must_ be a BNode
        # TODO, the path_val BNode must be value of exactly one sh:path subject in the SG.
        if recursion >= 10:
            raise ReportableRuntimeError("Path traversal depth is too much!")
        find_list = set(sg.graph.objects(path_val, RDF.first))
        if len(find_list) > 0:
            first_node = next(iter(find_list))
            rest_nodes = set(sg.graph.objects(path_val, RDF.rest))
            go_deeper = True
            if len(rest_nodes) < 1:
                if recursion == 0:
                    raise ReportableRuntimeError(
                        "A list of SHACL Paths must contain at least "
                        "two path items.")
                else:
                    go_deeper = False
            rest_node = next(iter(rest_nodes))
            if rest_node == RDF.nil:
                if recursion == 0:
                    raise ReportableRuntimeError(
                        "A list of SHACL Paths must contain at least "
                        "two path items.")
                else:
                    go_deeper = False
            this_level_nodes = cls.value_nodes_from_path(sg,
                focus, first_node, target_graph, recursion=recursion+1)
            if not go_deeper:
                return this_level_nodes
            found_value_nodes = set()
            for tln in iter(this_level_nodes):
                value_nodes = cls.value_nodes_from_path(sg,
                    tln, rest_node, target_graph, recursion=recursion+1)
                found_value_nodes.update(value_nodes)
            return found_value_nodes

        find_inverse = set(sg.graph.objects(path_val, SH_inversePath))
        if len(find_inverse) > 0:
            inverse_path = next(iter(find_inverse))
            return set(target_graph.subjects(inverse_path, focus))

        find_alternatives = set(sg.graph.objects(path_val, SH_alternativePath))
        if len(find_alternatives) > 0:
            alternatives_list = next(iter(find_alternatives))
            all_collected = set()
            visited_alternatives = 0
            for a in sg.graph.items(alternatives_list):
                found_nodes = cls.value_nodes_from_path(sg,
                    focus, a, target_graph, recursion=recursion+1)
                visited_alternatives += 1
                all_collected.update(found_nodes)
            if visited_alternatives < 2:
                raise ReportableRuntimeError(
                    "List of SHACL alternate paths "
                    "must have alt least two path items.")
            return all_collected

        find_zero_or_more = set(sg.graph.objects(path_val, SH_zeroOrMorePath))
        if len(find_zero_or_more) > 0:
            zero_or_more_path = next(iter(find_zero_or_more))
            collection_set = set()
            # Note, the zero-or-more path always includes the current subject too!
            collection_set.add(focus)
            found_nodes = cls.value_nodes_from_path(sg,
                focus, zero_or_more_path, target_graph, recursion=recursion+1)
            search_deeper_nodes = set(iter(found_nodes))
            while len(search_deeper_nodes) > 0:
                current_node = search_deeper_nodes.pop()
                if current_node in collection_set:
                    continue
                collection_set.add(current_node)
                found_more_nodes = cls.value_nodes_from_path(sg,
                    current_node, zero_or_more_path, target_graph,
                    recursion=recursion+1)
                search_deeper_nodes.update(found_more_nodes)
            return collection_set

        find_one_or_more = set(sg.graph.objects(path_val, SH_oneOrMorePath))
        if len(find_one_or_more) > 0:
            one_or_more_path = next(iter(find_one_or_more))
            collection_set = set()
            found_nodes = cls.value_nodes_from_path(sg,
                focus, one_or_more_path, target_graph, recursion=recursion + 1)
            # Note, the one-or-more path should _not_ include the current focus
            search_deeper_nodes = set(iter(found_nodes))
            while len(search_deeper_nodes) > 0:
                current_node = search_deeper_nodes.pop()
                if current_node in collection_set:
                    continue
                collection_set.add(current_node)
                found_more_nodes = cls.value_nodes_from_path(sg,
                    current_node, one_or_more_path, target_graph,
                    recursion=recursion + 1)
                search_deeper_nodes.update(found_more_nodes)
            return collection_set

        find_zero_or_one = set(sg.graph.objects(path_val, SH_zeroOrOnePath))
        if len(find_zero_or_one) > 0:
            zero_or_one_path = next(iter(find_zero_or_one))
            collection_set = set()
            # Note, the zero-or-one path always includes the current subject too!
            collection_set.add(focus)
            found_nodes = cls.value_nodes_from_path(sg,
                focus, zero_or_one_path, target_graph, recursion=recursion+1)
            collection_set.update(found_nodes)
            return collection_set

        raise NotImplementedError(
            "That path method to get value nodes of property shapes is not yet implemented.")

    def value_nodes(self, target_graph, focus):
        """
        For each focus node, you can get a set of value nodes.
        For a Node Shape, each focus node has just one value node,
            which is just the focus_node
        :param target_graph:
        :param focus:
        :return:
        """
        if not isinstance(focus, (tuple, list, set)):
            focus = [focus]
        if not self.is_property_shape:
            return {f: set((f,)) for f in focus}
        path_val = self.path()
        focus_dict = {}
        for f in focus:
            focus_dict[f] = self.value_nodes_from_path(self.sg, f, path_val, target_graph)
        return focus_dict

    def find_custom_constraints(self):
        applicable_custom_constraints = set()
        for c in self.sg.custom_constraints:
            mandatory = c.mandatory_parameters
            found_all_mandatory = True
            for mandatory_param in mandatory:
                path = mandatory_param.path()
                assert isinstance(path, rdflib.URIRef)
                found_vals = set(self.sg.objects(self.node, path))
                #found_vals = self._value_nodes_from_path(self.node, mandatory_param.path(), self.sg.graph)
                found_all_mandatory = found_all_mandatory and bool(len(found_vals) > 0)
            if found_all_mandatory:
                applicable_custom_constraints.add(c)
        return applicable_custom_constraints


    def validate(self, target_graph, focus=None, bail_on_error=False):
        #assert isinstance(target_graph, rdflib.Graph)
        if self.deactivated:
            return True, []
        if focus is not None:
            if not isinstance(focus, (tuple, list, set)):
                focus = [focus]
        else:
            focus = self.focus_nodes(target_graph)
        if len(focus) < 1:
            # Its possible for shapes to have _no_ focus nodes
            # (they are called in other ways)
            return True, []
        parameters = self.parameters()
        reports = []
        focus_value_nodes = self.value_nodes(target_graph, focus)
        non_conformant = False
        done_constraints = set()
        run_count = 0
        constraint_components = [CONSTRAINT_PARAMETERS_MAP[p] for p in iter(parameters)]
        for constraint_component in constraint_components:
            if constraint_component in done_constraints:
                continue
            try:
                c = constraint_component(self)
            except ConstraintLoadWarning as w:
                self.logger.warning(repr(w))
                continue
            except ConstraintLoadError as e:
                self.logger.error(repr(e))
                raise e
            _is_conform, _r = c.evaluate(target_graph, focus_value_nodes)
            non_conformant = non_conformant or (not _is_conform)
            reports.extend(_r)
            run_count += 1
            done_constraints.add(constraint_component)
            if non_conformant and bail_on_error:
                break
        applicable_custom_constraints = self.find_custom_constraints()
        for a in applicable_custom_constraints:
            if non_conformant and bail_on_error:
                break
            validator = a.make_validator_for_shape(self)
            _is_conform, _r = validator.evaluate(target_graph, focus_value_nodes)
            non_conformant = non_conformant or (not _is_conform)
            reports.extend(_r)
            run_count += 1
        #if run_count < 1:
            #raise RuntimeError("A SHACL Shape should have at least one parameter or attached property shape.")
        return (not non_conformant), reports


