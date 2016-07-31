from . import scope, typeparser, exc, vis, flags, retic_ast, consistency, typing, utils
import ast

tydict = typing.Alias(typing.Dict[str, retic_ast.Type])

# Given a writable (LHS) AST node and a type, figure out which types
# correspond to indvidual non-destructurable targets in the AST node.
def decomp_assign(lhs: ast.expr, rhs: retic_ast.Type, level_up=None):
    if isinstance(lhs, ast.Name) or isinstance(lhs, ast.Subscript) or isinstance(lhs, ast.Attribute):
        return {lhs: rhs}
    elif isinstance(lhs, ast.Tuple) or isinstance(lhs, ast.List):
        if isinstance(rhs, retic_ast.Dyn):
            return {k: v for d in [decomp_assign(lhe, retic_ast.Dyn(), level_up=rhs) for lhe in lhs.elts] for k, v in d.items()}
        elif isinstance(rhs, retic_ast.Bot):
            return {k: v for d in [decomp_assign(lhe, retic_ast.Bot(), level_up=rhs) for lhe in lhs.elts] for k, v in d.items()}
        elif isinstance(rhs, retic_ast.List):
            return {k: v for d in [decomp_assign(lhe, rhs.elts, level_up=rhs) for lhe in lhs.elts] for k, v in d.items()}
        else: raise exc.StaticTypeError(lhs, 'Value of type {} can not be destructured for assignment'.format(rhs))
    elif isinstance(lhs, ast.Starred):
        return decomp_assign(lhs.value, retic_ast.List(elts=rhs), level_up=level_up)
    else: 
        raise exc.InternalReticulatedError(lhs)
        

# Performs local type inference on a scope. ext_scope is the overall
# scope this takes place in (some of which may be shadowed by locals),
# while ext_fixed are the annotated variables in the same scope which
# cannot be shadowed.
def infer_types(ext_scope: tydict, ext_fixed: tydict, body: typing.List[ast.stmt])->tydict:
    # Find assignment targets
    infer_targets = scope.InferenceTargetFinder().preorder(body)
    # Create a scope for the locals that aren't already defined by a
    # type annotation. Initialize everything to the bottom type
    bot_scope = {k: retic_ast.Bot() for k in infer_targets if k not in ext_fixed}

    while True:
        # Add the inference scope to the overall scope. We don't
        # shadow fixed things since we kept them out of bot_scope
        # above
        infer_scope = ext_scope.copy()
        infer_scope.update(bot_scope)

        # Find all bindings in the current body
        assignments = scope.AssignmentFinder().preorder(body)

        for targ, val, kind in assignments:
            # For each binding, typecheck the RHS in the scope
            Typechecker().preorder(val, infer_scope)

            # Then decompose the assignment to the level of individual
            # variables. Join the current type for the variable to the
            # type of the RHS and write that back into bot_scope.
            if kind == 'ASSIGN':
                assigns = decomp_assign(targ, val.retic_type)
                for targ in assigns:
                    if isinstance(targ, ast.Name) and targ.id in bot_scope:
                        bot_scope[targ.id] = consistency.join(assigns[targ], bot_scope[targ.id])
            elif kind == 'FOR':
                assigns = decomp_assign(targ, consistency.iterable_type(val.retic_type))
                for targ in assigns:
                    if isinstance(targ, ast.Name) and targ.id in bot_scope:
                        bot_scope[targ.id] = consistency.join(assigns[targ], bot_scope[targ.id])
            else:
                raise exc.InternalReticulatedError(kind)
        
        # If bot_scope is free of Bots, then we're done. Otherwise do another iteration.
        if all(not isinstance(bot_scope[k], retic_ast.Bot) for k in bot_scope):
            break

    ret = ext_scope.copy()
    ret.update(bot_scope)
    return ret
    

# Determines the internal scope of a function and updates the arguments with .retic_type
def getFunctionScope(n: ast.FunctionDef, surrounding: tydict)->tydict:
    try:
        local = scope.InitialScopeFinder().preorder(n.body)
    except scope.InconsistentAssignment as e:
        raise exc.StaticTypeError(n, 'Multiple bindings of {} occur in the scope of {} with differing types: {} and {}'.format(e.args[0], n.name, e.args[1], e.args[2]))
    args = getLocalArgTypes(n.args)
    funscope = surrounding.copy()
    
    for k in local:
        if k in args and local[k] != args[k]:
            raise exc.StaticTypeError(n, 'Variable {} is bound both as an argument and by a definition in {} with differing types: {} and {}'.format(k, n.name, args[k], local[k]))
    local.update(args)
    
    funscope.update(local)
    
    return infer_types(funscope, local, n.body)

# Determines the internal scope of a lambda
def getLambdaScope(n: ast.Lambda, surrounding: tydict)->tydict:
    args = getLocalArgTypes(n.args)
    scope = surrounding.copy()
    scope.update(args)
    return scope

# Determines the internal scope of a top-level module
def getModuleScope(n: ast.Module)->tydict:
    try:
        local = scope.InitialScopeFinder().preorder(n.body)
    except scope.InconsistentAssignment as e:
        raise exc.StaticTypeError(None, 'Multiple bindings of {} occur at the top level with differing types: {} and {}'.format(e.args[0], e.args[1], e.args[2]))
    return infer_types(local, local, n.body)

# Determines the internal scope of a comprehension
def getComprehensionScope(n: typing.List[ast.comprehension], env: tydict, 
                          typechecker: 'Typechecker', *args)->tydict:
    # We pass in the typechecker because later comprehensions are in
    # the scope of the earlier comprehensions
    env = env.copy()
    for comp in n:
        typechecker.dispatch(comp, env, *args)
        if isinstance(comp.target, ast.Name):
            if isinstance(comp.iter.retic_type, retic_ast.Dyn):
                ty = retic_ast.Dyn()
            elif isinstance(comp.iter.retic_type, retic_ast.List):
                ty = comp.iter.retic_type.elts
            else:
                raise exc.StaticTypeError(comp.iter,\
                    'Iteration expression has type {}, which is not iterable'.format(comp.iter.retic_type))
            env[comp.target.id] = ty
        else: raise exc.UnimplementedException()
    return env

def getLocalArgTypes(n: ast.arguments)->tydict:
    args = {}
    for arg in n.args:
        ty = typeparser.typeparse(arg.annotation)
        args[arg.arg] = arg.retic_type = ty
    for arg in n.kwonlyargs:
        ty = typeparser.typeparse(arg.annotation)
        args[arg.arg] = arg.retic_type = ty
    if n.vararg:
        ty = typeparser.typeparse(n.vararg.annotation)
        args[n.vararg.arg]  = n.vararg.retic_type = ty
    if n.kwarg:
        ty = typeparser.typeparse(n.kwarg.annotation)
        args[n.kwarg.arg]  = n.kwarg.retic_type = ty
    return args
    

class Typechecker(vis.Visitor):
    # Detects static type errors and _UPDATES IN PLACE_ the ast. Each
    # expression node should have a .retic_type node added containing
    # its static type. Also, every FunctionDef should have a
    # .retic_return_type node added, and every ast.arg should have a
    # .retic_type node.

    def visitlist(self, n, *args):
        for s in n:
            self.dispatch(s, *args)
        
    def visitNoneType(self, n, *args): pass

    def visitModule(self, n):
        env = getModuleScope(n)
        self.dispatch(n.body, env)

    def visitFunctionDef(self, n, env, *args):
        self.dispatch(n.args, env, *args)
        [self.dispatch(dec, env, *args) for dec in n.decorator_list]

        # getFunctionScope will update the ast.arg's of the function with .retic_types.
        fun_env = getFunctionScope(n, env)
        # Attaching return type
        n.retic_return_type = typeparser.typeparse(n.returns)

        self.dispatch(n.body, fun_env, *args)
        

    def visitarguments(self, n, *args):
        # We still need to check the types of default arguments against their annotations
        [self.dispatch(default, *args) for default in n.defaults]
        [self.dispatch(arg, *args) for arg in n.args]
        if flags.PY_VERSION == 3:
            [self.dispatch(default, *args) for default in n.kw_defaults]

    def visitarg(self, n, *args):
        self.dispatch(n.annotation, *args)

    def visitReturn(self, n, *args):
        # Handle return type checking in a separate pass
        self.dispatch(n.value, *args)

    # Assignment stuff
    def visitAssign(self, n, *args):
        self.dispatch(n.value, *args)
        for target in n.targets:
            self.dispatch(target, *args)
            assigns = decomp_assign(target, n.value.retic_type)
            for subtarg in assigns:
                if not consistency.assignable(subtarg.retic_type, assigns[subtarg]):
                    raise exc.StaticTypeError(subtarg, 'Value of type {} cannot be assigned to target {}, which has type {}'.format(assigns[subtarg], 
                                                                                                                                    typeparser.unparse(subtarg), 
                                                                                                                                    subtarg.retic_type))

    def visitAugAssign(self, n, *args):
        self.dispatch(n.value, *args)
        self.dispatch(n.target, *args)
        ty = consistency.apply_binop(n.op, n.target.retic_type, n.value.retic_type)
        if not consistency.assignable(n.target.retic_type, ty):
            raise exc.StaticTypeError(n.value, 'Value of type {} cannot be {} into a target which has type {}'.format(n.value.retic_type, 
                                                                                                                      utils.stringify(n.op, 'PASTTENSE'), 
                                                                                                                      target.id, target.retic_type))

    def visitDelete(self, n, *args):
        [self.dispatch(target,*args) for target in n.targets]
        for target in n.targets:
            self.dispatch(target, *args)
            if not isinstance(target.retic_type, retic_ast.Dyn):
                raise exc.StaticTypeError(target, 'Statically typed values cannot be deleted')

    # Control flow stuff
    def visitIf(self, n, *args):
        self.dispatch(n.test, *args)
        if not consistency.assignable(retic_ast.Bool(), n.test.retic_type):
            raise exc.StaticTypeError(n.test, 'Test expression has type {} but was expected to have type bool'.format(n.test.retic_type))
        self.dispatch(n.body, *args)
        self.dispatch(n.orelse, *args)

    def visitFor(self, n, *args):
        self.dispatch(n.target, *args)
        self.dispatch(n.iter, *args)
        if not consistency.member_assignable(n.target.retic_type, n.iter.retic_type):
            raise exc.StaticTypeError(n.target, 'Iteration expression has type {}, but the iteration variable(s) have the expected type {}'.format(n.iter.retic_type, n.target.retic_type))
        self.dispatch(n.body, *args)
        self.dispatch(n.orelse, *args)

    def visitWhile(self, n, *args):
        self.dispatch(n.test, *args)
        if not consistency.assignable(retic_ast.Bool(), n.test.retic_type):
            raise exc.StaticTypeError(n.test, 'Test expression has type {} but was expected to have type bool'.format(n.test.retic_type))
        self.dispatch(n.body, *args)
        self.dispatch(n.orelse, *args)

    def visitWith(self, n, *args): 
        self.dispatch(n.body, *args)
        if flags.PY_VERSION == 3 and flags.PY3_VERSION >= 3:
            [self.dispatch(item, *args) for item in n.items]
        else:
            self.dispatch(n.context_expr, *args)
            self.dispatch(n.optional_vars, *args)
            if n.optional_vars and not consistency.assignable(n.context_expr.retic_type, n.optional_vars.retic_type):
                raise exc.StaticTypeError(n.optional_vars, 'With expression has type {}, but the bound variable(s) have the expected type {}'.format(n.context_expr.retic_type, n.optional_vars.retic_type))

    def visitwithitem(self, n, *args):
        self.dispatch(n.context_expr, *args)
        self.dispatch(n.optional_vars, *args)
        if n.optional_vars and not consistency.assignable(n.context_expr.retic_type, n.optional_vars.retic_type):
            raise exc.StaticTypeError(n.optional_vars, 'With expression has type {}, but the bound variable(s) have the expected type {}'.format(n.context_expr.retic_type, n.optional_vars.retic_type))
            

    # Class stuff
    def visitClassDef(self, n, *args):
        raise exc.UnimplementedException()
            

    # Exception stuff
    # Python 2.7, 3.2
    def visitTryExcept(self, n, *args):
        self.dispatch(n.body, *args)
        self.dispatch(n.handlers, *args)
        self.dispatch(n.orelse, *args)

    # Python 2.7, 3.2
    def visitTryFinally(self, n, *args):
        self.dispatch(n.body, *args)
        self.dispatch(n.finalbody, *args)
    
    # Python 3.3
    def visitTry(self, n, *args):
        self.dispatch(n.body, *args)
        self.dispatch(n.handlers, *args)
        self.dispatch(n.orelse, *args)
        self.dispatch(n.finalbody, *args)

    def visitExceptHandler(self, n, env, *args):
        self.dispatch(n.type, env, *args)
        self.dispatch(n.body, env, *args)
        
        if n.name and n.name in env:
            ty = env[n.name]
        else:
            ty = retic_ast.Dyn()

        if not consistency.instance_assignable(ty, n.type.retic_type):
            raise exc.StaticTypeError(target, 'Instances of {} cannot be assigned to variable {}, which has type {}'.format(typeparser.unparse(n.type), n.name, target.retic_type))
        else:
            # ExceptHandlers aren't expressions, but since the target
            # of the binding is just a string, not an Expression, we
            # write its type into the node
            n.retic_type = ty

    def visitRaise(self, n, *args):
        if flags.PY_VERSION == 3:
            self.dispatch(n.exc, *args)
            self.dispatch(n.cause, *args)
        elif flags.PY_VERSION == 2:
            self.dispatch(n.type, *args)
            self.dispatch(n.inst, *args)
            self.dispatch(n.tback, *args)

    def visitAssert(self, n, *args):
        self.dispatch(n.test, *args)
        if not consistency.assignable(retic_ast.Bool(), n.test.retic_type):
            raise exc.StaticTypeError(n.test, 'Asserted expression has type {} but was expected to have type bool'.format(n.test.retic_type))
        self.dispatch(n.msg, *args)

    # Miscellaneous
    def visitExpr(self, n, *args):
        self.dispatch(n.value, *args)

    def visitPrint(self, n, *args):
        self.dispatch(n.dest, *args)
        self.dispatch(n.values, *args)

    def visitExec(self, n, *args):
        self.dispatch(n.body, *args)
        self.dispatch(n.globals, *args) 
        self.dispatch(n.locals, *args)

    def visitImport(self, n, *args): pass
    def visitImportFrom(self, n, *args): pass
    def visitPass(self, n, *args): pass
    def visitBreak(self, n, *args): pass
    def visitContinue(self, n, *args): pass

### EXPRESSIONS ###
    # Op stuff
    def visitBoolOp(self, n, *args):
        tys = []
        for val in n.values:
            self.dispatch(val, *args)
            tys.append(val.retic_type)
        n.retic_type = consistency.join(*tys)

    def visitBinOp(self, n, *args):
        self.dispatch(n.left, *args)
        self.dispatch(n.right, *args)
        ty = consistency.apply_binop(n.op, n.left.retic_type, n.right.retic_type)
        if ty:
            n.retic_type = ty
        else: raise exc.StaticTypeError(n, 'Can\'t {} operands of type {} and {}'.format(utils.stringify(n.op), n.left.retic_type, n.right.retic_type))

    def visitUnaryOp(self, n, *args):
        self.dispatch(n.operand, *args)
        ty = consistency.apply_unop(n.op, n.operand.retic_type)
        if ty:
            n.retic_type = ty
        else: raise StaticTypeError(n, 'Can\'t {} an operand of type {}'.format(utils.stringify(n.op), n.operand.retic_type))

    def visitCompare(self, n, *args):
        self.dispatch(n.left, *args)
        self.dispatch(n.comparators, *args)
        # Some rather complicated logic needed here to reject objects that definitely don't have __lt__ etc
        n.retic_type = retic_ast.Bool()
        
    # Collections stuff    
    def visitList(self, n, *args):
        tys = []
        for val in n.elts:
            self.dispatch(val, *args)
            tys.append(val.retic_type)
        n.retic_type = retic_ast.List(consistency.join(*tys))

    def visitTuple(self, n, *args):
        tys = []
        for val in n.elts:
            self.dispatch(val, *args)
            tys.append(val.retic_type)
        n.retic_type = retic_ast.Dyn() # Add tuple types

    def visitDict(self, n, *args):
        ktys = []
        vtys = []
        for key in n.keys:
            self.dispatch(key, *args)
            ktys.append(key.retic_type)
        for val in n.values:
            self.dispatch(val, *args)
            vtys.append(val.retic_type)
        n.retic_type = retic_ast.Dyn() # Add dict types

    def visitSet(self, n, *args):
        tys = []
        for val in n.elts:
            self.dispatch(val, *args)
            tys.append(val.retic_type)
        n.retic_type = retic_ast.Dyn() # Add set tys

    def visitListComp(self, n, env, *args):
        # Don't dispatch on the generators -- that will be done by getComprehensionScope
        comp_env = getComprehensionScope(n.generators, env, self, *args)
        self.dispatch(n.elt, comp_env, *args)
        n.retic_type = retic_ast.List(n.elt.retic_type)

    def visitSetComp(self, n, *args):
        self.dispatch(n.generators, *args)
        self.dispatch(n.elt, *args)
        n.retic_type = retic_ast.Dyn()

    def visitDictComp(self, n, *args):
        self.dispatch(n.generators, *args)
        self.dispatch(n.key, *args)
        self.dispatch(n.value, *args)
        n.retic_type = retic_ast.Dyn()

    def visitGeneratorExp(self, n, *args):
        self.dispatch(n.generators, *args)
        self.dispatch(n.elt, *args)
        n.retic_type = retic_ast.Dyn()

    def visitcomprehension(self, n, *args):
        self.dispatch(n.iter, *args)
        self.dispatch(n.ifs, *args)
        self.dispatch(n.target, *args)
        n.retic_type = n.target.retic_type

    # Control flow stuff
    def visitYield(self, n, *args):
        self.dispatch(n.value, *args)
        n.retic_type = retic_ast.Dyn()

    def visitYieldFrom(self, n, *args):
        self.dispatch(n.value, *args)
        n.retic_type = retic_ast.Dyn()

    def visitIfExp(self, n, *args):
        self.dispatch(n.test, *args)
        if not consistency.assignable(retic_ast.Bool(), n.test.retic_type):
            raise exc.StaticTypeError(n.test, 'Test expression has type {} but was expected to have type bool'.format(n.test.retic_type))
        self.dispatch(n.body, *args)
        self.dispatch(n.orelse, *args)
        n.retic_type = consistency.join(n.body.retic_type, n.orelse.retic_type)

    # Function stuff
    def visitCall(self, n, *args):
        self.dispatch(n.func, *args)
        self.dispatch(n.args, *args)
        [ast.keyword(arg=k.arg, value=self.dispatch(k.value, *args)) for k in n.keywords]
        self.dispatch(n.starargs, *args) if getattr(n, 'starargs', None) else None
        self.dispatch(n.kwargs, *args) if getattr(n, 'kwargs', None) else None
        
        ty, tyerr = consistency.apply(n.func, n.func.retic_type, n.args, n.keywords, n.starargs, n.kwargs)
        if not ty:
            raise tyerr
        else: n.retic_type = ty

    def visitLambda(self, n, env, *args):
        self.dispatch(n.args, env, *args)
        lam_env = getLambdaScope(n, env)
        self.dispatch(n.body, lam_env, *args)

        argtys = []
        for arg in n.args.args:
            if arg.annotation:
                argty = typeparser.typeparse(arg.annotation)
            else:
                argty = retic_ast.Dyn()
            argtys.append(argty)
        retty = n.body.retic_type

        n.retic_type = retic_ast.Function(retic_ast.PosAT(argtys), retty)

    # Variable stuff
    def visitAttribute(self, n, *args):
        self.dispatch(n.value, *args)
        
        if isinstance(n.value.retic_type, retic_ast.Dyn):
            n.retic_type = retic_ast.Dyn()
        elif isinstance(n.value.retic_type, retic_ast.Bot):
            n.retic_type = retic_ast.Bot()
        else:
            raise exc.StaticTypeError(n.value, 'Cannot get attributes from a value of type {}'.format(n.value.retic_type))

    def visitSubscript(self, n, *args):
        self.dispatch(n.value, *args)
        self.dispatch(n.slice, n.value.retic_type, n, *args)
        n.retic_type = n.slice.retic_type

    def visitIndex(self, n, orig_type, orig_node, *args):
        self.dispatch(n.value, *args)
        if isinstance(orig_type, retic_ast.List):
            if consistency.assignable(retic_ast.Int(), n.value.retic_type):
                n.retic_type = orig_type.elts
            else:
                raise exc.StaticTypeError(n.value, 'Cannot index into a List with a value of type {}; value of type int required'.format(n.value.retic_type))
        elif isinstance(orig_type, retic_ast.Dyn):
            n.retic_type = retic_ast.Dyn()
        elif isinstance(orig_type, retic_ast.Bot):
            n.retic_type = retic_ast.Bot()
        else:
            raise exc.StaticTypeError(orig_node, 'Cannot index into a value of type {}'.format(orig_type))

    def visitSlice(self, n, orig_type, orig_node, *args):
        self.dispatch(n.lower, *args)
        self.dispatch(n.upper, *args)
        self.dispatch(n.step, *args)
        if isinstance(orig_type, retic_ast.List):
            if not consistency.assignable(retic_ast.Int(), n.lower.retic_type):
                raise exc.StaticTypeError(n.lower, 'Cannot index into a List with a lower bound of type {}; value of type int required'.format(n.lower.retic_type))
            elif not consistency.assignable(retic_ast.Int(), n.upper.retic_type):
                raise exc.StaticTypeError(n.upper, 'Cannot index into a List with an upper bound of type {}; value of type int required'.format(n.upper.retic_type))
            elif not consistency.assignable(retic_ast.Int(), n.step.retic_type):
                raise exc.StaticTypeError(n.step, 'Cannot index into a List with a step of type {}; value of type int required'.format(n.step.retic_type))
            else:
                n.retic_type = retic_ast.List(elts=orig_type.elts)
        elif isinstance(orig_type, retic_ast.Dyn):
            n.retic_type = retic_ast.Dyn()
        elif isinstance(orig_type, retic_ast.Bot):
            n.retic_type = retic_ast.Bot()
        else:
            raise exc.StaticTypeError(orig_node, 'Cannot index into a value of type {}'.format(orig_type))

    def visitExtSlice(self, n, orig_type, orig_node, *args):
        # I have no idea what to do with ExtSlices and I can't find an example where they're used, so...
        self.dispatch(n.dims, *args)
        n.retic_type = retic_ast.Dyn()

    def visitStarred(self, n, *args):
        # Starrd exps can only be assignment targets. The starred thing had better be an iterable thing like a list or tuple, I think
        self.dispatch(n.value, *args)

        if not consistency.assignable(retic_ast.List(elts=retic_ast.Dyn()), n.value.retic_type):
            raise exc.StaticTypeError(n.value, 'Starred value must be a list or tuple, but has type {}'.format(n.value.retic_type))

        n.retic_type = n.value.retic_type

    def visitNameConstant(self, n, *args):
        if n.value is True or n.value is False:
            n.retic_type = retic_ast.Bool()
        elif n.value is None:
            n.retic_type = retic_ast.Void() 
        else: n.retic_type = retic_ast.Dyn()

    def visitName(self, n, env, *args):
        if n.id in env:
            n.retic_type = env[n.id]
        else:
            n.retic_type = retic_ast.Dyn()

    def visitNum(self, n, *args):
        if isinstance(n.n, int):
            n.retic_type = retic_ast.Int()
        else:
            n.retic_type = retic_ast.Dyn()

    def visitStr(self, n, *args):
        n.retic_type = retic_ast.Str()

    def visitBytes(self, n, *args):
        n.retic_type = retic_ast.Dyn()

    def visitEllipsis(self, n, *args):
        n.retic_type = retic_ast.Dyn()
    
    def visitGlobal(self, n, *args): pass
    def visitNonlocal(self, n, *args): pass
