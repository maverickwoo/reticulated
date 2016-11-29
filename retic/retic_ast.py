import ast
from . import typing, exc, ast_trans
from .typing import retic_prefix, List
# At bottom of file: 'from . import builtin_fields'

## AST nodes used by Reticulated, including Reticulated's internal
## representation of types. 

retic_prefix('typing')

typing.nominal()


record = typing.Dict[str, 'Type']


## Internal representation of types

def generate_mro(n:'Class'):
    goto_dyn = False
    classmap = {'Dyn': type('Dyn', (), {})}
    rev_classmap = {classmap['Dyn']: Dyn()}
    def build_classmap(cls):
        nonlocal goto_dyn
        if isinstance(cls, Class):
            if cls in classmap:
                return classmap[cls]
            inhs = []
            for inh in cls.inherits:
                bcm = build_classmap(inh)
                if bcm is not None and bcm not in inhs:
                    inhs.append(bcm)
            cty = type(cls.name, tuple(inhs), {})
            classmap[cls] = cty
            rev_classmap[cty] = cls
            return cty
        else:
            assert isinstance(cls, Dyn)
            goto_dyn = True
            return None
    
    ty = build_classmap(n)
    mro = ty.mro()
    return [rev_classmap[c] for c in mro[:-1]] + ([Dyn()] if goto_dyn else [])

class Type: 
    def __getitem__(self, k:str)->'Type':
        raise KeyError(k)
    def bind(self)->'Type':
        return self
    def __hash__(self):
        return id(self)


@typing.constructor_fields
class OutputAlias(Type):
    def __init__(self, path:str, underlying:Type):
        self.path = path 
        self.underlying = underlying
    def to_ast(self, lineno:int, col_offset:int)->ast.expr:
        path = self.path.split('.')
        st = ast.Name(id=path[0], ctx=ast.Load(), lineno=lineno, col_offset=col_offset)
        for elt in path[1:]:
            st = ast.Attribute(value=st, attr=elt, ctx=ast.Load(), lineno=lineno, col_offset=col_offset)
        return st

@typing.constructor_fields
class ClassOutputAlias(Type):
    def __init__(self, path:str, underlying:Type):
        self.path = path 
        self.underlying = underlying
    def to_ast(self, lineno:int, col_offset:int)->ast.expr:
        from . import transient
        classname_marker = transient.__retic_type_marker__.__name__ # In case we change the name of the marker in transient.py
        path = self.path.split('.')
        st = ast.Name(id=path[0], ctx=ast.Load(), lineno=lineno, col_offset=col_offset)
        for elt in path[1:]:
            st = ast.Attribute(value=st, attr=elt, ctx=ast.Load(), lineno=lineno, col_offset=col_offset)

        return ast_trans.Call(func=ast.Name(id=classname_marker, ctx=ast.Load(), lineno=lineno, col_offset=col_offset),
                              args=[st], keywords=[],
                              starargs=None, kwargs=None, lineno=lineno, col_offset=col_offset)

@typing.constructor_fields
class Module(Type):
    def __init__(self, exports:record):
        self.exports = exports
    def __eq__(self, other):
        return isinstance(other, Module) and self.exports == other.exports
    def __getitem__(self, k:str)->Type:
        try:
            return self.exports[k]
        except KeyError:
            return builtin_fields.modfields(self)[k]
    def to_ast(self, lineno:int, col_offset:int)->ast.expr:
        return ast.Name(id='object', ctx=ast.Load(), lineno=lineno, col_offset=col_offset)
    def __str__(self)->str:
        return 'Module[{}]'.format(self.exports)
    __repr__ = __str__
    

@typing.fields({'name':str, 'inherits':typing.List[Type], 'members':record, 'fields':record, 'initialized':bool, 'instanceof':typing.Union['Class', None]})
class Class(Type):
    def __init__(self, name:str):
        self.name = name
        self.inherits = []
        self.members = {}
        self.fields = {}
        self.initialized = False
        self.instanceof = None
    def __eq__(self, other):
        return other is self

    def try_to_initialize(self):
        if all(isinstance(base, retic_ast.Dyn) or (isinstance(base, retic_ast.Class) and base.initialized) for base in self.parents):
            self.initialized = True

    def __getitem__(self, k:str):
        if hasattr(self, 'mro'):
            mro = self.mro
        else:
            mro = generate_mro(self)
            if self.initialized:
                self.mro = mro
        
        for cls in mro:
            try:
                return cls.get_class_member(k)
            except KeyError:
                pass
        for cls in mro:
            try:
                return cls.get_metaclass_member(k)
            except KeyError:
                pass
        try:
            return builtin_fields.basics(self)[k]
        except:
            if self.initialized:
                raise KeyError()
            else: 
                return Bot()
        
    def get_metaclass_member(self, k:str):
        if self.instanceof is None:
            raise KeyError()
        else:
            return self.instanceof[k].bind()

    def get_class_member(self, k:str):
        return self.members[k]
    def get_instance_field(self, k:str):
        return self.fields[k]

    def subtype_of(self, other:'Class'):
        return other is self or \
            any(sup.subtype_of(other) for sup in self.inherits)

    def to_ast(self, lineno:int, col_offset:int)->ast.expr:
        from . import transient
        classname_marker = transient.__retic_type_marker__.__name__ # In case we change the name of the marker in transient.py
        st = ast.Name(id=self.name, ctx=ast.Load(), lineno=lineno, col_offset=col_offset)

        return ast_trans.Call(func=ast.Name(id=classname_marker, ctx=ast.Load(), lineno=lineno, col_offset=col_offset),
                              args=[st], keywords=[],
                              starargs=None, kwargs=None, lineno=lineno, col_offset=col_offset)

    def __str__(self)->str:
        return 'Type[{}]'.format(self.name)
    __repr__ = __str__
    def __hash__(self):
        return id(self)


@typing.constructor_fields
class Structural(Type):
    def __init__(self, members:typing.Dict[str, Type]):
        self.members = members
    def __eq__(self, other):
        return isinstance(other, Structural) and self.members == other.members
    def __getitem__(self, k:str):
        return self.members[k]
    def to_ast(self, lineno:int, col_offset:int)->ast.expr:
        return ast.List(elts=[ast.Str(s=k, lineno=lineno, col_offset=col_offset) for k in self.members], lineno=lineno, col_offset=col_offset, ctx=ast.Load())
    def __str__(self)->str:
        return str(self.members)
    __repr__ = __str__


@typing.constructor_fields
class Instance(Type):
    def __init__(self, instanceof:Class):
        self.instanceof = instanceof
    def __eq__(self, other):
        return isinstance(other, Instance) and self.instanceof == other.instanceof
    def __getitem__(self, k:str):
        if hasattr(self.instanceof, 'mro'):
            mro = self.instanceof.mro
        else:
            mro = generate_mro(self.instanceof)
            if self.instanceof.initialized:
                self.instanceof.mro = mro
             
        for cls in mro:
            try:
                return cls.get_instance_field(k)
            except KeyError:
                pass
        for cls in mro:
            try:
                return cls.get_class_member(k).bind()
            except KeyError:
                pass
        try:
            return builtin_fields.basics(self)[k].bind()
        except:
            if self.instanceof.initialized:
                raise KeyError()
            else: 
                return Bot()
        
    def to_ast(self, lineno:int, col_offset:int)->ast.expr:
        st = ast.Name(id=self.instanceof.name, ctx=ast.Load(), lineno=lineno, col_offset=col_offset)
        return st
    def __str__(self)->str:
        return self.instanceof.name
    __repr__ = __str__
    def __hash__(self):
        return hash(self.instanceof)


class Bot(Type):
    def to_ast(self, lineno:int, col_offset:int)->ast.expr:
        raise exc.InternalReticulatedError(lineno, col_offset)
    def __eq__(self, other):
        return isinstance(other, Bot)
    def __getitem__(self, k:str)->Type:
        return Bot()
    def get_instance_field(self, k:str):
        return Bot()

class Dyn(Type): 
    def to_ast(self, lineno:int, col_offset:int)->ast.expr:
        return ast.Name(id='object', ctx=ast.Load(), lineno=lineno, col_offset=col_offset)
    def __str__(self)->str:
        return 'Any'
    __repr__ = __str__
    def __eq__(self, other):
        return isinstance(other, Dyn)
    def __getitem__(self, k:str)->Type:
        return Dyn()
    def get_instance_field(self, k:str): 
        raise KeyError()
    def get_class_member(self, k:str): 
        return Dyn()


class Union(Type):
    def __init__(self, alternatives:List(Type)):
        assert len(alternatives) >= 2
        self.alternatives = alternatives

    def __str__(self):
        return 'Union{}'.format(self.alternatives)
    def __eq__(self, other):
        return isinstance(other, Union) and self.alternatives == other.alternatives
    __repr__ = __str__
    def to_ast(self, lineno:int, col_offset:int)->ast.expr:
        return ast_trans.Call(func=ast.Name(id='__retic_union__', ctx=ast.Load(), lineno=lineno, col_offset=col_offset),
                              args=[
                                  ast.List(elts=[alt.to_ast(lineno, col_offset) for alt in self.alternatives], ctx=ast.Load(), lineno=lineno, col_offset=col_offset)
                              ], keywords=[],
                              starargs=None, kwargs=None, lineno=lineno, col_offset=col_offset)
        
    def __getitem__(self, k:str)->Type:
        types = []
        for alt in alternatives:
            if alt[k] not in types:
                types.append(alt[k])
        if len(types) <= 1:
            return types[0]
        else:
            return Union(types)

@typing.fields({'type': str})
class Primitive(Type): 
    def to_ast(self, lineno:int, col_offset:int)->ast.expr:
        return ast.Name(id=self.type, ctx=ast.Load(), lineno=lineno, col_offset=col_offset)
    def __str__(self)->str:
        return self.type
    __repr__ = __str__
    def __eq__(self, other):
        return isinstance(other, self.__class__)

class Int(Primitive):
    def __init__(self):
        self.type = 'int'
    def __getitem__(self, k):
        return builtin_fields.intfields[k]

@typing.fields({'n': int})
class SingletonInt(Primitive):
    def __init__(self, n:int):
        self.n = n
        self.type = 'int'
    def __getitem__(self, k):
        return builtin_fields.intfields[k]

class Float(Primitive):
    def __init__(self):
        self.type = 'float'

class Bool(Primitive):
    def __init__(self):
        self.type = 'bool'

class Str(Primitive):
    def __init__(self):
        self.type = 'str'
    def __getitem__(self, k):
        return builtin_fields.strfields[k]

class Void(Primitive):
    def __init__(self):
        self.type = 'None'
    def __getitem__(self, k):
        return builtin_fields.voidfields[k]


@typing.constructor_fields
class Function(Type):
    def __init__(self, froms:'ArgTypes', to:Type):
        self.froms = froms
        self.to = to

    def to_ast(self, lineno:int, col_offset:int)->ast.expr:
        return ast.Name(id='callable', ctx=ast.Load(), lineno=lineno, col_offset=col_offset)

    def __str__(self)->str:
        return 'Callable[{},{}]'.format(self.froms, self.to)
    __repr__ = __str__

    def __eq__(self, other):
        return isinstance(other, Function) and \
            self.froms == other.froms and self.to == other.to
    def bind(self)->Type:
        return Function(self.froms.bind(), self.to)

    def __getitem__(self, k):
        return builtin_fields.funcfields(self)[k]

class TopType(Type):
    pass

# class TopTuple(TopType):
#     def __eq__(self, other):
#         return isinstance(other, TopTuple)

class TopList(TopType):
    def __eq__(self, other):
        return isinstance(other, TopList)

@typing.constructor_fields
class List(Type):
    def __init__(self, elts: Type):
        self.elts = elts

    def __getitem__(self, k):
        return {
            'append': Function(PosAT([self.elts]), Void()),
            'clear': Function(PosAT([]), Void()),
            'copy': Function(PosAT([]), List(self.elts)),
            'count': Function(PosAT([self.elts]), Int()),
            'extend': Function(PosAT([self]), List(self.elts)),
            'index': Function(PosAT([self.elts]), Int()),
            'insert': Function(PosAT([Int(), self.elts]), Int()),
            'pop': Function(ArbAT(), self.elts),
            'remove': Function(PosAT([self.elts]), Void()),
            'reverse': Function(PosAT([]), Void()),
            'sort': Function(ArbAT(), Void())
        }[k]

    def to_ast(self, lineno:int, col_offset:int)->ast.expr:
        return ast.Name(id='list', ctx=ast.Load(), lineno=lineno, col_offset=col_offset)

    def __str__(self)->str:
        return 'List[{}]'.format(self.elts)
    __repr__ = __str__

    def __eq__(self, other):
        return isinstance(other, List) and \
            self.elts == other.elts

@typing.constructor_fields
class Set(Type):
    def __init__(self, elts: Type):
        self.elts = elts

    def __getitem__(self, k):
        return builtin_fields.setfields(self)[k]

    def to_ast(self, lineno:int, col_offset:int)->ast.expr:
        return ast.Name(id='set', ctx=ast.Load(), lineno=lineno, col_offset=col_offset)

    def __str__(self)->str:
        return 'Set[{}]'.format(self.elts)
    __repr__ = __str__

    def __eq__(self, other):
        return isinstance(other, Set) and \
            self.elts == other.elts

@typing.constructor_fields
class Dict(Type):
    def __init__(self, keys: Type, values: Type):
        self.keys = keys
        self.values = values

    def __getitem__(self, k):
        return {}[k]

    def to_ast(self, lineno:int, col_offset:int)->ast.expr:
        return ast.Name(id='dict', ctx=ast.Load(), lineno=lineno, col_offset=col_offset)

    def __str__(self)->str:
        return 'Dict[{}, {}]'.format(self.keys, self.values)
    __repr__ = __str__

    def __eq__(self, other):
        return isinstance(other, Dict) and \
            self.keys == other.keys and self.values == other.values
    def __getitem__(self, k):
        return builtin_fields.dictfields(self)[k]

@typing.constructor_fields
class Tuple(Type):
    def __init__(self, *elts: typing.List[Type]):
        self.elts = elts

    def to_ast(self, lineno:int, col_offset:int)->ast.expr:
        return ast.Name(id='tuple', ctx=ast.Load(), lineno=lineno, col_offset=col_offset)

    def __str__(self)->str:
        return 'Tuple{}'.format(list(self.elts))
    __repr__ = __str__

    def __eq__(self, other):
        return isinstance(other, Tuple) and \
            self.elts == other.elts

@typing.constructor_fields
class HTuple(Type):
    def __init__(self, elts: Type):
        self.elts = elts

    def to_ast(self, lineno:int, col_offset:int)->ast.expr:
        return ast.Name(id='tuple', ctx=ast.Load(), lineno=lineno, col_offset=col_offset)

    def __str__(self)->str:
        return 'Tuple[{}, ...]'.format(self.elts)
    __repr__ = __str__

    def __eq__(self, other):
        return isinstance(other, HTuple) and \
            self.elts == other.elts

# ArgTypes is the LHS of the function type arrow. We should _not_ use
# this on the inside of functions to determine what the type env or
# required transient checks are.
class ArgTypes: 
    def match(self, nargs: int)->typing.List[Type]:
        raise Exception('abstract')

    def can_match(self, nargs: int)->bool:
        raise Exception('abstract')
        
    def bind(self):
        raise Exception('abstract')


# Essentially Dyn for argtypes: accepts anything
class ArbAT(ArgTypes):
    def __str__(self)->str:
        return '...'
    __repr__ = __str__
    def __eq__(self, other):
        return isinstance(other, ArbAT)
    def bind(self):
        return self

# Strict positional type: can't be called with anything but 
# the arguments specified
@typing.constructor_fields
class PosAT(ArgTypes):
    def __init__(self, types: typing.List[Type]):
        self.types = types

    def __str__(self)->str:
        return str(self.types)
    __repr__ = __str__
    def __eq__(self, other):
        return isinstance(other, PosAT) and \
            self.types == other.types
    def bind(self):
        assert len(self.types) >= 1
        return PosAT(self.types[1:])


# Strict named positional type
@typing.constructor_fields
class NamedAT(ArgTypes):
    def __init__(self, bindings: typing.List[typing.Tuple[str, Type]]):
        self.bindings = bindings

    def __str__(self)->str:
        return str(['{}: {}'.format(k, v) for k, v in self.bindings])
    __repr__ = __str__
    def __eq__(self, other):
        return isinstance(other, NamedAT) and \
            self.bindings == other.bindings
    def bind(self):
        assert len(self.bindings) >= 1
        return NamedAT(self.bindings[1:])

# Permissive named positional type: will reject positional arguments known
# to be wrong, but if called with varargs, kwargs, etc, will give up
@typing.constructor_fields
class ApproxNamedAT(ArgTypes):
    def __init__(self, bindings: typing.List[typing.Tuple[str, Type]]):
        self.bindings = bindings

    def __str__(self)->str:
        return str(['{}: {}'.format(k, v) for k, v in self.bindings] + ['...'])
    __repr__ = __str__
    def __eq__(self, other):
        return isinstance(other, ApproxNamedAT) and \
            self.bindings == other.bindings
    def bind(self):
        if len(self.bindings) >= 1:
            return ApproxNamedAT(self.bindings[1:])
        else:
            return ApproxNamedAT([])

@typing.constructor_fields
class Check(ast.expr):
    def __init__(self, value: ast.expr, type: Type, lineno:int, col_offset:int):
        self.value = value
        self.type = type
        self.lineno = lineno
        self.col_offset = col_offset

    def to_ast(self)->ast.expr:
        return ast.Call(func=ast.Name(id='_retic_check', ctx=ast.Load()), args=[self.value, self.type.to_ast()], 
                        keywords=[], starargs=None, kwargs=None)
        

@typing.constructor_fields
class ExpandSeq(ast.expr):
    def __init__(self, body:typing.List[ast.stmt], lineno:int, col_offset:int):
        self.body = body
        self.lineno = lineno
        self.col_offset = col_offset

from . import builtin_fields
