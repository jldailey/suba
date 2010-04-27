"""
	Fast template engine, does very simple parsing (no regex, one split) and then generates the AST tree directly.
	The AST tree is compiled to bytecode and cached (so only the first run of a template must compile).
	The bytecode cache is in-memory only.
"""
import re, io, os, ast, builtins, copy, time
from ast import *

__all__ = ['template']

# to get complete compliance with all of python's type specifiers, we use a small regex
# q, and m, are added by suba
type_re = re.compile("[0-9.#0+ -]*[diouxXeEfFgGcrsqm]")

class TemplateFormatError(Exception): pass # fatal, caused by parsing failure, raises to caller
class TemplateResourceModified(Exception): pass # non-fatal, causes refresh from disk

def template(text=None, filename=None, stripWhitespace=False, encoding="utf8", base_path=".", skipCache=False, **kw):
	"""
		Fast template engine, does very simple parsing and then generates the AST tree directly.
		The AST tree is compiled to bytecode and cached (so only the first run of a template must compile).
		The code cache is in-memory only.
	
		The most basic syntax is similar to the % string substitution operator, but without the trailing type indicator.
		The template itself returns a generator, so you must read it out with something that will iterate it.
		Typically, one would just join() it all, but you could also flush each block directly if you wanted.

		>>> ''.join(template(text="<p>%(name)s</p>", name="John"))
		'<p>John</p>'
		
		The 'm' type specifier will escape multiline strings.

		>>> ''.join(template(text="%(foo)m", foo=""\"Line 1:
		... Line 2:
		... Line 3:\"""))
		'Line 1:\\\\\\nLine 2:\\\\\\nLine 3:'

		The 'q' type specifier will escape quotation marks within the value.

		>>> value = '"Halt!"'
		>>> ''.join(template(text="%(value)q, the guard shouted.", value=value))
		'\\\\"Halt!\\\\", the guard shouted.'


		>>> with open("_test_file_", "w") as f:
		...		f.write("<p>%(name)s</p>")
		15
		>>> ''.join(template(filename="_test_file_", name="Jacob"))
		'<p>Jacob</p>'
		>>> os.unlink("_test_file_")
		
		The more advanced syntax is just embedded python, with one rule for handling indents:
		 - Lines that end in ':' increase the indent of all following statements until a %/ is reached.

		>>> ''.join(template(text=\"""
		...	<ul>
		...	%(for item in items:)
		...		<li>%(item)s</li>
		...	%/
		...	</ul>""\", items=["John", "Paul", "Ringo"], stripWhitespace=True))
		...
		'<ul><li>John</li><li>Paul</li><li>Ringo</li></ul>'

		Tests for if, else, elif.

		>>> t = \"""
		...	%(if foo:)
		... foo is true
		... %(elif bar:)
		... bar is true
		... %(else:)
		... nothing is true
		... %/\"""
		>>> ''.join(template(text=t, foo=False, bar=False, stripWhitespace=True))
		'nothing is true'
		>>> ''.join(template(text=t, foo=True, bar=False, stripWhitespace=True))
		'foo is true'
		>>> ''.join(template(text=t, foo=False, bar=True, stripWhitespace=True))
		'bar is true'

		You can import modules and use them in the template.

		>>> import datetime
		>>> ''.join(template(text="now is:%(import datetime) %(datetime.datetime.strptime('12/10/2001','%d/%m/%Y').strftime('%d/%m/%y'))s"))
		'now is: 12/10/01'

		You can use any conversion specifier that the Mod (%) operator supports.

		>>> pi = 3.1415926
		>>> ''.join(template(text="pi is about %(pi)d, %(pi).2f, %(pi).4f", pi=pi))
		'pi is about 3, 3.14, 3.1416'

		Includes are supported.  The included file is compiled and inlined wherever it is included.

		>>> try: os.makedirs("_test/")
		... except: pass
		>>> f = open("_test/included.suba", "w")
		>>> f.write("This is a special message for %(name)s.")
		39
		>>> f.close()
		>>> ''.join(template(text="<p>%(include('_test/included.suba'))</p>", name="John"))
		'<p>This is a special message for John.</p>'

		You can specify a base_path, a location to find templates, in three ways. (default is '.')

		1. As a keyword argument to template().

		>>> ''.join(template(text="<p>%(include('included.suba'))</p>", base_path="_test", name="Peter"))
		'<p>This is a special message for Peter.</p>'

		2. As a regular argument to include() within the template itself.

		>>> ''.join(template(text="<p>%(include('included.suba', '_test'))</p>", name="Paul"))
		'<p>This is a special message for Paul.</p>'

		3. As a keyword argument to include() within the template.

		>>> ''.join(template(text="<p>%(include('included.suba', base_path='_test'))</p>", name="Mary"))
		'<p>This is a special message for Mary.</p>'

		If the file changes, the cache automatically updates on the next call.

		>>> time.sleep(1) # make sure the mtime actually changes
		>>> os.remove("_test/included.suba")
		>>> f = open("_test/included.suba", "w")
		>>> f.write("Thank you %(name)s, for the message!")
		36
		>>> f.close()
		>>> ''.join(template(text="<p>%(include('included.suba', base_path='_test'))</p>", name="Mary"))
		'<p>Thank you Mary, for the message!</p>'

		>>> os.remove("_test/included.suba")

		You can define functions locally in the template.

		>>> ''.join(template(text=\"""%(def hex(s): return int(s, 16))%(hex('111'))d""\"))
		'273'

		>>> ''.join(template(text=\"""
		... %(def hex(s):
		...		return int(s, 16))
		... Your hex values are: %(for k,v in args.items():)
		...	 %(k)=%(hex(v))d,
		...	%/
		... \""", a="111", b="333", stripWhitespace=True))
		'Your hex values are: a=273,b=819,'

		You can use functions as template macros, not just to compute return values.

		>>> ''.join(template(text=\"""
		... %(def li(data):)
		...		<li>%(data)</li>%(# notice no print statement)
		... %/
		... %(li('one'))
		... %(li('two'))
		... \""", stripWhitespace=True))
		'<li>one</li><li>two</li>'

		If you mess up the indentation of your python code in your template, it will alert you with a proper line number.

		>>> f = open("_test/errors.suba", "w")
		>>> f.write(""\"Line 1
		... Line Two
		... %("Line 3")
		... %( if True:)
		...		^^ with a wrong indent
		... %/""\")
		78
		>>> f.close()
		>>> ''.join(template(filename="errors.suba", base_path="_test"))
		Traceback (most recent call last):
			...
			File "errors.suba", Line 4
				if True:
				 ^
		IndentationError: unexpected indent
		>>> try: os.remove("_test/errors.suba")
		... except: pass
		
		TODO: more tests of this line number stuff, such as with includes, etc.

	"""
	path = base_path.split(os.path.sep)

	if text is None and filename is not None:
		# never allow absolute paths, or '..', in filenames
		full_name = os.path.sep.join(path + [f for f in filename.split(os.path.sep) if f != '..' and f != ''])
		h = full_name.__hash__()
		h += os.path.getmtime(full_name)
	elif filename is None and text is not None:
		h = text.__hash__()
	else:
		raise ArgumentError("template() requires either text= or filename= arguments.")
	
	## Compile Phase ##
	# note about performance: compiling time is one-time only, so on scale it matters very very little.
	# what matters is the execution of the generated code.
	# absolutely anything that can be done to manipulate the generated AST to save execution time should be done.
	if skipCache or _code_cache.get(h, None) is None:
		if filename is not None:
			text = open(os.path.sep.join(path + [filename]), "rb").read()
		if type(text) is bytes:
			text = str(text, encoding)
		if filename is None:
			filename = "<inline_template>"
		try:
			head = compile_ast(text, stripWhitespace=stripWhitespace, encoding=encoding, base_path=path)
		except IndentationError as e:
			e.filename = filename
			raise
		# print("COMPILING:", ast.dump(head, include_attributes=False))
		_code_cache[h] = compile(head, filename, 'exec')

	## Execution Phase ##
	# provide a few global helpers and then execute the cached byte code
	loc = {}
	glob = {'TemplateResourceModified':TemplateResourceModified}
	# this executes the Module(), which defines a function inside loc
	exec(_code_cache[h], glob, loc)
	# calling execute returns the generator, without having run any of the code inside yet
	gen = loc['execute'](**kw)
	# we pull the first item out, causing the preamble to run, yielding either True, or a TemplateResourceModified exception
	for err in gen:
		if err is None:
			return gen
		if type(err) == TemplateResourceModified:
			# print("Forcing reload.",str(err))
			del gen
			return template(text=text, filename=filename, stripWhitespace=stripWhitespace, encoding=encoding, base_path=base_path, skipCache=True, **kw)
		raise Exception("execute did not return a proper generator, first value was:",err)


def compile_ast(text, stripWhitespace=False, encoding=None, transform=True, base_path=None):
	"Builds a Module ast tree.	Containing a single function: execute, a generator function."
	head = Module(body=[ 
		# build the first node of the new code tree
		# which will be a module with a single function: 'execute', a generator function
		FunctionDef(name='execute', args=arguments(args=[], vararg=None, varargannotation=None, kwonlyargs=[], 
				kwarg='args', kwargannotation=None, defaults=[], kw_defaults=[]), 
			body=[], decorator_list=[], returns=None, lineno=0),
		],lineno=0)
	cursor = [] # a stack
	cursor.append(head.body[0].body)
	# gets a series of ast,motion pairs from the gen_ast generator
	for expr, motion in gen_ast(gen_chunks(text)):
		if expr is not None: # add the ast node to the tree
			cursor[-1].append(expr)
		# then adjust the cursor according to motion
		if motion is MOTION_ASCEND: # _ASCEND closes a block, such as an if, else, etc.
			if len(cursor) < 2:
				raise TemplateFormatError("Too many closings tags ('%/')")
			# before we ascend, make sure all the Expr's in the about-to-be-closed body are yielding
			_yieldall(cursor[-1])
			cursor = cursor[:-1]
		elif motion is MOTION_DESCEND: # _DESCEND puts the cursor in a .body
			cursor.append(expr.body) # (if, def, with, try, except, etc. all work this way)
			del cursor[-1][0] # the temporary 'pass' statement
		elif motion is MOTION_ELSE_DESCEND: # _ELSE_DESCEND is used for else and elif
			cursor[-1] = cursor[-2][-1].orelse

	ast.fix_missing_locations(head)

	if transform:
		head.body[0].body = [
			# include a single 'import os' at the top
			Import(names=[alias(name='os', asname=None, lineno=0, col_offset=0)], lineno=0, col_offset=0),
		] + head.body[0].body
		# patch up the generated tree, to reference the keyword arguments when necessary, etc
		t = TemplateTransformer(stripWhitespace, encoding, base_path)
		head = t.visit(head)
		# any includes that were inlined during the transform will add freshness checks to t.preamble
		# if the checks fail, they will yield an exception (not raise it)
		# template() above always reads the first item from the generator
		# if none of the checks yielded (so it's all safe to proceed with this cached template)
		# then we must yield None to release the generator to the caller see: the end of template()
		t.preamble.append(Expr(value=Yield(value=Name(id='None', ctx=Load()))))
		# now insert the preamble into the proper spot in the body (after the import, before the real stuff)
		head.body[0].body[1:1] = t.preamble
		del t
		# then fill in any missing lineno, col_offsets so that compile() wont complain
		ast.fix_missing_locations(head)

	return head

def gen_chunks(text, start=0):
	"""A generator that does lexing for our parser. Yields <text>,<type> pairs.

		>>> list(gen_chunks("abc%(123)def%g"))
		[('abc', None), ('(123)', 'd'), ('ef', None), ('%', None), ('g', None)]

		>>> list(gen_chunks("abc%()def%g"))
		[('abc', None), ('()', 'd'), ('ef', None), ('%', None), ('g', None)]

		>>> list(gen_chunks("abc%()def%%g"))
		[('abc', None), ('()', 'd'), ('ef', None), ('%', None), ('', None), ('%', None), ('g', None)]

		>>> list(gen_chunks("abc%(print('%s'))sef%%g"))
		[('abc', None), ("(print('%s'))", 's'), ('ef', None), ('%', None), ('', None), ('%', None), ('g', None)]

		>>> list(gen_chunks("<ul>%(for item in items:)<li>%(item)s</li>%/</ul>"))
		[('<ul>', None), ('(for item in items:)', None), ('<li>', None), ('(item)', 's'), ('</li>', None), ('/', None), ('</ul>', None)]

	"""

	while -1 < start < len(text):
		i = text.find('%',start)
		if i == -1:
			yield text[start:], None
			break
		yield text[start:i], None
		if text[i+1] == '(':
			m = match_forward(text, ')', '(', start=i+2)
			if m == -1:
				raise TemplateFormatError("Unmatched %%( starting at '%s'" % text[i:i+40])
			text_part = text[m+1:]
			type_part = None
			ma = type_re.match(text_part)
			start = m + 1
			if ma is not None:
				type_part = ma.group(0)
				start += len(type_part)
			yield text[i+1:m+1], type_part
		elif text[i+1] == '/':
			yield '/', None
			start = i + 2
		else:
			yield '%', None
			start = i + 1

MOTION_NONE = 0
MOTION_ASCEND = 1
MOTION_DESCEND = 2
MOTION_ELSE_DESCEND = 3
MOTION_ELIF_DESCEND = 4

def gen_ast(chunks):
	""" Given a chunks iterable, yields a series of [<ast>,<motion>] pairs.
		>>> [ (ast.dump(x),y) for x,y in gen_ast(gen_chunks("abc%(123)def%g")) ]
		[("Expr(value=Yield(value=Str(s='abc')))", 0), ("Expr(value=Yield(value=BinOp(left=Str(s='%d'), op=Mod(), right=Num(n=123))))", 0), ("Expr(value=Yield(value=Str(s='ef%g')))", 0)]
	"""
	stack = []
	lineno = 0
	def linecount(t):
		return max(t.count('\r'),t.count('\n'))
	for chunk, type_part in chunks:
		# print("CHUNK:",chunk.replace('\n','\\n').replace('\t','\\t'),type_part)
		if len(chunk) == 0:
			continue
		if chunk[0] in ('/','('):
			# yield any text on the stack first
			if len(stack) > 0:
				yield Expr(value=Yield(value=Str(s=''.join(stack)))), MOTION_NONE
				stack = []
		else:
			if len(chunk) > 0:
				lineno += linecount(chunk)
				stack.append(chunk)

		if chunk[0] == '/':
			yield None, MOTION_ASCEND
			if len(chunk) > 1:
				lineno += linecount(chunk)
				yield Expr(value=Yield(value=Str(s=chunk[1:]))), MOTION_NONE
		elif chunk[0] == '(':
			motion = MOTION_NONE
			node = None
			# eval the middle
			eval_part = chunk[1:-1]

			if eval_part.endswith(":"): # if the statement to eval is like an if, while, or for, then we need to do some tricks
				eval_part += " pass" # add a temp. node, so we can parse the incomplete statement
				motion = MOTION_DESCEND
			
			if eval_part.startswith("else:"):
				motion = MOTION_ELSE_DESCEND
			else:
				if eval_part.startswith("elif "):
					yield None, MOTION_ELSE_DESCEND # yield an immediate else descend
					motion = MOTION_DESCEND # then the 'if' statement from this line will descend regularly
					eval_part = eval_part[2:] # chop off the 'el' so we parse as a regular 'if' statement
				try: # parse the eval_part
					body = ast.parse(eval_part).body
					if len(body) > 0: # a block with no expressions (e.g., it was all comments) will have no nodes and can be skipped
						node = body[0]
				except IndentationError as e: # fix up indentation errors to make sure they indicate the right spot in the actual template file
					e.lineno += lineno - linecount(eval_part)
					e.offset += 2 # should be 2 + (space between left margin and opening %), but i dont know how to count this atm
					raise
				except Exception as e:
					raise Exception("Error while parsing sub-expression: %s" % (eval_part), e)

				# if this eval_part had a type_part attached (a type specifier as recognized by the % operator)
				# then wrap the node in a call to the % operator with this type specifier
				# NOTE TO SELF: this depends on node.value, will this crash if you do %(if foo:)s, which is If(...,body=[]), with no value?
				if type_part is not None:
					# q and m are special modifiers used only in suba
					fq = type_part.find('q')
					fm = type_part.find('m')
					if fq > -1:
						new = _quote(node.value)
						node = ast.copy_location(new, node.value)
					if fm > -1:
						new = _multiline(node.value)
						node = ast.copy_location(new, node.value)
					if fq == -1 and fm == -1:
						# the default case, just pass the type_part on to the Mod operator
						new = Expr(value=Yield(value=BinOp(left=Str(s='%'+type_part), op=Mod(), right=node.value)))
						node = ast.copy_location(new, node.value)

			# yield the parsed node
			yield node, motion

	if len(stack) > 0:
		# yield the remaining text
		yield Expr(value=Yield(value=Str(s=''.join(stack)))), MOTION_NONE

def gen_bytes(gen, encoding):
	for item in gen:
		yield bytes(str(item), encoding)

def match_forward(text, find, against, start=0, stop=-1):
	"""This will find the index of the closing parantheses.
	'find' is the closing character, 'against' is the opening char."""
	count = 1
	if stop == -1:
		stop = len(text)
	for i in range(start,stop):
		if text[i] == against:
			count += 1
		elif text[i] == find:
			count -= 1
		if count == 0:
			return i
	return -1

class TemplateTransformer(ast.NodeTransformer):
	def __init__(self, stripWhitespace=False, encoding=None, base_path=None):
		ast.NodeTransformer.__init__(self)
		# seenStore is a map of variables that are created within the template (not passed in)
		self.seenStore = {
			'args': True, # 'args' is a special identifier that refers to the keyword argument dict
			'TemplateResourceModified': True, # also a special case, because we forcibly add a reference
			# inside all templates
		}
		# seenFuncs is a map of the functions that are defined in the template ("def foo(): ...")
		self.seenFuncs = {}
		self.encoding = encoding
		self.stripWhitespace = stripWhitespace
		self.base_path = base_path if base_path is not None else []
		self.preamble = []

	def visit_Expr(self, node):
		""" When capturing a call to include, we must grab it here, so we can replace the whole Expr(Call('include')).
		"""
		if type(node.value) is Call:
			call = node.value
			if type(call.func) is Name:
				if call.func.id == 'include': 
					if len(call.args) < 1:
						raise TemplateFormatError("include requires at least a filename as an argument.")
					base_path = None
					# if the original call to include had an additional argument
					# use that argument as the base_path
					# print('call',ast.dump(call))
					if len(call.args) > 1:
						base_path = call.args[1].s
					# or if there was a base_path= kwarg provided, use that
					elif len(call.keywords) > 0:
						for k in call.keywords:
							if k.arg == "base_path":
								base_path = k.value.s
					if base_path is None:
						# if we didn't get one from the call to include
						# look for one that was given as an argument to the template() call
						base_path = self.base_path
					if type(base_path) is str:
						base_path = base_path.split(os.path.sep)
					# the first argument to include() is the filename
					template_name = call.args[0].s
					# get the ast tree that comes from this included file
					check, fundef = include_ast(template_name, base_path)
					# each include produces the code to execute, plus some code to check for freshness
					# this code absolutely must run first, because we can't restart the generator once it has already yielded
					self.preamble.append(check)
					if fundef is None:
						raise TemplateFormatError("include_ast returned None")
					# return a copy of the the cached ast tree, because it will be further modified to fit with the including template
					fundef = copy.deepcopy(fundef)
					_yieldall(fundef.body)
					for expr in fundef.body:
						self.generic_visit(expr)
					return fundef.body
		elif type(node.value) is Yield:
			y = node.value
			if type(y.value) == Str:
				if self.stripWhitespace:
					s = strip_whitespace(y.value.s)
					if len(s) == 0:
						return None # dont even compile in the Expr(Yield) if it was only yielding white space
					else:
						y.value.s = s
			elif type(y.value) == Call:
				call = y.value
				if type(call.func) is Name:
					if self.seenFuncs.get(call.func.id, False) is not False: # was defined locally
						# replace the Call with one to ''.join(Call)
						y.value = _call(Attribute(value=Str(s=''), attr='join', ctx=Load()), [y.value])
						ast.copy_location(y.value, node)
		self.generic_visit(node)
		return node

	def visit_FunctionDef(self, node):
		self.seenFuncs[node.name] = True
		for arg in node.args.args:
			self.seenStore[arg.arg] = True
		# iterate over each Expr in the body, and make sure it is yielding
		_yieldall(node.body)
		self.generic_visit(node)
		return node
	
	def visit_Import(self, node):
		for name in node.names:
			if name.asname is not None:
				self.seenStore[name.asname] = True
			else:
				self.seenStore[name.name] = True
		self.generic_visit(node)
		return node

	def visit_Name(self, node):
		if type(node.ctx) == Store:
				self.seenStore[node.id] = True
				return node
		# 'include' is handled specially elsewhere
		if node.id == 'include':
			return node
		# else if we are reading a named variable, and it hasn't been set before
		if type(node.ctx) == ast.Load and self.seenStore.get(node.id, False) is False:
			# check if it is a builtin, or is a function defined in the template
			if builtins.__dict__.get(node.id,None) is None and self.seenFuncs.get(node.id,None) is None:
				# if not, replace it with a reference to args[...]
				new = Subscript(value=Name(id='args', ctx=Load()),
					slice=Index(value=Str(s=node.id)), ctx=node.ctx, lineno=node.lineno)
				return ast.copy_location(new, node)
			return node
		else: # is Load, but a local variable
			return node

def strip_whitespace(s):
	out = io.StringIO()
	remove = False
	for c in s:
		if c == "\n":
			remove = True
		if remove and c not in ("\n", "\t", " "):
			remove = False
		if not remove:
			out.write(c)
	return out.getvalue()

_code_cache = {}
def include_ast(filename, base_path=None):
	if base_path is None:
		base_path = []
	full_name = os.path.sep.join(base_path + [f for f in filename.split(os.path.sep) if f != '..' and f != ''])
	h = full_name.__hash__()
	m = os.path.getmtime(full_name)
	h += m
	if _code_cache.get(h,None) is None:
		with open(full_name) as f:
			module = compile_ast(f.read(), transform=False)
			fundef = module.body[0] # the only element of the Module's body, is the function defintion
			_code_cache[h] = fundef
	return _checkMtimeAndYield(full_name, m), _code_cache[h]

# these are quick utils for building chunks of ast
def _call(func,args):
	""" func(args) """
	return Call(func=func, args=args, keywords=[], starargs=None,kwargs=None)
def _replace(node):
	""" node.replace """
	return Attribute(value=node, attr='replace', ctx=Load())
def _quote(node):
	""" node.replace('\"',"\\\"") """
	return Expr(value=_call(_replace(node), [Str(s="\""),Str(s="\\\"")]))
def _multiline(node):
	""" node.replace('\n','\\n') """
	return Expr(value=_call(_replace(node), [Str(s='\n'),Str(s="\\\n")]))
def _compareMtime(full_name, mtime):
	return Compare(left=Call(func=Attribute(value=Attribute(value=Name(id='os', ctx=Load(), lineno=0), attr='path', ctx=Load()), 
		attr='getmtime', ctx=Load()), args=[Str(s=full_name)], keywords=[], starargs=None, kwargs=None), 
		ops=[Gt()], 
		comparators=[Num(n=mtime)])
def _checkMtimeAndYield(full_name, mtime):
	""" if os.path.getmtime(full_name) > mtime:
		yield TemplateResourceModified(full_name)
	""" # static checks like this are compiled into the top of include trees
	return If(test=_compareMtime(full_name, mtime), body=[
		Expr(value=Yield(value=Call(func=Name(id='TemplateResourceModified', ctx=Load(), lineno=0), 
			args=[Str(s=full_name)], keywords=[], starargs=None, kwargs=None)))
	], orelse=[])
def _yieldall(body):
	for i in range(len(body)):
		expr = body[i]
		if type(expr) is Expr:
			if type(expr.value) != Yield and not (type(expr.value) is Call and type(expr.value.func) is Name and expr.value.func.id is 'include'):
				new = Yield(value=expr.value)
				body[i].value = ast.copy_location(new, expr.value)

if __name__ == "__main__":
	import doctest
	doctest.testmod(raise_on_error=False)
	text = """abc%(123)def%g
	%(if True:)
	yes
	%(elif False:)
	elif here
	%(else:)
	no
	%/
	print(text)
	for node, dir in gen_ast(gen_chunks(text)):
		if node is not None:
			print(ast.dump(node), dir)
		else:
			print(node,dir)

	print(ast.dump(compile_ast(text)))
	print(list(gen_chunks(text)))
	print(list(gen_chunks("abc%(123)def%g")))
	print(list(gen_ast(gen_chunks("<ul>%(for item in items:)<li>%(item)s</li>%/</ul>"))))
	"""
