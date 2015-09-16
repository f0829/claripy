import logging
import functools
import operator

l = logging.getLogger("claripy.backends.backend_vsa")

from ..backend import Backend, BackendError

def arg_filter(f):
    @functools.wraps(f)
    def filter(*args): #pylint:disable=redefined-builtin
        if type(args[0]) in {int, long}: #pylint:disable=unidiomatic-typecheck
            raise BackendError('Unsupported argument type %s' % type(args[0]))
        return f(*args)

    return filter

def normalize_arg_order(f):
    @functools.wraps(f)
    def normalizer(*args):
        if len(args) != 2:
            raise BackendError('Unsupported arguments number %d' % len(args))

        if type(args[0]) not in { StridedInterval, DiscreteStridedIntervalSet, ValueSet }: #pylint:disable=unidiomatic-typecheck
            if type(args[1]) not in { StridedInterval, DiscreteStridedIntervalSet, ValueSet }: #pylint:disable=unidiomatic-typecheck
                raise BackendError('Unsupported arguments')
            args = [args[1], args[0]]

        return f(*args)

    return normalizer

def normalize_boolean_arg_types(f):
    def convert_bool(a):
        if isinstance(a, BoolResult):
            return a
        if a == True:
            return TrueResult()
        elif a == False:
            return FalseResult()
        else:
            raise BackendError('Unsupported type %s' % type(a))

    @functools.wraps(f)
    def normalizer(*args):
        new_args = [convert_bool(a) for a in args]

        return f(*new_args)

    return normalizer

def convert_bvv_args(f):
    def convert_bvv(a):
        if isinstance(a, BVV):
            return BackendVSA.CreateStridedInterval(to_conv=a)
        return a

    @functools.wraps(f)
    def converter(*args):
        new_args = [convert_bvv(a) for a in args]

        return f(*new_args)

    return converter

def normalize_reversed_arguments(f):
    @functools.wraps(f)
    def normalizer(self, ast, result=None):
        arg_reversed = []
        raw_args = []
        for i in xrange(len(ast.args)):
            if isinstance(ast.args[i], Base) and \
                            type(ast.args[i].model) in { #pylint:disable=unidiomatic-typecheck
                                                    StridedInterval,
                                                    DiscreteStridedIntervalSet,
                                                    ValueSet
            }:
                if ast.args[i].model.reversed:
                    arg_reversed.append(True)
                    raw_args.append(ast.args[i].reversed)
                    continue

            # It's not reversed
            arg_reversed.append(False)
            raw_args.append(ast.args[i])

        any_reversed_arg = any(arg_reversed)
        for i in xrange(len(raw_args)):
            raw_args[i] = self.convert(raw_args[i])

        normalized = ast.swap_args(raw_args)
        ret = f(self, normalized, result=result)

        # inner_i = I(args[0]._claripy, ret, variables=variables)
        if any_reversed_arg:
            return ret.reverse()
            #ret = A(args[0]._claripy, 'Reverse', (inner_i,), variables=variables, collapsible=False)

        return ret

    return normalizer

class BackendVSA(Backend):
    def __init__(self):
        Backend.__init__(self)
        # self._make_raw_ops(set(expression_operations) - set(expression_set_operations), op_module=BackendVSA)
        self._make_expr_ops(set(expression_set_operations), op_class=self)
        self._make_raw_ops(set(backend_operations_vsa_compliant), op_module=BackendVSA)

        self._op_raw['StridedInterval'] = BackendVSA.CreateStridedInterval
        self._op_raw['ValueSet'] = ValueSet.__init__
        self._op_raw['AbstractLocation'] = AbstractLocation.__init__
        self._op_raw['Reverse'] = BackendVSA.Reverse
        self._op_raw['If'] = self.If
        self._op_expr['BVS'] = self.BVS

    def convert(self, expr, result=None):
        return Backend.convert(self, expr.ite_excavated if isinstance(expr, Base) else expr, result=result)

    def _convert(self, a, result=None):
        if type(a) in { int, long, float, str }: #pylint:disable=unidiomatic-typecheck
            return a
        if type(a) is bool:
            return TrueResult() if a else FalseResult()
        if type(a) is BVV: #pylint:disable=unidiomatic-typecheck
            return BackendVSA.CreateStridedInterval(bits=a.bits, to_conv=a)
        if type(a) in { StridedInterval, DiscreteStridedIntervalSet, ValueSet }: #pylint:disable=unidiomatic-typecheck
            return a
        if isinstance(a, BoolResult):
            return a

        raise BackendError("why is fish raising NotImplementedError INSTEAD OF THE ERROR THAT'S SUPPOSED TO BE RAISED IN THIS SITUATION? SERIOUSLY, JUST RAISE A BACKENDERROR AND EVERYONE WILL BE HAPPY, BUT NO, PEOPLE HAVE TO RAISE THEIR OWN ERRORS INSTEAD OF USING THE ERRORS THAT WERE ****DESIGNED**** FOR THIS SORT OF THING. WHY DO I BOTHER DESIGNING A GOOD ERROR HIERARCHY, ANYWAYS? WILL IT BE USED? NO! IT'LL BE ALL NotImplementedError('THIS') or Exception('THAT') AND EVERYTHING WILL MELT DOWN. UGH!")

    def _eval(self, expr, n, result=None, solver=None, extra_constraints=()):
        if isinstance(expr, StridedInterval):
            return expr.eval(n)
        elif isinstance(expr, ValueSet):
            results = []

            while len(results) < n:
                results.extend(expr.eval(n - len(results)))

            return results
        elif isinstance(expr, BoolResult):
            return expr.value
        else:
            raise BackendError('Unsupported type %s' % type(expr))

    def _min(self, expr, result=None, solver=None, extra_constraints=()):
        if isinstance(expr, StridedInterval):
            if expr.is_top:
                # TODO: Return
                return StridedInterval.min_int(expr.bits)

            return expr.lower_bound
        else:
            raise BackendError('Unsupported expr type %s' % type(expr))

    def _max(self, expr, result=None, solver=None, extra_constraints=()):
        if isinstance(expr, StridedInterval):
            if expr.is_top:
                # TODO:
                return StridedInterval.max_int(expr.bits)

            return expr.upper_bound

        else:
            raise BackendError('Unsupported expr type %s' % type(expr))

    def _solution(self, obj, v, result=None, solver=None, extra_constraints=()):
        if isinstance(obj, BoolResult):
            return v in obj.value

        if isinstance(obj, StridedInterval):
            return not obj.intersection(v).is_empty

        if isinstance(obj, ValueSet):
            for _, si in obj.items():
                if not si.intersection(v).is_empty:
                    return True
            return False

        raise NotImplementedError(type(obj).__name__)

    def _has_true(self, o):
        return BoolResult.has_true(o)

    def _has_false(self, o):
        return BoolResult.has_false(o)

    def _is_true(self, o):
        return BoolResult.is_true(o)

    def _is_false(self, o):
        return BoolResult.is_false(o)

    #
    # Backend Operations
    #

    def _identical(self, a, b, result=None):
        if type(a) != type(b):
            return False
        return a.identical(b)

    def _unique(self, obj, result=None): #pylint:disable=unused-argument,no-self-use
        if isinstance(obj, StridedInterval):
            return obj.unique
        elif isinstance(obj, ValueSet):
            return obj.unique
        else:
            raise BackendError('Not supported type of operand %s' % type(obj))

    def name(self, a, result=None):
        if isinstance(a, StridedInterval):
            return a.name

        else:
            return None

    @staticmethod
    @normalize_boolean_arg_types
    def And(a, *args):
        return reduce(operator.__and__, args, a)

    @staticmethod
    @normalize_boolean_arg_types
    def Not(a):
        return ~a

    @staticmethod
    @normalize_arg_order
    def ULT(a, b):
        return a.ULT(b)

    @staticmethod
    @normalize_arg_order
    def ULE(a, b):
        return a.ULE(b)

    @staticmethod
    @normalize_arg_order
    def UGT(a, b):
        return a.UGT(b)

    @staticmethod
    @normalize_arg_order
    def UGE(a, b):
        return a.UGE(b)

    @staticmethod
    @normalize_arg_order
    def SLT(a, b):
        return a.SLT(b)

    @staticmethod
    @normalize_arg_order
    def SLE(a, b):
        return a.SLE(b)

    @staticmethod
    @normalize_arg_order
    def SGT(a, b):
        return a.SGT(b)

    @staticmethod
    @normalize_arg_order
    def SGE(a, b):
        return a.SGE(b)

    @staticmethod
    def BVS(ast, result=None): #pylint:disable=unused-argument
        size = ast.size()
        name, mn, mx, stride = ast.args
        return CreateStridedInterval(name=name, bits=size, lower_bound=mn, upper_bound=mx, stride=stride)

    def If(self, cond, t, f):
        if not self.has_true(cond):
            return f
        elif not self.has_false(cond):
            return t
        else:
            return t.union(f)

    # TODO: Implement other operations!

    @staticmethod
    def LShR(expr, shift_amount):
        return expr >> shift_amount

    @staticmethod
    def Concat(*args):
        ret = None
        for expr in args:
            if type(expr) not in { StridedInterval, DiscreteStridedIntervalSet, ValueSet, BVV }: #pylint:disable=unidiomatic-typecheck
                raise BackendError('Unsupported expr type %s' % type(expr))

            if type(expr) is BVV: #pylint:disable=unidiomatic-typecheck
                expr = BackendVSA.CreateStridedInterval(bits=expr.bits, to_conv=expr)

            ret = ret.concat(expr) if ret is not None else expr

        return ret

    @arg_filter
    def _size(self, arg, result=None):
        if type(arg) in { StridedInterval, DiscreteStridedIntervalSet, ValueSet }: #pylint:disable=unidiomatic-typecheck
            return len(arg)
        else:
            return arg.size()

    @staticmethod
    def Extract(*args):
        low_bit = args[1]
        high_bit = args[0]
        expr = args[2]

        if type(expr) not in { StridedInterval, DiscreteStridedIntervalSet, ValueSet }: #pylint:disable=unidiomatic-typecheck
            raise BackendError('Unsupported expr type %s' % type(expr))

        ret = expr.extract(high_bit, low_bit)

        return ret

    @staticmethod
    @convert_bvv_args
    def SignExt(*args):
        new_bits = args[0]
        expr = args[1]

        if type(expr) not in { StridedInterval, DiscreteStridedIntervalSet }: #pylint:disable=unidiomatic-typecheck
            raise BackendError('Unsupported expr type %s' % type(expr))

        return expr.sign_extend(new_bits + expr.bits)

    @staticmethod
    @convert_bvv_args
    def ZeroExt(*args):
        new_bits = args[0]
        expr = args[1]

        if type(expr) not in { StridedInterval, DiscreteStridedIntervalSet }: #pylint:disable=unidiomatic-typecheck
            raise BackendError('Unsupported expr type %s' % type(expr))

        return expr.zero_extend(new_bits + expr.bits)

    @staticmethod
    def Reverse(arg):
        if type(arg) not in {StridedInterval, DiscreteStridedIntervalSet, ValueSet}: #pylint:disable=unidiomatic-typecheck
            raise BackendError('Unsupported expr type %s' % type(arg))

        return arg.reverse()

    @normalize_reversed_arguments
    def union(self, ast, result=None): #pylint:disable=unused-argument,no-self-use
        if len(ast.args) != 2:
            raise BackendError('Incorrect number of arguments (%d) passed to BackendVSA.union().' % len(ast.args))

        ret = ast.args[0].union(ast.args[1])

        if ret is NotImplemented:
            ret = ast.args[1].union(ast.args[0])

        return ret

    @normalize_reversed_arguments
    def intersection(self, ast, result=None): #pylint:disable=unused-argument,no-self-use
        if len(ast.args) != 2:
            raise BackendError('Incorrect number of arguments (%d) passed to BackendVSA.intersection().' % len(ast.args))

        ret = None

        for arg in ast.args:
            if ret is None:
                ret = arg
            else:
                ret = ret.intersection(arg)

        return ret

    @normalize_reversed_arguments
    def widen(self, ast, result=None): #pylint:disable=unused-argument,no-self-use
        if len(ast.args) != 2:
            raise BackendError('Incorrect number of arguments (%d) passed to BackendVSA.widen().' % len(ast.args))

        ret = ast.args[0].widen(ast.args[1])
        if ret is NotImplemented:
            ret = ast.args[1].widen(ast.args[0])

        return ret

    @staticmethod
    def CreateTopStridedInterval(bits, name=None, uninitialized=False): #pylint:disable=unused-argument,no-self-use
        return StridedInterval.top(bits, name, uninitialized=uninitialized)

from ..bv import BVV
from ..ast.base import Base
from ..operations import backend_operations_vsa_compliant, expression_set_operations
from ..vsa import StridedInterval, CreateStridedInterval, DiscreteStridedIntervalSet, ValueSet, AbstractLocation, BoolResult, TrueResult, FalseResult

BackendVSA.CreateStridedInterval = staticmethod(CreateStridedInterval)
