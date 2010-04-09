"""
	Fast template engine, does very simple parsing (no regex, one split) and then generates the AST tree directly.
	The AST tree is compiled to bytecode and cached (so only the first run of a template must compile).
	The bytecode cache is in-memory only.
"""
import re, io, os, ast, builtins, copy
from ast import *

__all__ = ['template']

# to get complete compliance with all of python's type specifiers, we use a small regex
type_re = re.compile("[0-9.#0+ -]*[diouxXeEfFgGcrsqm]")

def template(text=None, filename=None, stripWhitespace=False, encoding="utf8", base_path=".", **kw):
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

		>>> ''.join(template(text=\"""
		...	%(if foo:)
		... foo is true
		... %(elif bar:)
		... bar is true
		... %(else:)
		... nothing is true
		... %/\""", foo=False, bar=False, stripWhitespace=True))
		'nothing is true'


		>>> import datetime
		>>> ''.join(template(text="now is: %(datetime.datetime.strptime('12/10/2001','%d/%m/%Y').strftime('%d/%m/%y'))s", datetime=datetime))
		'now is: 12/10/01'

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

		2. As a regular argument to include().

		>>> ''.join(template(text="<p>%(include('included.suba', '_test'))</p>", name="Paul"))
		'<p>This is a special message for Paul.</p>'

		3. As a keyword argument to include().

		>>> ''.join(template(text="<p>%(include('included.suba', base_path='_test'))</p>", name="Mary"))
		'<p>This is a special message for Mary.</p>'

		>>> os.remove("_test/included.suba")

	"""

	if text is None and filename is not None:
		h = filename.__hash__()
		try:
			h += os.path.getmtime(filename)
		except:
			pass
	elif filename is None and text is not None:
		h = text.__hash__()
	else:
		raise ArgumentError("template() requires either text= or filename= arguments.")
	base_path = base_path.split(os.path.sep)
	
	## Compile Phase ##
	# note about performance: compiling time is one-time only, so on scale it matters very very little.
	# what matters is the execution of the generated code.
	# absolutely anything that can be done to manipulate the generated AST to save execution time should be done.
	if _code_cache.get(h, None) is None:
		if filename is not None:
			text = open(os.path.sep.join(base_path + [filename]), "rb").read()
		if type(text) is bytes:
			text = str(text, encoding)
		if filename is None:
			filename = "<inline_template>"
		head = compile_ast(text, stripWhitespace=stripWhitespace, encoding=encoding, base_path=base_path)
		# print("COMPILING:", ast.dump(head))
		_code_cache[h] = compile(head, filename, 'exec')

	## Execution Phase ##
	# provide a few global helpers and then execute the cached byte code
	loc = {}
	glob = {}
	exec(_code_cache[h], glob, loc)
	# print("ARGUMENTS: %s" % kw)
	gen = loc['execute'](**kw)
	return gen

def compile_ast(text, stripWhitespace=False, encoding=None, filename=None, transform=True, base_path=None):
	""" Builds a Module ast tree.  Containing a single function: execute, a generator function. 
		stripWhitespace and encoding are the same as in template().
		filename is only used in debugging output, auto-generated if not specified.
	"""
	head = Module(body=[ # build the first node of the new code tree
		# which will be a module with a single function: 'execute', a generator function
		FunctionDef(name='execute', args=arguments(args=[], vararg=None, varargannotation=None, kwonlyargs=[], 
				kwarg='args', kwargannotation=None, defaults=[], kw_defaults=[]), 
			body=[], decorator_list=[], returns=None, lineno=0),
		],lineno=0)
	# point a cursor into the tree where we will build from
	# the cursor is a stack, so cursor[-1] is the current location for insertions
	cursor = []
	cursor.append(head.body[0].body) # this points the cursor at the body of the 'execute' function
	# split up the text into chunks for parsing
	chunks = text.split('%')
	c = 0
	# re-combine chunks that are not breaks between eval sections
	while True:
		if c >= len(chunks) - 1: break # force re-eval of len() on each loop
		chunka = chunks[c]
		chunkb = chunks[c + 1]
		if chunkb[0] not in ('(','/'):
			chunks[c] = chunka + '%' + chunkb
			del chunks[c+1]
		else:
			c += 1
	# this is the current lineno within the source text
	lineno = 1 # we keep track of this as best we can, so that stack trace rendering points at the real template locations
	for c in range(len(chunks)):
		chunk = chunks[c]
		if len(chunk) == 0: continue
		if chunk[0] == '(':
			i = match_forward(chunk, ')', '(', start=1)
			# if we found a matched parentheses group %(...)...
			# then eval the middle, and yield the left overs from after the closing )
			if i == -1:
				raise TemplateFormatError("Unmatched '%%(' in template, beginning at: '%s'" % (chunk[0:50]))
			# each bit of template will be parsed into 3 chunks: %(<eval_part>)<type_part><text_part>
			# type_part is allowed to be empty
			eval_part = chunk[1:i]
			text_part = chunk[i+1:]
			type_part = None
			m = type_re.match(text_part)
			if m is not None:
				type_part = m.group(0)
				text_part = text_part[len(type_part):]
			do_descend = False
			if eval_part.endswith(":"): # if the statement to eval is like an if, while, or for, then we need to do some tricks
				eval_part += " pass" # add a temp. node, so we can parse the incomplete statement
				do_descend = True
			if eval_part.startswith("else:"):
				# for an else statement, just move the cursor back and over to the orelse block
				cursor[-1] = cursor[-2][-1].orelse
			else:
				if eval_part.startswith("elif "):
					cursor[-1] = cursor[-2][-1].orelse
					eval_part = eval_part[2:] # and add the if statement
					do_descend = True
				try: # parse the body of the %( ... ) group
					node = ast.parse(eval_part).body[0]
				except IndentationError as e: # fix up indentation errors to make sure they indicate the right spot in the actual template file
					e.filename = filename
					e.lineno += lineno - eval_part.count("\n")
					e.offset += 2 # should be 2 + (space between left margin and opening %), but i dont know how to count this atm
					raise
				except Exception as e:
					print("Error while parsing sub-expression: %s" % (eval_part))
					raise e
				
				# update all the line numbers
				# for child in ast.walk(node):
					# child.lineno = lineno

				# if this eval_part had a type_part attached (a type specifier as recognized by the % operator)
				# then wrap the node in a call to the % operator with this type specifier
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
						# the default case, just pass the type_part on to the % operator
						new = Expr(value=BinOp(left=Str(s='%'+type_part,lineno=0), op=Mod(lineno=0), right=node.value,lineno=0),lineno=0)
						node = ast.copy_location(new, node.value)

				# put our new node into the ast tree
				cursor[-1].append(node)
				if do_descend: # adjust the cursor is needed
					del cursor[-1][-1].body[0] # clear the temp. node from this new block
					cursor.append(cursor[-1][-1].body) # and point our cursor inside the new block
			if len(text_part): # if there is left over text after the %( ... ) block, yield it out.
				cursor[-1].append(Expr(value=Yield(value=Str(s=text_part, lineno=lineno), lineno=lineno), lineno=lineno))
		elif chunk[0] == '/': # process a %/ block terminator, by decreasing the indent
			if len(cursor) < 2: # if there is nothing on the stack to close
				raise TemplateFormatError("Too many close tags %/")
			# before we ascend, make sure all the Expr's in the about-to-be-closed body are yielding
			_yieldall(cursor[-1])
			# pop the right side off the cursor stack
			cursor = cursor[:-1]
			if len(chunk) > 1:
				# if there was text after the '/' (almost always), yield it out.
				cursor[-1].append(Expr(value=Yield(value=Str(s=chunk[1:], lineno=lineno), lineno=lineno), lineno=lineno))
		else:
			# otherwise, it really wasn't a section that we care about
			# so put the % back in, and yield it out.
			cursor[-1].append(Expr(value=Yield(value=Str(
				s=("%"+chunk) if c > 0 else chunk, lineno=lineno), lineno=lineno), lineno=lineno))
		lineno += chunk.count("\n")
	ast.fix_missing_locations(head)
	# print("compile_ast, before transform:")
	# print(ast.dump(head, include_attributes=False))
	if transform:
		# patch up the generated tree, to reference the keyword arguments when necessary
		head = TemplateTransformer(stripWhitespace, encoding, base_path).visit(head)
		ast.fix_missing_locations(head)
		# print("compile_ast, after transform:")
		# print(ast.dump(head, include_attributes=False))
	return head

class TemplateFormatError(Exception): pass

def gen_bytes(gen, encoding):
	for item in gen:
		yield bytes(str(item), encoding)

def match_forward(text, find, against, start=0, stop=-1):
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
		self.seenStore = {'args': True} # 'args' is a special identifier that refers to the keyword argument dict
		# seenFuncs is a map of the functions that are defined in the template ("def foo(): ...")
		self.seenFuncs = {}
		self.encoding = encoding
		self.stripWhitespace = stripWhitespace
		self.base_path = base_path if base_path is not None else []
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
					if len(call.args) > 1:
						base_path = call.args[1].s
					# or if there was a base_path= kwarg provided, use that
					elif call.kwargs is not None:
						base_path = call.kwargs.get('base_path', None)
					if base_path is not None:
						if type(base_path) is str:
							base_path = os.path.sep.split(base_path)
						elif type(base_path) is Str:
							base_path = os.path.sep.split(base_path.s)
						else:
							raise TemplateFormatError("base_path argument to include() must be a string.")
					else:
						# if we didn't get one from the individual call
						# look for one that was given as an argument to the template() call
						base_path = self.base_path
					# get the ast tree that comes from this included file
					template_name = call.args[0].s
					fundef = include_ast(template_name, base_path)
					if fundef is None:
						raise TemplateFormatError("include_ast returned None")
					# return a copy of the the cached ast tree, because it may be further modified by the including template
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

	def visit_Call(self, node):
		""" 
			1. Calling 'include' is a special case; it is replaced inline, and not called.
			2. Also, if the template had defined a function locally, like:
			\"""
			%(def foo(x):)
				Foo: %(x)
			%/ 
			Here is some %(foo('bar')), and then some %(foo('baz')).
			\""" 
			Then, this function is treated carefully, it must be iterable (str counts).
		"""
		if type(node.func) is Name: 
			# if this Call is calling a function defined in the template, handle it safely
			# it's result must be join-able as string
			if self.seenFuncs.get(node.func.id, False) is not False:
				# replace the bare call with a ''.join(call)
				new = _call(Attribute(value=Str(s=''), attr='join', ctx=Load()), [node])
				self.generic_visit(node)
				return ast.copy_location(new, node)
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
def include_ast(filename, base_path=[]):
	h = filename.__hash__()
	try:
		h += os.path.getmtime(filename)
	except:
		pass
	if _code_cache.get(h,None) is None:
		module = compile_ast(open(os.path.sep.join(base_path + [filename])).read(), transform=False)
		fundef = module.body[0] # the only element of the Module's body, is the function defintion
		_code_cache[h] = fundef
	return _code_cache[h]

# these are quick utils for building chunks of ast
def _call(func,args):
	return Call(func=func, args=args, keywords=[], starargs=None,kwargs=None)
def _replace(node):
	return Attribute(value=node, attr='replace', ctx=Load())
def _quote(node):
	return Expr(value=_call(_replace(node), [Str(s="\""),Str(s="\\\"")]))
def _multiline(node):
	return Expr(value=_call(_replace(node), [Str(s='\n'),Str(s="\\\n")]))
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
	# value = '"Halt!"'
	# print(''.join(template(text="""%(for i in items:)%(i).%/""", items=['a','b','c'])))
	# print(''.join(template(text="%(value)q, the guard shouted.", value=value)))
	# '\\\\"Halt!\\\\", the guard shouted.'
	# print(''.join(template(text="<p>%(name)s</p>", name="John")))
	# try: os.makedirs("_test/")
	# except: pass
	# f = open("_test/included.suba", "w")
	# f.write("special message for %(name)s")
	# f.close()
	# print(''.join(template(text="%(args)s", name="John")))
	# print(''.join(template(text="<p>Hello, this is %(name)", name="John")))
	# print(''.join(template(text="<p>This is a %(include('_test/included.suba')), are you %(name)s?", name="John")))
	# # '<p>This is a special message for John, are you John?.</p>'
	# os.remove("_test/included.suba")

