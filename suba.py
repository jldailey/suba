#!/usr/bin/env python3.1
"""
	Fast template engine, does very simple parsing (no regex, one split) and then generates the AST tree directly.
	The AST tree is compiled to bytecode and cached (so only the first run of a template must compile).
	The bytecode cache is in-memory only.
"""
import re, io, os, ast, builtins, copy, time
from ast import *

__all__ = ['template', 'synth']

# to get complete compliance with all of python's type specifiers, we use a small regex
# q and m, are added by suba
type_re = re.compile("[0-9.#0+-]*[diouxXeEfFgGcrsqm]")

class FormatError(Exception): pass # fatal, caused by parsing failure, raises to caller
class ResourceModified(Exception): pass # non-fatal, causes refresh from disk

CLOSE_MARK = '/'
OPEN_MARK = '%'
OPEN_PAREN = '('
CLOSE_PAREN = ')'

# by default a CLOSE_MARK will close 1 body, but in the case of elif, it might need to close more
ASCEND_COUNT = 1

class NoMotion: pass
class Ascend: pass
class Descend: pass
class ElseDescend: pass

def template(text=None, filename=None, stripWhitespace=False, encoding="utf8", root=".", skipCache=False, **kw):
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

		You can specify a root, a location to find templates, in three ways. (default is '.')

		1. As a keyword argument to template().

		>>> ''.join(template(text="<p>%(include('included.suba'))</p>", root="_test", name="Peter"))
		'<p>This is a special message for Peter.</p>'

		2. As a regular argument to include() within the template itself.

		>>> ''.join(template(text="<p>%(include('included.suba', '_test'))</p>", name="Paul"))
		'<p>This is a special message for Paul.</p>'

		3. As a keyword argument to include() within the template.

		>>> ''.join(template(text="<p>%(include('included.suba', root='_test'))</p>", name="Mary"))
		'<p>This is a special message for Mary.</p>'

		If the file changes, the cache automatically updates on the next call.

		>>> time.sleep(1) # make sure the mtime actually changes
		>>> os.remove("_test/included.suba")
		>>> f = open("_test/included.suba", "w")
		>>> f.write("Thank you %(name)s, for the message!")
		36
		>>> f.close()
		>>> ''.join(template(text="<p>%(include('included.suba', root='_test'))</p>", name="Mary"))
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
		... %(def li(data, cls=None):)
		...		<li%(if cls:) class="%(cls)"%/>%(data)</li>
		... %/
		... %(li('one'))
		... %(li('two', cls='foo'))\""", stripWhitespace=True))
		'<li>one</li><li class="foo">two</li>'

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
		>>> ''.join(template(filename="errors.suba", root="_test"))
		Traceback (most recent call last):
			...
			File "errors.suba", Line 4
				if True:
				 ^
		IndentationError: unexpected indent
		>>> try: os.remove("_test/errors.suba")
		... except: pass

		>>> ''.join(template(""\"
		...	line 2
		... %(x = 1/0)
		... line 4""\"))
		Traceback (most recent call last):
			...
			File "<inline_template>", line 3, in execute
		ZeroDivisionError: int division or modulo by zero
		
		TODO: more tests of this line number stuff, such as with includes, etc.
		TODO: improve the quality of these lineno tests, as doctest doesn't check the stacktrace
	"""
	path = root.split(os.path.sep)

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
			head = compile_ast(text, stripWhitespace=stripWhitespace, encoding=encoding, root=path)
		except IndentationError as e:
			e.filename = filename
			raise
		# print("COMPILING:", ast.dump(head, include_attributes=True))
		_code_cache[h] = compile(head, filename, 'exec')

	## Execution Phase ##
	# provide a few global helpers and then execute the cached byte code
	loc = {}
	glob = {'ResourceModified':ResourceModified}
	# this executes the Module(), which defines a function inside loc
	exec(_code_cache[h], glob, loc)
	# calling execute returns the generator, without having run any of the code inside yet
	gen = loc['execute'](**kw)
	# we pull the first item out, causing the preamble to run, yielding either True, or a ResourceModified exception
	for err in gen:
		if err is None:
			return gen
		if type(err) == ResourceModified:
			# print("Forcing reload.",str(err))
			del gen
			return template(text=text, filename=filename, stripWhitespace=stripWhitespace, encoding=encoding, root=root, skipCache=True, **kw)
		raise Exception("execute did not return a proper generator, first value was:",err)

def compile_ast(text, stripWhitespace=False, encoding=None, transform=True, root=None):
	"Builds a Module ast tree.	Containing a single function: execute, a generator function."
	global ASCEND_COUNT
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
		# print("expr: %s motion: %s" % (expr, motion))
		if expr is not None: # add the ast node to the tree
			cursor[-1].append(expr)
			# print("compile_ast:",ast.dump(expr, include_attributes=True))
		# then adjust the cursor according to motion
		if motion is Ascend: # Ascend closes a block, such as an if, else, etc.
			if len(cursor) < 2:
				raise FormatError("Too many closings tags ('%%/'), cursor: %s" % (cursor, ))
			# as we ascend, make sure all the Expr's in the about-to-be-closed body are yielding
			for _ in range(ASCEND_COUNT):
				_yieldall(cursor[-1])
				cursor = cursor[:-1]
			ASCEND_COUNT = 1
		elif motion is Descend: # Descend opens a new block, and puts the cursor inside
			cursor.append(expr.body) # (if, def, with, try, except, etc. all work this way)
			del cursor[-1][0] # delete the temporary 'pass' statement
		elif motion is ElseDescend: # ElseDescend is used for else and elif
			# it just steps the cursor sideways, to the else block
			cursor[-1] = cursor[-2][-1].orelse

	if transform:
		head.body[0].body = [
			# include a single 'import os' at the top
			Import(names=[alias(name='os', asname=None, lineno=0, col_offset=0)], lineno=0, col_offset=0),
		] + head.body[0].body
		# patch up the generated tree, to reference the keyword arguments when necessary, etc
		t = Transformer(stripWhitespace, encoding, root)
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

	# print("COMPILED: ", ast.dump(head))
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

		Test for a parse error condition

		>>> list(gen_chunks("<ul>%(for item in items:)<li>%(item)s</li>%//</ul>"))
		[('<ul>', None), ('(for item in items:)', None), ('<li>', None), ('(item)', 's'), ('</li>', None), ('/', None), ('/</ul>', None)]

		>>> list(gen_chunks("/<ul>%(for item in items:)<li>%(item)s</li>%//</ul>"))
		[('/<ul>', None), ('(for item in items:)', None), ('<li>', None), ('(item)', 's'), ('</li>', None), ('/', None), ('/</ul>', None)]

		>>> list(gen_chunks("(<ul>%(for item in items:)<li>%(item)s</li>%//</ul>"))
		[('(<ul>', None), ('(for item in items:)', None), ('<li>', None), ('(item)', 's'), ('</li>', None), ('/', None), ('/</ul>', None)]

	"""

	while -1 < start < len(text):
		i = text.find(OPEN_MARK,start)
		# print("i = %d, text[...] = %s" % (i, text[i-3:i+4]))
		if i == -1:
			yield text[start:], None
			break
		yield text[start:i], None
		if text[i+1] == OPEN_PAREN:
			m = match_forward(text, CLOSE_PAREN, OPEN_PAREN, start=i+2)
			if m == -1:
				raise FormatError("Unmatched %s%s starting at '%s'" % (OPEN_MARK, OPEN_PAREN, text[i:i+40]))
			text_part = text[m+1:]
			type_part = None
			ma = type_re.match(text_part)
			start = m + 1
			if ma is not None:
				type_part = ma.group(0)
				start += len(type_part)
			yield text[i+1:m+1], type_part
		elif text[i+1] == CLOSE_MARK:
			yield CLOSE_MARK, None
			start = i + 2
		else:
			yield OPEN_MARK, None
			start = i + 1

def linecount(t):
	return max(t.count('\r'),t.count('\n'))

def gen_ast(chunks):
	""" Given a chunks iterable, yields a series of [<ast>,<motion>] pairs.

		>>> [ (ast.dump(x),y.__name__) for x,y in gen_ast(gen_chunks("abc%(123)def%g")) ]
		[("Expr(value=Yield(value=Str(s='abc')))", 'NoMotion'), ("Expr(value=Yield(value=BinOp(left=Str(s='%d'), op=Mod(), right=Num(n=123))))", 'NoMotion'), ("Expr(value=Yield(value=Str(s='ef%g')))", 'NoMotion')]

		>>> list(gen_chunks("/*comment*/%(for i in range(1):) foo%//*comment2*/"))
		[('/*comment*/', None), ('(for i in range(1):)', None), (' foo', None), ('/', None), ('/*comment2*/', None)]
	

		>>> [ (ast.dump(x),y.__name__) for x,y in gen_ast(gen_chunks("/*comment*/%('foo')")) ]
		[("Expr(value=Yield(value=Str(s='/*comment*/')))", 'NoMotion'), ("Expr(value=Str(s='foo'))", 'NoMotion')]

	"""
	global ASCEND_COUNT
	stack = []
	lineno = 1
	# a closure to assign the lineno to all nodes
	def locate(n):
		if n is not None:
			for node in ast.walk(n):
				node.lineno = lineno
				node.col_offset = 0
		return n

	for chunk, type_part in chunks:

		# ignore empty chunks
		if len(chunk) == 0:
			continue

		# if it's a plain piece of text
		if chunk[0] not in (CLOSE_MARK, OPEN_PAREN):
			stack.append(chunk) # stack it up for later
			continue # get the next chunk
		# otherwise, it is something we will need to eval

		# yield all text on the stack before proceeding
		if len(stack) > 0:
			text = ''.join(stack)
			if len(text) > 0:
				yield locate(Expr(value=Yield(value=Str(s=text)))), NoMotion
				lineno += linecount(text) # count it
			stack = []

		# if it's a close marker
		if chunk == CLOSE_MARK:
			# yield the Ascend motion for the cursor
			yield None, Ascend
			# and if there is any text after the close
			if len(chunk) > 1:
				stack.append(chunk[1:]) # stack it for later

		elif chunk[0] == OPEN_PAREN:
			# set up the default node, motion we will yield based on what we find inside this OPEN_PAREN
			node = None
			motion = NoMotion
			# eval the middle (without the parens)
			eval_part = chunk[1:-1]
			# if the statement to eval is like an if, while, or for, then we need to do some tricks
			if eval_part.endswith(":"):
				eval_part += " pass" # add a temp. node, so we can parse the incomplete statement
				motion = Descend

			if eval_part.startswith("else:"):
				motion = ElseDescend
			else:
				if eval_part.startswith("elif "):
					yield None, ElseDescend # yield an immediate else descend
					eval_part = eval_part[2:] # chop off the 'el' so we parse as a regular 'if' statement
					motion = Descend # then the 'if' statement from this line will descend regularly
					ASCEND_COUNT += 1
				try: # parse the eval_part
					body = ast.parse(eval_part).body
					if len(body) > 0: # a block with no expressions (e.g., it was all comments) will have no nodes and can be skipped
						node = body[0]
						node = locate(node)
				except IndentationError as e: # fix up indentation errors to make sure they indicate the right spot in the actual template file
					e.lineno += lineno - linecount(eval_part)
					e.offset += 1 # should be 1 + (space between left margin and opening %), but i dont know how to count this atm
					raise
				except Exception as e:
					e.lineno += lineno - linecount(eval_part)
					e.offset += 1
					raise Exception("Error while parsing sub-expression: %s, %s" % (eval_part, str(e)), e)

				# if this eval_part had a type_part attached (a conversion specifier as recognized by the % operator)
				# then wrap the node in a call to the % operator with this type specifier
				if type_part is not None:
					# you can't give a type on a node with no value
					if not hasattr(node, 'value'):
						# so just put the type_part on the stack as regular text to be yielded
						stack.append(type_part)
					else:
						# q and m are special modifiers used only in suba
						fq = type_part.find('q')
						fm = type_part.find('m')
						if fq > -1:
							new = _quote(node.value)
							node = ast.copy_location(new, node.value)
						if fm > -1:
							new = _multiline(node.value)
							node = ast.copy_location(new, node.value)
						# for the default types, just pass the type_part on to the Mod operator
						if fq == -1 and fm == -1:
							new = Expr(value=Yield(value=BinOp(left=Str(s='%'+type_part), op=Mod(), right=node.value)))
							node = ast.copy_location(new, node.value)

			# yield the parsed node
			# print("gen_ast:", ast.dump(node, include_attributes=True))
			yield node, motion
		else:
			stack.append(chunk)

	if len(stack) > 0:
		# yield the remaining text
		yield locate(Expr(value=Yield(value=Str(s=''.join(stack))))), NoMotion

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

class Transformer(ast.NodeTransformer):
	def __init__(self, stripWhitespace=False, encoding=None, root=None):
		ast.NodeTransformer.__init__(self)
		# seenStore is a map of variables that are created within the template (not passed in)
		self.seenStore = {
			'args': True, # 'args' is a special identifier that refers to the keyword argument dict
			'ResourceModified': True, # also a special case, because we forcibly add a reference
			'None': True, 'True': True, 'False': True, # constants that are defined but arent in builtins
		}
		# seenFuncs is a map of the functions that are defined in the template ("def foo(): ...")
		self.seenFuncs = {}
		self.encoding = encoding
		self.stripWhitespace = stripWhitespace
		self.root = root if root is not None else []
		self.preamble = []

	def visit_Expr(self, node):
		""" When capturing a call to include, we must grab it here, so we can replace the whole Expr(Call('include')).
		"""
		if type(node.value) is Call:
			call = node.value
			if type(call.func) is Name and call.func.id == 'include': 
				if len(call.args) < 1:
					raise FormatError("include requires at least a filename as an argument.")
				root = None
				# if the original call to include had an additional argument
				# use that argument as the root
				# print('call',ast.dump(call))
				if len(call.args) > 1:
					root = call.args[1].s
				# or if there was a root= kwarg provided, use that
				elif len(call.keywords) > 0:
					for k in call.keywords:
						if k.arg == "root":
							root = k.value.s
				if root is None:
					# if we didn't get one from the call to include
					# look for one that was given as an argument to the template() call
					root = self.root
				if type(root) is str:
					root = root.split(os.path.sep)
				# the first argument to include() is the filename
				template_name = call.args[0].s
				# get the ast tree that comes from this included file
				check, fundef = include_ast(template_name, root)
				# each include produces the code to execute, plus some code to check for freshness
				# this code absolutely must run first, because we can't restart the generator once it has already yielded
				self.preamble.append(check)
				if fundef is None:
					raise FormatError("include_ast returned None")
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
def include_ast(filename, root=None):
	if root is None:
		root = []
	full_name = os.path.sep.join(root + [f for f in filename.split(os.path.sep) if f != '..' and f != ''])
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
	""" os.path.getmtime(full_name) > mtime """
	return Compare(left=Call(func=Attribute(value=Attribute(value=Name(id='os', ctx=Load(), lineno=0), attr='path', ctx=Load()), 
		attr='getmtime', ctx=Load()), args=[Str(s=full_name)], keywords=[], starargs=None, kwargs=None), 
		ops=[Gt()], 
		comparators=[Num(n=mtime)])
def _checkMtimeAndYield(full_name, mtime):
	""" if os.path.getmtime(full_name) > mtime:
		yield ResourceModified(full_name)
	""" # static checks like this are compiled into the top of include trees
	return If(test=_compareMtime(full_name, mtime), body=[
		Expr(value=Yield(value=Call(func=Name(id='ResourceModified', ctx=Load(), lineno=0), 
			args=[Str(s=full_name)], keywords=[], starargs=None, kwargs=None)))
	], orelse=[])
def _yieldall(body):
	for i in range(len(body)):
		expr = body[i]
		if type(expr) is Expr:
			if type(expr.value) != Yield and not (type(expr.value) is Call and type(expr.value.func) is Name and expr.value.func.id is 'include'):
				new = Yield(value=expr.value)
				body[i].value = ast.copy_location(new, expr.value)

# the absolute bare minimum idea of a DOM node
# a structure used for building a tree and dumping a string
class Node:
	def __init__(self, tagName):
		self.tagName = tagName
		self.parentNode = None
		self.id = None
		self.className = None
		self.attrs = {}
		self.childNodes = []
	def setAttribute(self, k, v):
		self.attrs[k] = v
	def appendChild(self, n):
		self.childNodes.append(n)
		n.parentNode = self
		return n
	def __str__(self):
		return "<%(tagName)s%(id)s%(cls)s%(attrs)s>%(children)s</%(tagName)s>" % {
			'tagName': self.tagName,
			'id':' id="%s"' % self.id if self.id else "",
			'cls':' class="%s"' % self.className if self.className else "",
			'attrs':"".join([' %s="%s"' % (k,v) for k,v in self.attrs.items()]),
			'children':"".join([str(child) for child in self.childNodes]),
		}
	def __repr__(self):
		return str(self)

class TextNode:
	def __init__(self, text):
		self.text = text
	def __str__(self):
		return self.text
	def __repr__(self):
		return self.text

_synth_cache = {}
def synth(expr):
	""" A state-machine parser for generating Nodes from CSS expressions. 

		>>> synth("div#foo")
		[<div id="foo"></div>]

		>>> synth("div.bar")
		[<div class="bar"></div>]

		>>> synth("a[href=#home]")
		[<a href="#home"></a>]

		>>> synth("a[href=#home] 'Home Link'")
		[<a href="#home">Home Link</a>]

		>>> synth("div p span a[href=#home] 'Home Link' + a[href=#logout] 'Logout Link'")
		[<div><p><span><a href="#home">Home Link</a><a href="#logout">Logout Link</a></span></p></div>]

		>>> synth("div p span 'Here' + + p span 'There'")
		[<div><p><span>Here</span></p><p><span>There</span></p></div>]

		>>> synth("div, span")
		[<div></div>, <span></span>]

		>>> synth('div#id1.class1[a=b][k=v], div#id2.class2[href="home, on the range"] "some inner, text" span "span, text" + sub "sub text"')
		[<div id="id1" class="class1" a="b" k="v"></div>, <div id="id2" class="class2" href=""home, on the range"">some inner, text<span>span, text</span><sub>sub text</sub></div>]

		>>> synth("div#id1.class1[a=b][k=v], div#id2.class2[href='home, on the range'] 'some inner, text' span 'span, text' + sub 'sub text'")
		[<div id="id1" class="class1" a="b" k="v"></div>, <div id="id2" class="class2" href="'home, on the range'">some inner, text<span>span, text</span><sub>sub text</sub></div>]

		>>> synth("div#%(id)s")
		[<div id="%(id)s"></div>]

		>>> synth("div#%(id)s.%(cls)s[%(k)s=%(v)s] '%(data)s'")
		[<div id="%(id)s" class="%(cls)s" %(k)s="%(v)s">%(data)s</div>]

	"""
	# check the cache first
	ret = _synth_cache.get(expr, [])
	if len(ret) > 0:
		return ret
	# the buffers to store characters in
	tagname, id, cls, attr, val, text = [io.StringIO() for _ in range(6)]
	qmode = None # one of: None, ", or '; represents what the text element is opened/closed with
	attrs = {}
	parent = None
	target = tagname
	# feed each character to the machine
	for c in expr:
		# print(c, target, parent)
		if c == '+' and target in (tagname,):
			if parent is not None:
				parent = parent.parentNode
		elif c == '#' and target in (tagname,cls,attr):
			target = id
		elif c == '.' and target in (tagname,id,attr):
			target = cls
		elif c == '[' and target in (tagname,id,cls,attr):
			target = attr
		elif c == '=' and target in (attr,):
			target = val
		elif c == ']' and target in (attr, val):
			attrs[attr.getvalue()] = val.getvalue()
			[(x.truncate(0) | x.seek(0,2)) for x in (attr, val)]
			target = tagname
		elif c in ('"',"'") and target in (tagname,):
			target = text
			qmode = c
		elif c == qmode and target in (text,):
			node = TextNode(text.getvalue())
			if parent is not None:
				parent.appendChild(node)
			else:
				ret.append(node)
			target = tagname
			text.truncate(0)
			text.seek(0,2)
			qmode = None
		elif c in (' ', ',') and target not in (val, text) and tagname.tell() > 0:
			node = Node(tagname.getvalue())
			node.id = id.getvalue()
			node.className = cls.getvalue()
			node.attrs = attrs
			if parent is not None:
				parent.appendChild(node)
			else:
				ret.append(node)
			if c == ',':
				parent = None
			elif c == ' ':
				parent = node
			[(x.truncate(0) | x.seek(0,2)) \
				for x in (tagname, id, cls, attr, val, text)]
			attrs = {}
			target = tagname
		elif target == tagname:
			if c != ' ':
				tagname.write(c)
		elif target in (id, cls, attr, val, text):
			target.write(c)
		else:
			raise ParseError("Undefined input/state: '%s'/%s" % (c,target))
	if tagname.tell() > 0:
		node = Node(tagname.getvalue())
		node.id = id.getvalue()
		node.className = cls.getvalue()
		node.attrs = attrs
		if parent is not None:
			parent.appendChild(node)
		else:
			ret.append(node)
	if text.tell() > 0:
		node = TextNode(text.getvalue())
		if parent is not None:
			parent.appendChild(node)
		else:
			ret.append(node)
	if len(ret) == 1:
		return str(ret[0])
	return [str(x) for x in ret]


if __name__ == "__main__":
	import doctest
	doctest.testmod(raise_on_error=False)
